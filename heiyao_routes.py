"""Loopback-only endpoints for importing from the Heiyao website."""
from functools import partial, wraps
import ipaddress
from urllib.parse import urlsplit

from aiohttp import web

from .heiyao_import import HeiyaoError, Site, WEIGHT_INDEX_URL
from .heiyao_versions import inspect_slot
from .prompt_stack import lora_key
from .runtime_storage import lora_catalog, resolve_lora


def assert_loopback(request):
    try:
        address = ipaddress.ip_address(request.remote)
        host = urlsplit("//" + request.host)
        origin_ok = request.headers.get("Origin") == f"{request.scheme}://{request.host}"
        if (address.is_loopback and host.hostname in {"localhost", "127.0.0.1", "::1"}
                and not host.username and (request.method == "GET" or origin_ok)):
            return
    except (TypeError, ValueError):
        pass
    raise HeiyaoError("请从本机 localhost 或 127.0.0.1 打开 ComfyUI 使用官网导入", 403)


def register_website_routes(routes, package, state, library, head_library, root, extensions):
    if root is None:
        return False
    site = Site(root, state, index_path=package / "data" / "heiyao_verified_weights.json",
                local_loras=partial(lora_catalog, root, extensions), index_url=WEIGHT_INDEX_URL)

    def guarded(handler):
        @wraps(handler)
        async def run(request):
            try:
                assert_loopback(request)
                data = await handler(request)
                return web.json_response(data, headers={"Cache-Control": "no-store"})
            except HeiyaoError as error:
                return web.json_response({"message": str(error), "code": error.code}, status=error.status)
            except (ValueError, TypeError):
                return web.json_response({"message": "官网导入参数或响应格式异常"}, status=400)
            except OSError:
                return web.json_response({"message": "无法读写 ComfyUI 的 LoRA 文件"}, status=500)
        return run

    async def body(request):
        data = await request.json()
        if not isinstance(data, dict):
            raise HeiyaoError("官网导入参数格式错误")
        return data

    @routes.get("/personal-lora/site/search")
    @guarded
    async def search(request):
        return {"items": await site.search(request.query.get("q", "").strip())}

    @routes.get("/personal-lora/site/role/{resource_id}")
    @guarded
    async def detail(request):
        return await site.detail(request.match_info["resource_id"])

    @routes.post("/personal-lora/site/login")
    @guarded
    async def login(request):
        data = await body(request)
        return {"session": await site.login(data.get("username"), data.get("password"))}

    @routes.post("/personal-lora/site/membership")
    @guarded
    async def membership(request):
        return await site.membership((await body(request)).get("session"))

    @routes.post("/personal-lora/site/logout")
    @guarded
    async def logout(request):
        await site.logout((await body(request)).get("session"))
        return {"ok": True}

    async def import_model(data, versioned=False):
        resource_id = data.get("id")
        if not isinstance(resource_id, str):
            raise HeiyaoError("请选择官网 LoRA")
        name, resource, reused = await site.import_weight(
            resource_id, data.get("session"), allow_download=data.get("download") is True,
            renew_ticket=data.get("renewTicket") is True,
            versioned=versioned,
        )
        if resolve_lora(root, name, extensions) is None:
            raise HeiyaoError("ComfyUI 还未登记该权重路径", 409)
        if resource.prompt:
            library.remember_import(name, resource.prompt)
        if resource.head_prompt:
            head_library.remember_import(name, resource.head_prompt)
        return {"name": name, "prompt": resource.prompt, "head_prompt": resource.head_prompt, "reused": reused}

    @routes.post("/personal-lora/site/import")
    @guarded
    async def import_role(request):
        return await import_model(await body(request))

    async def slot_info(raw_name):
        name = lora_key(raw_name)
        if not name:
            raise HeiyaoError("请先选择一个 LoRA")
        return await inspect_slot(site, name, resolve_lora(root, name, extensions))

    @routes.get("/personal-lora/site/update")
    @guarded
    async def check_update(request):
        return await slot_info(request.query.get("name"))

    @routes.post("/personal-lora/site/update")
    @guarded
    async def download_update(request):
        data = await body(request)
        info = await slot_info(data.get("name"))
        latest = info.get("latest")
        if info["state"] != "update" or not latest["available"] or latest["id"] != data.get("id"):
            raise HeiyaoError("可用版本已变化，请重新检查官网新版", 409)
        custom = {}
        if data.get("switch") is True:
            current = info["current"]
            source = None
            if site.index.get(current["id"], {}).get("version") == current["version"]:
                try:
                    source = await site.resource(current["id"])
                except HeiyaoError:
                    pass
            custom = {"short": library.custom(current["name"], source.prompt if source else None),
                      "head": head_library.custom(current["name"], source.head_prompt if source else None)}
        imported = await import_model(data, versioned=True)
        return {**imported, "custom": custom}

    return True
