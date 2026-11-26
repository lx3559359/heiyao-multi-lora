import hashlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from heiyao_versions import inspect_slot


OLD = "11111111-1111-1111-1111-111111111111"
NEW = "22222222-2222-2222-2222-222222222222"
OTHER = "33333333-3333-3333-3333-333333333333"


def record(data, version, title):
    return {"version": version, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "name": "role.safetensors", "catalog_title": title}


class FixtureSite:
    def __init__(self):
        self.index = {OLD: record(b"first model", "v001", "某剧 · 角色甲 | Krea 2 LoRA v001"),
                      NEW: record(b"new model", "v002", "某剧 | 角色甲 | Krea 2 角色 LoRA v002")}
        self.bundled_index = dict(self.index)
        self.items = [{"id": OLD, "title": "某剧 · 角色甲 | Krea 2 LoRA v001 | 示例图修订 v009"},
                      {"id": NEW, "title": "某剧 | 角色甲 | Krea 2 角色 LoRA v002"},
                      {"id": OTHER, "title": "某剧 | 角色甲 | FLUX LoRA v008"}]

    async def refresh_index(self):
        return True

    async def search(self, query):
        return self.items

    async def catalog_weights(self, items):
        return self.index

    async def detail(self, resource_id):
        assert resource_id == NEW
        return {"local": False, "available": True, "bytes": 9, "prompt": "role, style.", "head_prompt": "Head."}


class SlotVersionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(dir=Path.cwd() / "temp")
        self.addCleanup(self.directory.cleanup)
        self.weight = Path(self.directory.name) / "renamed.safetensors"
        self.weight.write_bytes(b"first model")
        self.site = FixtureSite()

    async def test_hash_identifies_renamed_weight_and_finds_same_model_version(self):
        result = await inspect_slot(self.site, self.weight.name, self.weight)
        self.assertEqual(result["current"]["version"], "v001")
        self.assertEqual(result["latest"]["id"], NEW)
        self.assertEqual(result["latest"]["version"], "v002")
        self.assertEqual(result["state"], "update")
        self.assertTrue(result["latest"]["available"])

    async def test_online_version_without_verified_weight_can_be_displayed_but_not_downloaded(self):
        del self.site.index[NEW]
        del self.site.bundled_index[NEW]
        result = await inspect_slot(self.site, self.weight.name, self.weight)
        self.assertEqual(result["latest"]["version"], "v002")
        self.assertEqual(result["state"], "unverified")
        self.assertFalse(result["latest"]["available"])

    async def test_edited_weight_is_unrecognized_even_with_same_filename(self):
        self.weight.write_bytes(b"other model")
        result = await inspect_slot(self.site, "role.safetensors", self.weight)
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["current"]["version"])

    async def test_example_revision_does_not_count_as_new_model_weight(self):
        self.site.items = self.site.items[:1]
        result = await inspect_slot(self.site, self.weight.name, self.weight)
        self.assertEqual(result["state"], "current")
        self.assertEqual(result["latest"]["version"], "v001")
