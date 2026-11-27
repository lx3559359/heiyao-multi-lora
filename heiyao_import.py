"""Read the Heiyao catalog and import checksum-pinned website LoRAs."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from secrets import token_urlsafe
from tempfile import NamedTemporaryFile
from time import monotonic
from urllib.parse import urlencode, urlsplit
from uuid import uuid4
from weakref import WeakValueDictionary
from zipfile import BadZipFile, ZipFile
from zlib import error as ZlibError

from aiohttp import ClientError, ClientSession, ClientTimeout, DummyCookieJar


SITE = "https://openheiyao.top"
CATALOG = "/v2/curated-resources"
WEIGHT_INDEX_URL = "https://raw.githubusercontent.com/lx3559359/heiyao-multi-lora/main/data/heiyao_verified_weights.json"
UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
VERSION = r"(?<![A-Za-z0-9])v\d{1,5}(?!\d)"
CHUNK_SIZE = 1024 * 1024
MAX_DOWNLOADS = 2
ZIP_POLL_INTERVAL = 5
ZIP_WAIT_LIMIT = 30 * 60
INDEX_REFRESH_INTERVAL = 60
WEIGHT_NAME = re.compile(r'[^\\/:*?"<>|\x00-\x1f]+\.safetensors', re.I)
TOKEN = re.compile(r"[A-Za-z0-9._~-]{1,4096}")
SEARCH_ALIASES = {"黑曜古风真人": "黑曜真人古风", "古风真人": "真人古风"}
FULL_SECTION = re.compile(r"^\s*完整触发提示词(?:\s*[\(（【][^\)）】]*[\)）】])?\s*[:：]\s*(.*)$")
SHORT_SECTION = re.compile(r"^\s*简短触发词(?:\s*[\(（【][^\)）】]*[\)）】])?\s*(?:[:：]\s*(.*))?$")
HEAD_SECTION = re.compile(r"^\s*完整头部特征触发词(?:[^:：]*[:：]\s*(.*)|\s*(?:[（(【][^）)】]*[）)】])?)$")
OTHER_SECTION = re.compile(r"^[\u3400-\u9fff][^:：]{0,30}[:：]")
LIVE_PIN_HEADING = re.compile(r"^\s*官网导入校验\s*[:：]")
LIVE_PIN_FIELDS = re.compile(r"\s*(v\d{1,5}|官网发布版)\s*\|\s*([1-9]\d{0,17})\s*\|\s*([0-9a-f]{64})\s*\|\s*(.+?)\s*")
MESSAGES = {
    "ACCOUNT_INVALID_CREDENTIALS": "官网账号或密码不正确",
    "DOWNLOAD_MEMBERSHIP_REQUIRED": "官网账号需要具备此 LoRA 的会员下载权益",
    "DOWNLOAD_QUOTA_EXHAUSTED": "当前官网账号的 LoRA 下载额度已用完",
}


class HeiyaoError(ValueError):
    def __init__(self, message, status=400, code="IMPORT_ERROR"):
        super().__init__(message)
        self.status = status
        self.code = code


def lookup_id(value):
    if re.fullmatch(UUID, value):
        return value
    url = urlsplit(value)
    match = re.fullmatch(r"lora/(" + UUID + r")", url.fragment)
    if url.scheme == "https" and url.netloc == "openheiyao.top" and url.path in ("", "/") and match:
        return match[1]
    return None


def section_prompt(summary, section, *, single_line=False):
    lines = summary.splitlines()
    for number, line in enumerate(lines):
        match = section.match(line)
        if not match:
            continue
        first = (match[1] or "").strip()
        if single_line and first:
            return first
        parts = [first] if first else []
        for next_line in lines[number + 1:]:
            value = next_line.strip()
            if (OTHER_SECTION.match(value) or SHORT_SECTION.fullmatch(value) or HEAD_SECTION.fullmatch(value)
                    or (parts and not value)):
                break
            if value:
                if single_line or match[1] is None:
                    return value
                parts.append(value)
        if parts:
            return "\n".join(parts)
    return ""


def full_prompt(summary):
    prompt = section_prompt(summary, FULL_SECTION)
    if not prompt:
        raise HeiyaoError("官网详情没有可识别的完整触发提示词")
    return prompt


def website_prompt(summary):
    short = section_prompt(summary, SHORT_SECTION, single_line=True)
    if short:
        return short
    try:
        return full_prompt(summary)
    except HeiyaoError:
        if re.search(r"(?:^|[，,。；;])\s*(?:未记录固定触发词|无需固定触发词|无固定触发词)(?:[。.!！]|$)", summary):
            return ""
        raise


def website_head_prompt(summary):
    return section_prompt(summary, HEAD_SECTION) or website_prompt(summary)


def website_weight_pin(title, summary):
    lines = [LIVE_PIN_HEADING.sub("", line, count=1) for line in summary.splitlines() if LIVE_PIN_HEADING.match(line)]
    if not lines:
        return None
    match = LIVE_PIN_FIELDS.fullmatch(lines[0]) if len(lines) == 1 else None
    if match is None or not WEIGHT_NAME.fullmatch(match[4]):
        raise HeiyaoError("官网权重校验信息格式无效", 409)
    version, size, sha256, name = match.groups()
    titled_version = re.search(VERSION, title, re.I)
    if titled_version and titled_version[0].lower() != version:
        raise HeiyaoError("官网条目版本与已校验的权重登记不一致", 409)
    return {"title": title, "version": version, "name": name, "bytes": int(size), "sha256": sha256}


def membership_view(data):
    month = re.match(r"^\d{4}-(?:0[1-9]|1[0-2])(?=-|$)", str(data.get("monthStart", "")))
    active = data.get("active")
    tier = (data.get("membership") or {}).get("tier")
    lora = data.get("lora") or {}
    used, remaining = lora.get("used"), lora.get("remaining")
    unlimited = active is True and tier == "LIFETIME"
    if (not month or type(active) is not bool or type(used) is not int or used < 0
            or (not unlimited and (type(remaining) is not int or remaining < 0))):
        raise HeiyaoError("官网返回的 LoRA 额度格式无效", 502)
    return {"month": month[0], "active": active, "tier": tier,
            "used": used, "remaining": None if unlimited else remaining, "unlimited": unlimited}


def safe_ticket(resource_id, path, *, bundle_id=None):
    directory = f"download-files/{bundle_id}" if bundle_id else "private-download"
    prefix = f"{CATALOG}/{resource_id}/{directory}/file?ticket="
    if not isinstance(path, str) or not re.fullmatch(re.escape(prefix) + r"\d{1,12}\.[A-Za-z0-9_-]{43}", path):
        raise HeiyaoError("官网返回的下载授权无效", 502)
    return path


@dataclass(frozen=True)
class Resource:
    id: str
    title: str
    prompt: str
    version: str
    name: str
    size: int
    sha256: str
    head_prompt: str = ""

    def destination(self, root, versioned=False):
        folder = root / "黑曜官网" / self.id
        if versioned:
            folder /= f"{self.version}-{self.sha256[:12]}"
        return folder / self.name

    def weight_names(self):
        unsuffixed = re.sub(r"_" + re.escape(self.version) + r"(?=\.safetensors$)", "", self.name)
        return self.name, unsuffixed


def verify_weight(path, resource):
    try:
        if path.stat().st_size != resource.size or not path.is_file():
            return False
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == resource.sha256
    except FileNotFoundError:
        return False


def fingerprint(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def extract_weight(archive, pending, resource):
    try:
        with ZipFile(archive) as bundle:
            weights = [file for file in bundle.infolist() if not file.is_dir()
                       and PurePosixPath(file.filename).name in resource.weight_names()]
            if len(weights) != 1 or weights[0].file_size != resource.size:
                raise HeiyaoError("官网 ZIP 未包含唯一且大小匹配的登记权重", 409)
            digest = hashlib.sha256()
            with bundle.open(weights[0]) as source, pending.open("wb") as output:
                while part := source.read(CHUNK_SIZE):
                    digest.update(part)
                    output.write(part)
            return digest.hexdigest()
    except (BadZipFile, NotImplementedError, RuntimeError, ZlibError) as error:
        raise HeiyaoError("官网 ZIP 无法读取或校验未通过", 502) from error


def matching_weight(root, resource):
    versioned, unversioned = resource.weight_names()
    candidates = (root / versioned, root / unversioned, resource.destination(root),
                  resource.destination(root, versioned=True))
    for path in dict.fromkeys(candidates):
        if path.resolve().is_relative_to(root) and verify_weight(path, resource):
            return path
    return None


def website_token(body, expiry_key):
    token = body.get("accessToken")
    try:
        expiry = datetime.fromisoformat(body.get(expiry_key, "")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        expiry = datetime.min.replace(tzinfo=timezone.utc)
    if not isinstance(token, str) or not TOKEN.fullmatch(token) or expiry <= datetime.now(timezone.utc):
        raise HeiyaoError("官网登录或访客会话响应无效", 502)
    return token, expiry


class Site:
    def __init__(self, root, data_dir, *, index_path=None, local_loras=None, index_url=None):
        self.root = root.resolve()
        self.local_loras = local_loras
        self.index_url = index_url
        self.data_dir = data_dir
        index = index_path if index_path is not None else data_dir / "heiyao_verified_weights.json"
        self.bundled_index = json.loads(index.read_text(encoding="utf-8"))
        self.index = self.bundled_index
        self.index_refreshed_at = None
        self.visitor = None
        self.refresh = None
        self.accounts = {}
        # A reserved key with no path means issuance was ambiguous or the file ticket stopped working.
        self.download_tickets = {}
        self.identity = None
        self._session = None
        self.visitor_lock = asyncio.Lock()
        self.import_locks = WeakValueDictionary()
        self.download_slots = asyncio.Semaphore(MAX_DOWNLOADS)
        self.ticket_lock = asyncio.Lock()
        self.compare_lock = asyncio.Lock()
        self.local_fingerprints = {}
        self.website_pins = {}

    def http(self):
        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=ClientTimeout(total=18), cookie_jar=DummyCookieJar())
        return self._session

    async def close(self, _app=None):
        if self._session is not None:
            await self._session.close()

    async def refresh_index(self):
        if self.index_url is None:
            return False
        updated = self.index_refreshed_at
        if updated is not None and monotonic() - updated < INDEX_REFRESH_INTERVAL:
            return True
        try:
            async with ClientSession(timeout=ClientTimeout(total=8)) as session:
                async with session.get(self.index_url, allow_redirects=False) as response:
                    if response.status != 200:
                        return False
                    payload = bytearray()
                    async for part in response.content.iter_chunked(65536):
                        payload.extend(part)
                        if len(payload) > 2 * 1024 * 1024:
                            return False
            records = json.loads(payload)
        except (ClientError, asyncio.TimeoutError, UnicodeError, ValueError):
            return False
        if not isinstance(records, dict) or not records:
            return False
        for resource_id, row in records.items():
            if (not re.fullmatch(UUID, resource_id) or not isinstance(row, dict)
                    or not isinstance(row.get("version"), str)
                    or (row["version"] != "官网发布版" and not re.fullmatch(r"v\d{1,5}", row["version"]))
                    or not isinstance(row.get("name"), str) or not WEIGHT_NAME.fullmatch(row["name"])
                    or type(row.get("bytes")) is not int or row["bytes"] <= 0
                    or not isinstance(row.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                    or any(not isinstance(row.get(field, ""), str) for field in ("title", "catalog_title"))):
                return False
        self.index = self.bundled_index | records
        self.index_refreshed_at = monotonic()
        return True

    async def json(self, path, token=None, payload=None, *, post=False):
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with self.http().request("POST" if post or payload is not None else "GET", SITE + path,
                                           headers=headers, json=payload, allow_redirects=False) as response:
                try:
                    body = await response.json(content_type=None)
                except ValueError:
                    body = {}
                if not isinstance(body, dict):
                    body = {}
                if response.status == 204 and path == "/v1/accounts/logout":
                    return {}
                if not 200 <= response.status < 300 or not body:
                    code = body.get("code")
                    if not isinstance(code, str) or not re.fullmatch(r"[A-Z_]{1,60}", code):
                        code = "SITE_RESPONSE_ERROR"
                    status = response.status if response.status in (400, 401, 403, 404, 409, 429) else 502
                    raise HeiyaoError(MESSAGES.get(code, f"官网请求失败（{response.status}）"), status, code)
                return body
        except (ClientError, asyncio.TimeoutError) as error:
            raise HeiyaoError("暂时无法连接黑曜官网", 502) from error

    def device(self):
        if self.identity is not None:
            return self.identity
        path = self.data_dir / "heiyao_device.json"
        try:
            identity = json.loads(path.read_text(encoding="utf-8"))
            if not re.fullmatch(UUID, identity["deviceId"]) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", identity["deviceCredential"]):
                raise ValueError
        except FileNotFoundError:
            identity = {"deviceId": str(uuid4()), "deviceCredential": token_urlsafe(32)}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(identity), encoding="utf-8")
        except (KeyError, TypeError, ValueError) as error:
            raise HeiyaoError("本地官网设备配置无法读取", 500) from error
        self.identity = identity
        return identity

    async def visitor_token(self, rejected=None):
        async with self.visitor_lock:
            if self.visitor and self.visitor[0] != rejected and self.visitor[1] > datetime.now(timezone.utc):
                return self.visitor[0]
            device = self.device()
            body = None
            if self.refresh:
                try:
                    body = await self.json("/v1/auth/token/refresh", payload={
                        "deviceId": device["deviceId"], "refreshToken": self.refresh,
                    })
                except HeiyaoError as error:
                    if error.status not in (400, 401, 403):
                        raise
            if body is None:
                body = await self.json("/v1/device-sessions/register", payload={
                    **device, "installChannel": "web-gallery", "appVersion": "0.1.0",
                })
            self.visitor = website_token(body, "accessTokenExpiresAt")
            self.refresh = body.get("refreshToken")
            return self.visitor[0]

    async def read(self, path):
        token = await self.visitor_token()
        try:
            return await self.json(path, token)
        except HeiyaoError as error:
            if error.status != 401:
                raise
        return await self.json(path, await self.visitor_token(rejected=token))

    async def login(self, username, password):
        if (not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,31}", username)
                or not isinstance(password, str) or not 10 <= len(password) <= 128):
            raise HeiyaoError("请填写有效的官网账号和密码")
        body = await self.json("/v1/accounts/login", await self.visitor_token(),
                               {"username": username, "password": password})
        account = website_token(body, "expiresAt")
        self.accounts = {key: value for key, value in self.accounts.items()
                         if value[1] > datetime.now(timezone.utc)}
        self.download_tickets = {key: value for key, value in self.download_tickets.items()
                                 if key[0] in self.accounts}
        handle = token_urlsafe(32)
        self.accounts[handle] = account
        return handle

    async def logout(self, handle):
        account = self.accounts.pop(handle, None) if isinstance(handle, str) else None
        if account:
            self.download_tickets = {key: value for key, value in self.download_tickets.items() if key[0] != handle}
            try:
                await self.json("/v1/accounts/logout", account[0], post=True)
            except HeiyaoError:
                pass

    async def search(self, query):
        query = SEARCH_ALIASES.get(query, query)
        exact = lookup_id(query)
        if exact:
            items = [await self.read(f"{CATALOG}/{exact}")]
        else:
            if "://" in query or len(query) > 120 or any(ord(char) < 32 for char in query):
                raise HeiyaoError("请填写角色名或黑曜官网 LoRA 详情链接")
            items = await self.search_pages(query)
        results = {item["id"]: {"id": item["id"], "title": item["title"], "updatedAt": item.get("updatedAt")} for item in items
                   if isinstance(item, dict) and "LoRA" in item.get("tags", [])
                   and isinstance(item.get("id"), str) and re.fullmatch(UUID, item["id"])
                   and isinstance(item.get("title"), str)}
        return sorted(results.values(), key=lambda item: query.casefold() not in item["title"].casefold())

    async def search_pages(self, query):
        params = {"tag": "LoRA", "limit": 50}
        if query:
            params["query"] = query
        items = []
        seen = set()
        while True:
            page = await self.read(CATALOG + "?" + urlencode(params))
            items.extend(page.get("items", []))
            cursor = page.get("nextCursor")
            if not cursor:
                return items
            if not isinstance(cursor, str) or cursor in seen:
                raise HeiyaoError("官网目录分页响应异常，请稍后重试", 502)
            seen.add(cursor)
            params["cursor"] = cursor

    @staticmethod
    def title_matches(record, title):
        version = re.search(VERSION, title, re.I)
        expected = record.get("title")
        return title == expected if expected else bool(version and version[0].lower() == record["version"])

    def compare_weights(self, items, pins):
        verified, unverified = [], []
        for item in items:
            record = pins.get(item["id"])
            if record and self.title_matches(record, item["title"]):
                verified.append((item, record))
            else:
                unverified.append(item)

        sizes = {record["bytes"] for _, record in verified}
        scanned = {}
        files = self.local_loras()
        fingerprints = set()
        for name, path in files.items():
            try:
                stat = path.stat()
                if stat.st_size not in sizes:
                    continue
                signature = fingerprint(stat)
                cached = self.local_fingerprints.get(name)
                if cached and cached[0] == signature:
                    digest = cached[1]
                else:
                    with path.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                scanned[name] = (signature, digest)
                fingerprints.add((stat.st_size, digest))
            except FileNotFoundError:
                continue
        self.local_fingerprints = scanned
        missing = [
            {**item, "version": record["version"], "bytes": record["bytes"], "sha256": record["sha256"]}
            for item, record in verified
            if (record["bytes"], record["sha256"]) not in fingerprints
        ]
        return {
            "local_count": len(files), "website_count": len(items),
            "matched_count": len(verified) - len(missing),
            "missing": missing, "unverified": unverified,
        }

    async def compare_library(self):
        async with self.compare_lock:
            items, _ = await asyncio.gather(self.search(""), self.refresh_index())
            pins = await self.catalog_weights(items)
            return await asyncio.to_thread(self.compare_weights, items, pins)

    async def catalog_weights(self, items):
        limit = asyncio.Semaphore(6)

        async def update(item):
            resource_id, title = item["id"], item["title"]
            revision = item.get("updatedAt")
            cache = self.website_pins.get(resource_id)
            if cache and revision and cache[:2] == (revision, title):
                return resource_id, cache[2]
            known = self.index.get(resource_id)
            if cache is None and known and self.title_matches(known, title):
                return resource_id, known
            try:
                async with limit:
                    detail = await self.read(f"{CATALOG}/{resource_id}")
                if detail.get("id") == resource_id and detail.get("title") == title and "LoRA" in detail.get("tags", []):
                    pin = website_weight_pin(title, detail.get("summary") or "")
                    if pin:
                        self.website_pins[resource_id] = (revision, title, pin)
                        return resource_id, pin
            except HeiyaoError:
                pass
            return resource_id, None

        rows = await asyncio.gather(*(update(item) for item in items))
        return {resource_id: pin for resource_id, pin in rows if pin is not None}

    async def resource(self, resource_id):
        if not re.fullmatch(UUID, resource_id):
            raise HeiyaoError("LoRA 资源 ID 格式不正确")
        detail = await self.read(f"{CATALOG}/{resource_id}")
        title = detail.get("title", "")
        if not isinstance(title, str):
            raise HeiyaoError("官网详情没有有效的 LoRA 标题", 502)
        if detail.get("id") != resource_id or "LoRA" not in detail.get("tags", []):
            raise HeiyaoError("官网详情与所选 LoRA 不一致", 409)
        summary = detail.get("summary") or ""
        record = website_weight_pin(title, summary)
        if record is None:
            record = self.index.get(resource_id)
            if (not record or not self.title_matches(record, title)) and await self.refresh_index():
                record = self.index.get(resource_id)
            if not record:
                raise HeiyaoError("官网还未发布可校验的权重登记", 409)
            if not self.title_matches(record, title):
                raise HeiyaoError("官网条目版本与已校验的权重登记不一致", 409)
        prompt = website_prompt(summary)
        name = record["name"]
        if not WEIGHT_NAME.fullmatch(name):
            raise HeiyaoError("权重文件名无效", 409)
        return Resource(resource_id, title, prompt, record["version"], name, record["bytes"], record["sha256"],
                        website_head_prompt(summary))

    def local_weight(self, resource):
        if self.local_loras is not None:
            names = resource.weight_names()
            scanned = getattr(self, "local_fingerprints", {})
            for name, path in self.local_loras().items():
                same_name = name.rsplit("/", 1)[-1] in names
                cached = scanned.get(name)
                if cached:
                    try:
                        if cached[0] == fingerprint(path.stat()):
                            if cached[1] == resource.sha256:
                                return name
                            continue
                    except FileNotFoundError:
                        continue
                if same_name and verify_weight(path, resource):
                    return name
            return None
        path = matching_weight(self.root, resource)
        return path.relative_to(self.root).as_posix() if path else None

    async def detail(self, resource_id):
        resource = await self.resource(resource_id)
        existing = await asyncio.to_thread(self.local_weight, resource)
        available = bool(existing)
        if not existing:
            available = (await self.read(f"{CATALOG}/{resource_id}/private-download/status")).get("available") is True
        return {"title": resource.title, "version": resource.version, "prompt": resource.prompt,
                "head_prompt": resource.head_prompt, "bytes": resource.size, "local": bool(existing), "available": available}

    def account(self, handle):
        account = self.accounts.get(handle) if isinstance(handle, str) else None
        if not account or account[1] <= datetime.now(timezone.utc):
            raise HeiyaoError("请先登录有下载权限的黑曜官网账号", 401, "LOGIN_REQUIRED")
        return account[0]

    async def membership(self, handle):
        token = self.account(handle)
        try:
            return membership_view(await self.json("/v1/accounts/download-membership", token))
        except HeiyaoError as error:
            if error.status == 401:
                raise HeiyaoError("官网登录已过期，请重新登录", 401, "LOGIN_REQUIRED") from error
            raise

    async def ticket(self, key, resource, bundle_id=None):
        if key not in self.download_tickets:
            self.download_tickets[key] = None
            try:
                endpoint = f"{CATALOG}/{resource.id}/private-download"
                if bundle_id:
                    endpoint = f"{CATALOG}/{resource.id}/download-files/{bundle_id}/ticket"
                started = asyncio.get_running_loop().time()
                while True:
                    async with self.ticket_lock:
                        result = await self.json(endpoint, self.account(key[0]), post=True)
                    if not bundle_id or result.get("status") != "PREPARING":
                        break
                    await asyncio.sleep(ZIP_POLL_INTERVAL)
                    if asyncio.get_running_loop().time() - started >= ZIP_WAIT_LIMIT:
                        raise HeiyaoError("官网 ZIP 仍在准备，请稍后再试", 409, "ZIP_PREPARING")
                self.download_tickets[key] = safe_ticket(resource.id, result.get("downloadPath"), bundle_id=bundle_id)
            except HeiyaoError as error:
                if error.status < 500:
                    self.download_tickets.pop(key, None)
                    if error.status == 401:
                        raise HeiyaoError("官网登录已过期，请重新登录", 401, "LOGIN_REQUIRED") from error
                    raise
                # A transport or server failure does not tell us whether the quota operation completed.
        path = self.download_tickets[key]
        if path is None:
            raise HeiyaoError("上次下载授权未确认或已不可用，请核对官网额度后再申请新票据",
                              409, "NEW_TICKET_REQUIRED")
        return path

    async def bundle(self, resource):
        try:
            listing = await self.read(f"{CATALOG}/{resource.id}/download-files")
        except HeiyaoError:
            return None
        bundle = listing.get("bundle")
        if (isinstance(bundle, dict) and bundle.get("purpose") == "download_package"
                and bundle.get("access") == "membership" and bundle.get("downloadAvailable") is True
                and re.fullmatch(UUID, str(bundle.get("fileId")))
                and type(bundle.get("sizeBytes")) is int and bundle["sizeBytes"] >= resource.size):
            return bundle
        return None

    async def get_weight(self, resource_id, handle, *, allow_download=False, renew_ticket=False):
        name, resource, reused = await self.import_weight(
            resource_id, handle, allow_download=allow_download, renew_ticket=renew_ticket,
        )
        return name, resource.prompt, reused

    async def import_weight(self, resource_id, handle, *, allow_download=False, renew_ticket=False,
                            versioned=False, expected_sha=None, progress=None):
        async with self.import_locks.setdefault(resource_id, asyncio.Lock()):
            resource = await self.resource(resource_id)
            if expected_sha is not None and resource.sha256 != expected_sha:
                raise HeiyaoError("官网权重登记已变化，请重新比对后下载", 409)
            existing = await asyncio.to_thread(self.local_weight, resource)
            if existing:
                return existing, resource, True
            if not allow_download:
                raise HeiyaoError("本地同版权重已不可用，请重新选择版本并确认下载", 409)
            destination = resource.destination(self.root, versioned)
            if not destination.resolve().is_relative_to(self.root) or destination.exists():
                raise HeiyaoError("本地同名权重已存在，请先核对文件版本", 409)
            self.account(handle)
            key = (handle, resource.id, resource.sha256)
            async with self.download_slots:
                try:
                    await self.transfer(key, resource, destination, renew_ticket, progress=progress)
                except HeiyaoError as error:
                    if error.code != "WEIGHT_RESPONSE_UNAVAILABLE":
                        raise
                    if progress is not None:
                        progress("zip_prepare")
                    bundle = await self.bundle(resource)
                    if bundle is None:
                        raise error
                    bundle_key = (*key, bundle["fileId"])
                    await self.transfer(bundle_key, resource, destination, renew_ticket, bundle, progress=progress)
                    self.download_tickets.pop(key, None)
            return destination.relative_to(self.root).as_posix(), resource, False

    async def transfer(self, key, resource, destination, renew_ticket, bundle=None, *, progress=None):
        if renew_ticket and self.download_tickets.get(key) is None:
            self.download_tickets.pop(key, None)
        path = await self.ticket(key, resource, bundle["fileId"] if bundle else None)
        try:
            await self.download(path, resource, destination, bundle=bundle, progress=progress)
        except HeiyaoError as error:
            if error.code == "NEW_TICKET_REQUIRED":
                self.download_tickets[key] = None
            raise
        self.download_tickets.pop(key, None)

    async def download(self, path, resource, destination, *, bundle=None, progress=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        length = 0
        digest = hashlib.sha256()
        pending = None
        archive = None
        # The ZIP listing gives total uncompressed bytes, so allow room for archive headers.
        maximum = bundle["sizeBytes"] + 8 * CHUNK_SIZE if bundle else resource.size
        try:
            async with self.http().get(SITE + path, headers={"Accept-Encoding": "identity"}, allow_redirects=False,
                                       timeout=ClientTimeout(total=900, sock_read=50)) as response:
                if response.status in (401, 403, 410):
                    raise HeiyaoError("原官网下载票据已不可用，请核对额度后再申请新票据",
                                      409, "NEW_TICKET_REQUIRED")
                encoded = response.headers.get("Content-Encoding", "identity").lower() != "identity"
                size = None if encoded else response.content_length
                if response.status != 200:
                    unavailable = response.status in (301, 302, 303, 307, 308, 404, 500, 502, 504)
                    code = "WEIGHT_RESPONSE_UNAVAILABLE" if not bundle and unavailable else "DOWNLOAD_RESPONSE_ERROR"
                    raise HeiyaoError(f"官网下载文件返回 HTTP {response.status}", 502, code)
                if not bundle and (response.content_type in ("application/zip", "text/html", "application/json")
                                   or size is not None and size != resource.size):
                    raise HeiyaoError("官网单文件响应与登记权重不符", 502, "WEIGHT_RESPONSE_UNAVAILABLE")
                if bundle and size is not None and size > maximum:
                    raise HeiyaoError("官网 ZIP 超过下载清单大小", 502)
                phase = "zip" if bundle else "file"
                total = size if bundle else resource.size
                if progress is not None:
                    progress(phase, 0, total)
                with NamedTemporaryFile(dir=destination.parent, suffix=".part", delete=False) as file:
                    pending = Path(file.name)
                    async for part in response.content.iter_chunked(CHUNK_SIZE):
                        length += len(part)
                        if length > maximum:
                            code = "IMPORT_ERROR" if bundle else "WEIGHT_RESPONSE_UNAVAILABLE"
                            raise HeiyaoError("官网下载文件超过登记大小", 502, code)
                        if not bundle:
                            digest.update(part)
                        file.write(part)
                        if progress is not None:
                            progress(phase, length, total)
            if progress is not None:
                progress("extract" if bundle else "verify")
            verified = digest.hexdigest()
            if bundle:
                archive, pending = pending, None
                with NamedTemporaryFile(dir=destination.parent, suffix=".part", delete=False) as file:
                    pending = Path(file.name)
                extraction = asyncio.create_task(asyncio.to_thread(extract_weight, archive, pending, resource))
                try:
                    verified = await asyncio.shield(extraction)
                except asyncio.CancelledError:
                    await asyncio.gather(extraction, return_exceptions=True)
                    raise
                length = pending.stat().st_size
            if length != resource.size or verified != resource.sha256:
                raise HeiyaoError("权重大小或 SHA-256 校验失败", 409)
            if destination.exists():
                raise HeiyaoError("导入位置已出现同名权重", 409)
            pending.rename(destination)
        except (ClientError, asyncio.TimeoutError) as error:
            raise HeiyaoError("下载中断，请检查网络后重试", 502) from error
        finally:
            if pending:
                pending.unlink(missing_ok=True)
            if archive:
                archive.unlink(missing_ok=True)
