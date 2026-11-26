"""Loopback-only endpoints for importing from the Heiyao website."""
from functools import wraps
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from .heiyao_import import HeiyaoError, Site
from .runtime_storage import lora_root


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


def register_website_routes(routes, package, state, library, head_library, folder_paths):
    root = lora_root(folder_paths)
    if root is None:
        return False
    site = Site(root, state, index_path=package / "data" / "heiyao_verified_weights.json")

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

    @routes.post("/personal-lora/site/import")
    @guarded
    async def import_role(request):
        data = await body(request)
        resource_id = data.get("id")
        if not isinstance(resource_id, str):
            raise HeiyaoError("请选择官网 LoRA")
        name, resource, reused = await site.import_weight(
            resource_id, data.get("session"), allow_download=data.get("download") is True,
            renew_ticket=data.get("renewTicket") is True,
        )
        mapped = folder_paths.get_full_path("loras", name)
        if not mapped or Path(mapped).resolve() != (root / name).resolve():
            raise HeiyaoError("ComfyUI 还未从主 LoRA 目录加载该权重", 409)
        if resource.prompt:
            library.remember_import(name, resource.prompt)
        if resource.head_prompt:
            head_library.remember_import(name, resource.head_prompt)
        return {"name": name, "prompt": resource.prompt, "head_prompt": resource.head_prompt, "reused": reused}

    return True
