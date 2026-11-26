import asyncio
import gzip
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from heiyao_import import HeiyaoError, Site

RESOURCE = "7fd546ab-1c65-4772-a56b-511a5c33f908"
SECOND_RESOURCE = "7fd546ab-1c65-4772-a56b-511a5c33f909"
FILENAME = "斗破苍穹_蝶_dpdievex.safetensors"
CHECKPOINT = b"small test checkpoint"
API = "/v2/curated-resources/" + RESOURCE
BUNDLE = API + "/download-files/00000000-0000-4000-8000-000000000000"


class WebsiteTransferTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd() / "temp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        data = self.root / "data"
        data.mkdir()
        record = {
            "name": FILENAME, "version": "v001", "bytes": len(CHECKPOINT),
            "sha256": hashlib.sha256(CHECKPOINT).hexdigest(),
        }
        second = {**record, "name": "second.safetensors", "sha256": hashlib.sha256(b"second").hexdigest(), "bytes": 6}
        (data / "heiyao_verified_weights.json").write_text(
            json.dumps({RESOURCE: record, SECOND_RESOURCE: second}), encoding="utf-8",
        )
        self.expires = "2099-01-01T00:00:00Z"
        self.ticket_requests = 0
        self.download_body = CHECKPOINT
        self.download_headers = {}
        self.download_status = 200
        self.ticket_status = 200
        self.ticket_error = "SITE_BUSY"
        self.download_requests = 0
        self.bundle_requests = 0
        self.bundle_body = b""
        self.bundle_preparations = 0
        self.transfer_connections = []
        self.first_started = asyncio.Event()
        self.release_first = None
        self.site = Site(self.root, data)
        app = web.Application()

        async def guest(request):
            return web.json_response({"accessToken": "visitor-token", "accessTokenExpiresAt": self.expires})

        async def detail(request):
            self.assertEqual(request.headers.get("Authorization"), "Bearer visitor-token")
            return web.json_response({"id": request.match_info["id"], "title": "角色 LoRA v001", "tags": ["LoRA"],
                                      "summary": "完整触发提示词：\nOne female character."})

        async def login(request):
            self.assertEqual(request.headers.get("Authorization"), "Bearer visitor-token")
            return web.json_response({"accessToken": "account-token", "expiresAt": self.expires})

        async def membership(request):
            self.assertEqual(request.headers.get("Authorization"), "Bearer account-token")
            return web.json_response({"monthStart": "2026-11-01", "membership": {"tier": "QUARTERLY"},
                                      "active": True, "lora": {"used": 4, "remaining": 1}})

        async def ticket(request):
            self.assertEqual(request.headers.get("Authorization"), "Bearer account-token")
            self.ticket_requests += 1
            self.transfer_connections.append(request.transport)
            if self.ticket_status != 200:
                return web.json_response({"code": self.ticket_error}, status=self.ticket_status)
            return web.json_response({"downloadPath": request.path + "/file?ticket=1." + "x" * 43})

        async def download(request):
            self.download_requests += 1
            self.assertNotIn("Authorization", request.headers)
            self.transfer_connections.append(request.transport)
            if request.match_info["id"] == SECOND_RESOURCE:
                return web.Response(body=b"second")
            if self.release_first:
                self.first_started.set()
                await self.release_first.wait()
            return web.Response(body=self.download_body, status=self.download_status, headers=self.download_headers)

        async def files(request):
            return web.json_response({"files": [], "groups": [], "bundle": {
                "fileId": BUNDLE.rsplit("/", 1)[-1], "purpose": "download_package", "access": "membership",
                "displayName": "release.zip", "sizeBytes": len(self.bundle_body), "downloadAvailable": bool(self.bundle_body),
            }})

        async def bundle_ticket(request):
            self.assertEqual(request.headers.get("Authorization"), "Bearer account-token")
            self.bundle_requests += 1
            if self.bundle_requests <= self.bundle_preparations:
                return web.json_response({"status": "PREPARING"}, status=202)
            return web.json_response({"downloadPath": BUNDLE + "/file?ticket=1." + "a" * 43})

        async def bundle_download(request):
            return web.Response(body=self.bundle_body, content_type="application/zip")

        app.router.add_post("/v1/device-sessions/register", guest)
        app.router.add_get("/v2/curated-resources/{id}", detail)
        app.router.add_post("/v1/accounts/login", login)
        app.router.add_get("/v1/accounts/download-membership", membership)
        app.router.add_post("/v2/curated-resources/{id}/private-download", ticket)
        app.router.add_get("/v2/curated-resources/{id}/private-download/file", download)
        app.router.add_get(API + "/download-files", files)
        app.router.add_post(BUNDLE + "/ticket", bundle_ticket)
        app.router.add_get(BUNDLE + "/file", bundle_download)
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        self.addAsyncCleanup(self.site.close)
        listener = web.TCPSite(runner, "127.0.0.1", port=0)
        await listener.start()
        origin = f"http://127.0.0.1:{runner.addresses[0][1]}"
        origin_patch = patch("heiyao_import.SITE", origin)
        origin_patch.start()
        self.addCleanup(origin_patch.stop)

    async def test_direct_download_reuses_the_ticket_connection(self):
        session = await self.site.login("test-account", "local-test-credential")
        await self.site.get_weight(RESOURCE, session, allow_download=True)
        self.assertEqual(len(self.transfer_connections), 2)
        self.assertIs(*self.transfer_connections)

    async def test_distinct_downloads_overlap_and_duplicate_reuses_the_file(self):
        self.release_first = asyncio.Event()
        session = await self.site.login("test-account", "local-test-credential")
        first = asyncio.create_task(self.site.get_weight(RESOURCE, session, allow_download=True))
        duplicate = None
        try:
            await asyncio.wait_for(self.first_started.wait(), timeout=3)
            duplicate = asyncio.create_task(self.site.get_weight(RESOURCE, session, allow_download=True))
            try:
                second = await asyncio.wait_for(
                    self.site.get_weight(SECOND_RESOURCE, session, allow_download=True), timeout=3,
                )
            except TimeoutError:
                self.fail("A distinct LoRA could not finish while the first transfer was waiting")
            self.assertEqual((self.root / second[0]).read_bytes(), b"second")
            self.assertFalse(duplicate.done())
        finally:
            self.release_first.set()
            await asyncio.gather(*([first, duplicate] if duplicate else [first]), return_exceptions=True)
        self.assertEqual(first.result()[0], duplicate.result()[0])
        self.assertTrue(duplicate.result()[2])
        self.assertEqual(self.ticket_requests, 2)

    async def test_only_authorized_download_and_reuse_never_requests_ticket_twice(self):
        with self.assertRaises(HeiyaoError) as missing_login:
            await self.site.get_weight(RESOURCE, None, allow_download=True)
        self.assertEqual(missing_login.exception.code, "LOGIN_REQUIRED")
        self.assertEqual(self.ticket_requests, 0)

        session = await self.site.login("test-account", "local-test-credential")
        with self.assertRaises(HeiyaoError) as unconfirmed:
            await self.site.get_weight(RESOURCE, session)
        self.assertEqual(unconfirmed.exception.status, 409)
        self.assertEqual(self.ticket_requests, 0)
        name, prompt, reused = await self.site.get_weight(RESOURCE, session, allow_download=True)
        self.assertFalse(reused)
        self.assertEqual((self.root / name).read_bytes(), CHECKPOINT)
        self.assertEqual(prompt, "One female character.")

        second_name, _, reused = await self.site.get_weight(RESOURCE, None)
        self.assertEqual(second_name, name)
        self.assertTrue(reused)
        self.assertEqual(self.ticket_requests, 1)

    async def test_versioned_download_preserves_old_weight_and_reuses_new_weight(self):
        old = self.root / "黑曜官网" / RESOURCE / FILENAME
        old.parent.mkdir(parents=True)
        old.write_bytes(b"previous local weight")
        with self.assertRaises(HeiyaoError) as missing_login:
            await self.site.import_weight(RESOURCE, None, allow_download=True, versioned=True)
        self.assertEqual(missing_login.exception.code, "LOGIN_REQUIRED")
        self.assertEqual(self.ticket_requests, 0)
        session = await self.site.login("test-account", "local-test-credential")
        name, resource, reused = await self.site.import_weight(
            RESOURCE, session, allow_download=True, versioned=True,
        )
        self.assertFalse(reused)
        self.assertEqual(resource.destination(self.root, versioned=True), self.root / name)
        self.assertEqual((self.root / name).read_bytes(), CHECKPOINT)
        self.assertEqual(old.read_bytes(), b"previous local weight")
        second_name, _, reused = await self.site.import_weight(RESOURCE, None, versioned=True)
        self.assertEqual(second_name, name)
        self.assertTrue(reused)
        self.assertEqual(self.ticket_requests, 1)

    async def test_bad_digest_leaves_no_model_or_partial_file(self):
        self.download_body = b"X" * len(CHECKPOINT)
        session = await self.site.login("test-account", "local-test-credential")
        with self.assertRaises(HeiyaoError) as error:
            await self.site.get_weight(RESOURCE, session, allow_download=True)
        self.assertIn("SHA-256", str(error.exception))
        self.assertEqual(list(self.root.rglob("*.safetensors")), [])
        self.assertEqual(list(self.root.rglob("*.part")), [])

    async def test_compressed_response_uses_decoded_weight_size(self):
        self.download_body = gzip.compress(CHECKPOINT)
        self.download_headers = {"Content-Encoding": "gzip"}
        session = await self.site.login("test-account", "local-test-credential")
        name, _, reused = await self.site.get_weight(RESOURCE, session, allow_download=True)
        self.assertFalse(reused)
        self.assertEqual((self.root / name).read_bytes(), CHECKPOINT)
        self.assertEqual(self.ticket_requests, 1)
        self.assertEqual(self.bundle_requests, 0)

    async def test_unavailable_weight_uses_official_zip_and_installs_only_pinned_member(self):
        self.download_status = 502
        self.bundle_preparations = 1
        archive = BytesIO()
        with ZipFile(archive, "w") as bundle:
            bundle.writestr("release/" + FILENAME, CHECKPOINT)
            bundle.writestr("说明.txt", "Example attachment")
        self.bundle_body = archive.getvalue()
        session = await self.site.login("test-account", "local-test-credential")
        with patch("heiyao_import.ZIP_POLL_INTERVAL", 0):
            name, _, reused = await self.site.get_weight(RESOURCE, session, allow_download=True)
        self.assertFalse(reused)
        self.assertEqual((self.root / name).read_bytes(), CHECKPOINT)
        self.assertEqual(self.bundle_requests, 2)
        self.assertEqual(list(self.root.rglob("*.zip")), [])
        self.assertEqual(list(self.root.rglob("说明.txt")), [])
        self.assertEqual(list(self.root.rglob("*.part")), [])

    async def test_official_zip_with_same_name_and_size_but_wrong_sha_is_rejected(self):
        self.download_status = 502
        archive = BytesIO()
        with ZipFile(archive, "w") as bundle:
            bundle.writestr(FILENAME, b"X" * len(CHECKPOINT))
        self.bundle_body = archive.getvalue()
        session = await self.site.login("test-account", "local-test-credential")
        with self.assertRaisesRegex(HeiyaoError, "SHA-256"):
            await self.site.get_weight(RESOURCE, session, allow_download=True)
        self.assertEqual(list(self.root.rglob("*.safetensors")), [])
        self.assertEqual(list(self.root.rglob("*.part")), [])


    async def test_interrupted_transfer_uses_same_ticket(self):
        session = await self.site.login("test-account", "local-test-credential")
        self.download_status = 503
        with self.assertRaises(HeiyaoError):
            await self.site.get_weight(RESOURCE, session, allow_download=True)
        self.download_status = 200
        await self.site.get_weight(RESOURCE, session, allow_download=True)
        self.assertEqual(self.ticket_requests, 1)

    async def test_ambiguous_ticket_failure_needs_explicit_renewal(self):
        session = await self.site.login("test-account", "local-test-credential")
        self.ticket_status = 503
        with self.assertRaises(HeiyaoError):
            await self.site.get_weight(RESOURCE, session, allow_download=True)
        self.ticket_status = 200
        self.ticket_error = "SITE_BUSY"
        self.download_requests = 0
        with self.assertRaises(HeiyaoError) as retry:
            await self.site.get_weight(RESOURCE, session, allow_download=True)
        self.assertEqual(retry.exception.code, "NEW_TICKET_REQUIRED")
        self.assertEqual(self.ticket_requests, 1)
        await self.site.get_weight(RESOURCE, session, allow_download=True, renew_ticket=True)
        self.assertEqual(self.ticket_requests, 2)

    async def test_unusable_file_ticket_requires_explicit_new_request(self):
        session = await self.site.login("test-account", "local-test-credential")
        self.download_status = 403
        with self.assertRaises(HeiyaoError) as failed:
            await self.site.get_weight(RESOURCE, session, allow_download=True)
        self.assertEqual(failed.exception.code, "NEW_TICKET_REQUIRED")
        self.assertEqual(self.ticket_requests, 1)

    async def test_quota_is_read_from_account_before_any_ticket(self):
        session = await self.site.login("test-account", "local-test-credential")
        state = await self.site.membership(session)
        self.assertEqual((state["tier"], state["used"], state["remaining"]), ("QUARTERLY", 4, 1))
        self.assertEqual(self.ticket_requests, 0)

    async def test_official_membership_and_quota_refusals_never_fetch_bytes(self):
        session = await self.site.login("test-account", "local-test-credential")
        self.ticket_status = 403
        for code in ("DOWNLOAD_MEMBERSHIP_REQUIRED", "DOWNLOAD_QUOTA_EXHAUSTED"):
            self.ticket_error = code
            with self.assertRaises(HeiyaoError) as denial:
                await self.site.get_weight(RESOURCE, session, allow_download=True)
            self.assertEqual(denial.exception.code, code)
        self.assertEqual(self.download_requests, 0)
        self.assertEqual(list(self.root.rglob("*.safetensors")), [])
