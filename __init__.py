"""ComfyUI entry point for the Personal LoRA Loader."""
from inspect import signature
import json
from pathlib import Path

from aiohttp import web
import comfy.sd
import comfy.utils
import folder_paths
from server import PromptServer

from .heiyao_routes import register_website_routes
from .runtime_storage import lora_catalog, lora_root, prepare_state, resolve_lora
from .prompt_stack import Library, compose, decode_slots, lora_key, text_field


PACKAGE = Path(__file__).resolve().parent
with (PACKAGE / "data" / "examples.json").open(encoding="utf-8") as source:
    EXAMPLES = json.load(source)
with (PACKAGE / "data" / "heads.json").open(encoding="utf-8") as source:
    HEAD_EXAMPLES = json.load(source)
STATE = prepare_state(PACKAGE, folder_paths)
LIBRARY = Library(STATE / "triggers.json", EXAMPLES)
HEAD_LIBRARY = Library(STATE / "head_triggers.json", HEAD_EXAMPLES)
NODE_ID = "PersonalLoRALoader"
LORA_ROOT = lora_root(folder_paths)
LORA_EXTENSIONS = folder_paths.supported_pt_extensions
LORA_METADATA = ("return_metadata" in signature(comfy.utils.load_torch_file).parameters
                 and "lora_metadata" in signature(comfy.sd.load_lora_for_models).parameters)


def catalog():
    return lora_catalog(LORA_ROOT, LORA_EXTENSIONS)


class PersonalLoRALoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "scene_prompt": ("STRING", {"default": "", "multiline": True}),
                "slots": ("PERSONAL_LORA_SLOTS", {"default": "[{}, {}]"}),
            },
            "optional": {"clip": ("CLIP",)},
        }

    RETURN_TYPES = ("MODEL", "CLIP", "STRING")
    RETURN_NAMES = ("model", "clip", "positive_prompt")
    FUNCTION = "apply"
    CATEGORY = "loaders"
    DESCRIPTION = "按槽位加载 LoRA，并把选中的简短或头部特征触发提示词加到场景文字前面。"

    @classmethod
    def IS_CHANGED(cls, **_):
        return json.dumps((LIBRARY.read(), HEAD_LIBRARY.read()), sort_keys=True, ensure_ascii=False)

    def apply(self, model, scene_prompt, slots, clip=None):
        rows = decode_slots(slots)
        loaded_path = None
        for index, row in enumerate(rows, 1):
            if not row.active:
                continue
            path = resolve_lora(LORA_ROOT, row.name, LORA_EXTENSIONS)
            if path is None:
                raise ValueError(f"第 {index} 槽在 ComfyUI 的 models/loras 内找不到 LoRA：{row.name}")
            if path != loaded_path:
                if LORA_METADATA:
                    weights, metadata = comfy.utils.load_torch_file(str(path), safe_load=True, return_metadata=True)
                    options = {"lora_metadata": metadata}
                else:
                    weights = comfy.utils.load_torch_file(str(path), safe_load=True)
                    options = {}
                loaded_path = path
            clip_strength = row.strength if clip is not None else 0.0
            model, clip = comfy.sd.load_lora_for_models(model, clip, weights, row.strength, clip_strength, **options)
        text = compose(rows, scene_prompt, LIBRARY.read(), HEAD_LIBRARY.read())
        return {"ui": {"positive_prompt": [text]}, "result": (model, clip, text)}


routes = PromptServer.instance.routes
WEBSITE_IMPORT = register_website_routes(routes, PACKAGE, STATE, LIBRARY, HEAD_LIBRARY, LORA_ROOT, LORA_EXTENSIONS)


@routes.get("/personal-lora/catalog")
async def get_catalog(request):
    return web.json_response({"loras": list(catalog()), "defaults": LIBRARY.read(),
                              "head_defaults": HEAD_LIBRARY.read(), "dynamic_slots": True, "website_import": WEBSITE_IMPORT,
                              "website_membership": WEBSITE_IMPORT, "website_updates": WEBSITE_IMPORT,
                              "website_library": WEBSITE_IMPORT})


@routes.put("/personal-lora/default")
async def set_default(request):
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="需要 application/json")
    try:
        body = await request.json()
        name = lora_key(body["name"])
        if resolve_lora(LORA_ROOT, name, LORA_EXTENSIONS) is None:
            raise ValueError("LoRA 已不在 ComfyUI 的 models/loras 内")
        mode = body.get("mode", "short")
        if mode not in ("short", "head"):
            raise ValueError("触发词档位无效")
        library = HEAD_LIBRARY if mode == "head" else LIBRARY
        library.save(name, body["prompt"])
        return web.json_response({"defaults": LIBRARY.read(), "head_defaults": HEAD_LIBRARY.read()})
    except (KeyError, TypeError, ValueError) as error:
        raise web.HTTPBadRequest(text=str(error)) from error


@routes.post("/personal-lora/preview")
async def preview(request):
    try:
        body = await request.json()
        rows = decode_slots(body["slots"])
        scene = text_field(body["scene"])
    except (KeyError, TypeError, ValueError) as error:
        raise web.HTTPBadRequest(text=str(error)) from error
    return web.json_response({"positive_prompt": compose(rows, scene, LIBRARY.read(), HEAD_LIBRARY.read())})


NODE_CLASS_MAPPINGS = {NODE_ID: PersonalLoRALoader}
NODE_DISPLAY_NAME_MAPPINGS = {NODE_ID: "个人多 LoRA 加载器"}
WEB_DIRECTORY = "./web"
