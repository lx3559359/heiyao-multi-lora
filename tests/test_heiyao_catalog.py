from pathlib import Path
import hashlib
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from heiyao_import import Site, website_prompt


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

    async def test_style_with_verified_title_and_weight_in_extra_model_directory(self):
        from heiyao_import import matching_comfy_weight

        with tempfile.TemporaryDirectory(dir=Path.cwd() / "temp") as directory:
            model = Path(directory) / "style.safetensors"
            model.write_bytes(b"verified style")
            name = "extra/style.safetensors"

            class FolderPaths:
                def get_filename_list(self, category):
                    return [name]

                def get_full_path(self, category, key):
                    return model if key == name else None

            record = {"title": "风格 LoRA", "version": "官网发布版", "name": model.name,
                      "bytes": model.stat().st_size, "sha256": hashlib.sha256(model.read_bytes()).hexdigest()}
            site = Site.__new__(Site)
            site.index = {STYLE: record}

            async def read(path):
                return {"id": STYLE, "title": record["title"], "tags": ["LoRA"], "summary": "无固定触发词。"}

            site.read = read
            resource = await site.resource(STYLE)
            self.assertEqual(resource.prompt, "")
            self.assertEqual(matching_comfy_weight(FolderPaths(), resource), name)
            model.write_bytes(b"other contents")
            self.assertIsNone(matching_comfy_weight(FolderPaths(), resource))
