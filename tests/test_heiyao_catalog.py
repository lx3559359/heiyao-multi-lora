import asyncio
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from heiyao_import import HeiyaoError, Resource, Site, website_prompt


FIRST = "11111111-1111-1111-1111-111111111111"
STYLE = "22222222-2222-2222-2222-222222222222"


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_reads_later_pages_and_ranks_matching_titles_first(self):
        site = Site.__new__(Site)
        requests = []

        async def read(path):
            params = parse_qs(urlsplit(path).query)
            requests.append(params)
            if "cursor" not in params:
                return {"items": [{"id": FIRST, "title": "角色 LoRA", "tags": ["LoRA"]}], "nextCursor": "page+2"}
            return {"items": [
                {"id": STYLE, "title": "黑曜动漫二次元 | Krea 2 风格 LoRA", "tags": ["LoRA"]},
                {"id": FIRST, "title": "角色 LoRA", "tags": ["LoRA"]},
            ]}

        site.read = read
        items = await site.search("黑曜动漫二次元")
        self.assertEqual([item["id"] for item in items], [STYLE, FIRST])
        self.assertEqual(requests[1]["cursor"], ["page+2"])

    async def test_common_reversed_style_name_uses_website_title(self):
        site = Site.__new__(Site)

        async def read(path):
            params = parse_qs(urlsplit(path).query)
            self.assertEqual(params["query"], ["黑曜真人古风"])
            return {"items": [{"id": STYLE, "title": "黑曜真人古风", "tags": ["LoRA"]}]}

        site.read = read
        self.assertEqual((await site.search("黑曜古风真人"))[0]["id"], STYLE)

    def test_style_without_fixed_trigger(self):
        self.assertEqual(website_prompt("Krea 2 风格 LoRA,无固定触发词。示例。"), "")

    async def test_style_with_verified_title_and_weight_in_models_subdirectory(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd() / "temp") as directory:
            model = Path(directory) / "style.safetensors"
            model.write_bytes(b"verified style")
            name = "Styles/style.safetensors"

            record = {"title": "风格 LoRA", "version": "官网发布版", "name": model.name,
                      "bytes": model.stat().st_size, "sha256": hashlib.sha256(model.read_bytes()).hexdigest()}
            site = Site.__new__(Site)
            site.index = {STYLE: record}
            site.local_loras = lambda: {name: model}

            async def read(path):
                return {"id": STYLE, "title": record["title"], "tags": ["LoRA"], "summary": "无固定触发词。"}

            site.read = read
            resource = await site.resource(STYLE)
            self.assertEqual(resource.prompt, "")
            self.assertEqual(site.local_weight(resource), name)
            model.write_bytes(b"other contents")
            self.assertIsNone(site.local_weight(resource))


    async def test_library_compare_by_content_with_local_only_and_unverified_entries(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd() / "temp") as directory:
            root = Path(directory)
            renamed = root / "renamed.safetensors"
            renamed.write_bytes(b"correct")
            mismatch = root / "style.safetensors"
            mismatch.write_bytes(b"other!!")
            no_pin = "33333333-3333-3333-3333-333333333333"
            index = root / "index.json"

            def record(title, filename, payload):
                return {"title": title, "version": "官网发布版", "name": filename,
                        "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}

            entries = {FIRST: record("角色 LoRA", "role.safetensors", b"correct"),
                       STYLE: record("风格 LoRA", "style.safetensors", b"missing")}
            index.write_text(json.dumps(entries), encoding="utf-8")
            site = Site(root, root, index_path=index, local_loras=lambda: {
                "renamed.safetensors": renamed, "style.safetensors": mismatch,
            })

            async def search(query):
                self.assertEqual(query, "")
                return [{"id": FIRST, "title": "角色 LoRA"}, {"id": STYLE, "title": "风格 LoRA"},
                        {"id": no_pin, "title": "新 LoRA"}]

            site.search = search

            async def read(path):
                raise HeiyaoError("Test resource has no public weight information", 404)

            site.read = read
            result = await site.compare_library()
            self.assertEqual((result["local_count"], result["website_count"], result["matched_count"]), (2, 3, 1))
            self.assertEqual([entry["id"] for entry in result["missing"]], [STYLE])
            self.assertEqual([entry["id"] for entry in result["unverified"]], [no_pin])
            resource = Resource(FIRST, "角色 LoRA", "", "官网发布版", "role.safetensors", 7, entries[FIRST]["sha256"])
            self.assertEqual(site.local_weight(resource), renamed.name)
            renamed.write_bytes(b"changed")
            self.assertIsNone(site.local_weight(resource))

    async def test_library_download_rejects_a_changed_pin_before_membership(self):
        site = Site.__new__(Site)
        site.import_lock = asyncio.Lock()

        async def current_release(resource_id):
            return Resource(resource_id, "角色 LoRA", "", "v002", "role.safetensors", 7, "a" * 64)

        def unexpected_filesystem_check(resource):
            self.fail("Changed pin should stop before checking for the file or requesting a ticket")

        site.resource = current_release
        site.local_weight = unexpected_filesystem_check
        with self.assertRaises(HeiyaoError) as error:
            await site.import_weight(FIRST, None, allow_download=True, expected_sha="b" * 64)
        self.assertEqual(error.exception.status, 409)
