import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from heiyao_import import HeiyaoError, Site


RESOURCE_ID = "11111111-1111-1111-1111-111111111111"
TITLE = "作品 · 新角色 | Krea 2 角色 LoRA v012"
MARKER = "官网导入校验：v012 | 21 | " + "a" * 64 + " | 新角色.safetensors"
SHORT = "newperson, cinematic game render"
HEAD = SHORT + ", black hair, hazel eyes"


class LiveReleaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.site = Site.__new__(Site)
        self.site.index = {}
        self.site.bundled_index = {}
        self.site.local_loras = lambda: {}
        self.site.compare_lock = asyncio.Lock()
        self.site.index_url = None
        self.site.website_pins = {}
        self.site.local_fingerprints = {}
        self.summary = (
            "简短触发词（默认）\u2028" + SHORT + "\u2028"
            "完整头部特征触发词（可选）\u2028" + HEAD + "\u2028"
            "当前版本的换装说明。\u2028" + MARKER
        )

        async def read(path):
            if path.endswith("/private-download/status"):
                return {"available": True}
            return {"id": RESOURCE_ID, "title": TITLE, "tags": ["LoRA"], "summary": self.summary}

        async def search(query):
            return [{"id": RESOURCE_ID, "title": TITLE}]

        self.site.read = read
        self.site.search = search

    async def test_new_release_uses_official_registration_without_a_github_pin(self):
        model = await self.site.resource(RESOURCE_ID)
        self.assertEqual((model.name, model.version, model.size, model.sha256), ("新角色.safetensors", "v012", 21, "a" * 64))
        self.assertEqual((model.prompt, model.head_prompt), (SHORT, HEAD))
        self.assertTrue((await self.site.detail(RESOURCE_ID))["available"])

    async def test_new_release_is_in_the_library_comparison(self):
        result = await self.site.compare_library()
        self.assertEqual(result["unverified"], [])
        self.assertEqual([item["id"] for item in result["missing"]], [RESOURCE_ID])

    async def test_bad_or_duplicate_official_registration_is_not_importable(self):
        for marker in (MARKER.replace("v012", "v013"), MARKER.replace("新角色", "../新角色"), MARKER + "\u2028" + MARKER):
            with self.subTest(marker=marker):
                self.summary = "完整触发提示词：newperson\u2028" + marker
                self.site.index = {
                    RESOURCE_ID: {"title": TITLE, "name": "old.safetensors", "version": "v012", "bytes": 1, "sha256": "b" * 64},
                }
                with self.assertRaises(HeiyaoError) as error:
                    await self.site.resource(RESOURCE_ID)
                self.assertEqual(error.exception.status, 409)
