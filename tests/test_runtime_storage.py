import pathlib
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from runtime_storage import lora_root, prepare_state


class UpdateStorageTests(unittest.TestCase):
    def test_existing_triggers_and_device_are_migrated_once_to_user_folder(self):
        with tempfile.TemporaryDirectory(dir=pathlib.Path.cwd() / "temp") as directory:
            root = pathlib.Path(directory)
            package = root / "custom_nodes" / "heiyao-multi-lora"
            legacy = package / "data"
            legacy.mkdir(parents=True)
            (legacy / "triggers.json").write_text('{"old": "Legacy."}', encoding="utf-8")
            (legacy / "heiyao_device.json").write_text('{"deviceId": "fixture"}', encoding="utf-8")
            state = root / "user" / "__personal_lora"
            paths = SimpleNamespace(get_system_user_directory=lambda name: str(state))
            self.assertEqual(prepare_state(package, paths), state)
            triggers = state / "triggers.json"
            self.assertEqual(triggers.read_text(encoding="utf-8"), '{"old": "Legacy."}')
            self.assertEqual((state / "heiyao_device.json").read_text(encoding="utf-8"),
                             '{"deviceId": "fixture"}')
            triggers.write_text('{"old": "Edited after install."}', encoding="utf-8")
            next_package = root / "anywhere" / "new-version"
            self.assertEqual(prepare_state(next_package, paths), state)
            self.assertEqual(triggers.read_text(encoding="utf-8"), '{"old": "Edited after install."}')
            self.assertEqual(prepare_state(package, paths), state)
            self.assertEqual(triggers.read_text(encoding="utf-8"), '{"old": "Edited after install."}')

    def test_website_download_targets_default_registered_comfy_lora_path(self):
        base = pathlib.Path.cwd() / "temp" / "manager-models"
        custom_root = base / "loras"
        paths = SimpleNamespace(get_folder_paths=lambda kind: [str(custom_root), str(base / "other")])
        self.assertEqual(lora_root(paths), custom_root.resolve())
        self.assertIsNone(lora_root(SimpleNamespace(get_folder_paths=lambda kind: [])))
