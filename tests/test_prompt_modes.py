import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from prompt_stack import compose, decode_slots


class PromptModeTests(unittest.TestCase):
    def compose_mode(self, **mode_fields):
        filename = "folder/role.safetensors"
        row = dict(name=filename, enabled=True, inject=True, **mode_fields)
        rows = decode_slots(json.dumps([row]))
        return compose(rows, "Scene.", {filename: "role, animation."}, {filename: "role, blue eyes."})

    def test_default_and_head(self):
        self.assertEqual(self.compose_mode(), "role, animation.\n\nScene.")
        self.assertEqual(self.compose_mode(prompt_mode="head"), "role, blue eyes.\n\nScene.")

    def test_independent_overrides(self):
        short = {"folder/role.safetensors": "My short."}
        heads = {"folder/role.safetensors": ""}
        self.assertEqual(self.compose_mode(overrides=short, head_overrides=heads), "My short.\n\nScene.")
        self.assertEqual(self.compose_mode(prompt_mode="head", overrides=short, head_overrides=heads), "Scene.")

    def test_website_two_tiers(self):
        from heiyao_import import website_head_prompt, website_prompt
        summary = "完整触发提示词(默认短版,角色词+画风):\nrole, animation.\n\n完整头部特征触发词(可选):\nrole, animation. Blue eyes.\n\n造型: Example."
        self.assertEqual(website_prompt(summary), "role, animation.")
        self.assertEqual(website_head_prompt(summary), "role, animation. Blue eyes.")
        self.assertEqual(website_head_prompt("完整触发提示词:\nlegacy"), "legacy")
