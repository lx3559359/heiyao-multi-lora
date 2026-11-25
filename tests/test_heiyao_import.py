import hashlib
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from heiyao_import import HeiyaoError, Resource, full_prompt, lookup_id, matching_weight, safe_ticket, verify_weight, membership_view, website_prompt

ROLE = "7fd546ab-1c65-4772-a56b-511a5c33f908"

class WebsiteImportTests(unittest.TestCase):
    def test_reads_full_labeled_section_preserving_line_breaks(self):
        source = "角色识别词: dpdievex\u2028完整触发提示词(整段复制,再接场景):\u2028dpdievex, a female character.\u2028Face detail.\u2028\u2028造型: test"
        self.assertEqual(full_prompt(source), "dpdievex, a female character.\nFace detail.")

    def test_refuses_to_guess_prompt_without_full_section(self):
        with self.assertRaises(HeiyaoError):
            full_prompt("角色识别词: dpdievex")

    def test_accepts_only_actual_official_lora_detail_link(self):
        self.assertEqual(lookup_id(f"https://openheiyao.top/#lora/{ROLE}"), ROLE)
        self.assertIsNone(lookup_id(f"https://example.org/#lora/{ROLE}"))

    def test_ticket_is_bound_to_requested_resource_and_origin(self):
        path = f"/v2/curated-resources/{ROLE}/private-download/file?ticket=10." + "x" * 43
        self.assertEqual(safe_ticket(ROLE, path), path)
        for unsafe in ("https://evil.invalid" + path, path + "&redirect=x", path.replace(ROLE, "different")):
            with self.assertRaises(HeiyaoError):
                safe_ticket(ROLE, unsafe)

    def test_local_alias_only_reused_if_checksum_matches(self):
        project_temp = pathlib.Path.cwd() / "temp"
        with tempfile.TemporaryDirectory(dir=project_temp) as directory:
            root = pathlib.Path(directory)
            content = b"weight checkpoint"
            entry = Resource(ROLE, "Title", "Prompt.", "v001", "test_v001.safetensors",
                             len(content), hashlib.sha256(content).hexdigest())
            path = root / "test.safetensors"
            path.write_bytes(content)
            self.assertEqual(matching_weight(root, entry), path)
            path.write_bytes(b"bad checkpoint!!")
            self.assertIsNone(matching_weight(root, entry))
            self.assertFalse(verify_weight(path, entry))


    def test_official_style_without_fixed_trigger(self):
        self.assertEqual(website_prompt("随附示例通过加载器启用,未记录固定触发词。"), "")
        with self.assertRaises(HeiyaoError):
            website_prompt("使用建议：在加载器里启用。")

    def test_membership_view_uses_website_quotas(self):
        value = {"monthStart": "2026-11-01", "membership": {"tier": "MONTHLY"},
                 "active": True, "lora": {"used": 3, "remaining": 2}}
        self.assertEqual(membership_view(value), {
            "month": "2026-11", "tier": "MONTHLY", "active": True,
            "used": 3, "remaining": 2, "unlimited": False,
        })
        value["membership"]["tier"] = "LIFETIME"
        value["lora"]["remaining"] = None
        self.assertTrue(membership_view(value)["unlimited"])
