"""ComfyUI entry point for the Personal LoRA Loader."""
import json
from pathlib import Path

from aiohttp import web
import folder_paths
from nodes import LoraLoader
from server import PromptServer

from .heiyao_routes import register_website_routes
from .runtime_storage import prepare_state
from .prompt_stack import Library, compose, decode_slots, lora_key, text_field


PACKAGE = Path(__file__).resolve().parent
with (PACKAGE / "data" / "examples.json").open(encoding="utf-8") as source:
    EXAMPLES = json.load(source)
STATE = prepare_state(PACKAGE, folder_paths)
LIBRARY = Library(STATE / "triggers.json", EXAMPLES)
NODE_ID = "PersonalLoRALoader"


def catalog():
    return {lora_key(filename): filename for filename in folder_paths.get_filename_list("loras")}


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
    DESCRIPTION = "按槽位加载 LoRA，并把各个启用的完整触发提示词加到场景文字前面。"

    @classmethod
    def IS_CHANGED(cls, **_):
        return json.dumps(LIBRARY.read(), sort_keys=True, ensure_ascii=False)

    def apply(self, model, scene_prompt, slots, clip=None):
        rows = decode_slots(slots)
        defaults = LIBRARY.read()
        names = catalog()
        loader = LoraLoader()
        for index, row in enumerate(rows, 1):
            if not row.active:
                continue
            if row.name not in names:
                raise ValueError(f"第 {index} 槽找不到 LoRA：{row.name}")
            clip_strength = row.strength if clip is not None else 0.0
            model, clip = loader.load_lora(model, clip, names[row.name], row.strength, clip_strength)
        text = compose(rows, scene_prompt, defaults)
        return {"ui": {"positive_prompt": [text]}, "result": (model, clip, text)}


routes = PromptServer.instance.routes
WEBSITE_IMPORT = register_website_routes(routes, PACKAGE, STATE, LIBRARY, folder_paths)


@routes.get("/personal-lora/catalog")
async def get_catalog(request):
    return web.json_response({"loras": list(catalog()), "defaults": LIBRARY.read(), "dynamic_slots": True, "website_import": WEBSITE_IMPORT,
                              "website_membership": WEBSITE_IMPORT})


@routes.put("/personal-lora/default")
async def set_default(request):
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="需要 application/json")
    try:
        body = await request.json()
        name = lora_key(body["name"])
        if name not in catalog():
            raise ValueError("LoRA 已不在本机模型列表里")
        return web.json_response({"defaults": LIBRARY.save(name, body["prompt"])})
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
    return web.json_response({"positive_prompt": compose(rows, scene, LIBRARY.read())})


NODE_CLASS_MAPPINGS = {NODE_ID: PersonalLoRALoader}
NODE_DISPLAY_NAME_MAPPINGS = {NODE_ID: "个人多 LoRA 加载器"}
WEB_DIRECTORY = "./web"
