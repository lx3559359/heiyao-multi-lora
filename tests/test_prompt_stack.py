import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from prompt_stack import Library, Slot, compose, decode_slots


class PromptStackTests(unittest.TestCase):
    def test_full_paragraph_precedes_scene(self):
        paragraph = "dpdievex, a female character.\nA softly tapered oval face."
        slots = [Slot("角色/蝶.safetensors", True, 0.9, True, {})]
        self.assertEqual(
            compose(slots, "  A garden at dawn. ", {"角色/蝶.safetensors": paragraph}),
            paragraph + "\n\nA garden at dawn.",
        )

    def test_skip_disabled_zero_and_unmarked_slots(self):
        slots = [
            Slot("a", False, 0.9, True, {}),
            Slot("a", True, 0, True, {}),
            Slot("a", True, 0.9, False, {}),
        ]
        self.assertEqual(compose(slots, "Scene.", {"a": "Hidden."}), "Scene.")

    def test_override_is_bound_to_file_and_empty_override_is_respected(self):
        overrides = {"v1/a.safetensors": "", "v2/a.safetensors": "Version two."}
        self.assertEqual(compose([Slot("v1/a.safetensors", True, 1, True, overrides)], "Scene.", {
            "v1/a.safetensors": "Default one.",
        }), "Scene.")
        self.assertEqual(compose([Slot("v2/a.safetensors", True, 1, True, overrides)], "", {}), "Version two.")
        self.assertEqual(compose([Slot("v3/a.safetensors", True, 1, True, overrides)], "", {}), "")

    def test_paths_normalize_separators_and_reject_traversal(self):
        self.assertEqual(decode_slots(r'[{"name":"dir\\a.safetensors"}]')[0].name, "dir/a.safetensors")
        with self.assertRaises(ValueError):
            decode_slots('[{"name":"../a.safetensors"}]')

    def test_slot_two_defaults_to_injecting(self):
        self.assertEqual([slot.inject for slot in decode_slots("[{}, {}]")],
                         [False, True])

    def test_empty_stack_preserves_scene_after_last_card_removed(self):
        self.assertEqual(decode_slots("[]"), [])
        self.assertEqual(compose(decode_slots("[]"), "Scene.", {}), "Scene.")

    def test_added_cards_above_eight_compose_in_saved_order(self):
        rows = [{} for _ in range(8)]
        rows.extend([
            {"name": "a", "enabled": True, "inject": True},
            {"name": "b", "enabled": True, "inject": True},
        ])
        loaded = decode_slots(json.dumps(rows))
        self.assertEqual(compose(loaded, "Scene.", {"a": "A.", "b": "B."}), "A.\n\nB.\n\nScene.")

    def test_library_saves_utf8_without_erasing_other_defaults(self):
        project_temp = pathlib.Path(__file__).resolve().parents[3] / "temp"
        project_temp.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=project_temp) as directory:
            path = pathlib.Path(directory) / "defaults.json"
            lib = Library(path, {"old.safetensors": "Existing."})
            lib.save("版本/蝶.safetensors", "完整段落。")
            self.assertEqual(Library(path, {"old.safetensors": "Existing."}).read(),
                             {"old.safetensors": "Existing.", "版本/蝶.safetensors": "完整段落。"})

    def test_dedupe_exact_sections_and_preserve_slot_order(self):
        slots = [
            Slot("a", True, -1, True, {}),
            Slot("b", True, 1, True, {}),
            Slot("a", True, 1, True, {}),
        ]
        self.assertEqual(compose(slots, "A.", {"a": "A.", "b": "B."}), "A.\n\nB.")
