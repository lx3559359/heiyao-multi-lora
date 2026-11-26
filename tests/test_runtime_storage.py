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

    def test_install_root_wins_even_when_model_directory_is_overridden(self):
        base = pathlib.Path.cwd() / "temp" / "manager-models"
        custom_root = base / "models" / "loras"
        paths = SimpleNamespace(__file__=str(base / "folder_paths.py"), models_dir=str(base / "other"),
                                get_folder_paths=lambda kind: [str(base / "other"), str(custom_root)])
        self.assertEqual(lora_root(paths), custom_root.resolve())
        self.assertIsNone(lora_root(SimpleNamespace()))

    def test_only_files_inside_the_main_lora_root_are_available(self):
        from runtime_storage import lora_catalog, resolve_lora

        with tempfile.TemporaryDirectory(dir=pathlib.Path.cwd() / "temp") as directory:
            base = pathlib.Path(directory).resolve()
            root = base / "models" / "loras"
            (root / "Characters").mkdir(parents=True)
            other = base / "external"
            other.mkdir()
            weight = root / "Characters" / "role.safetensors"
            weight.write_bytes(b"main weight")
            (root / "notes.txt").write_text("notes", encoding="utf-8")
            (other / "role.safetensors").write_bytes(b"external weight")
            extensions = {".safetensors"}
            self.assertEqual(lora_catalog(root, extensions), {"Characters/role.safetensors": weight})
            self.assertEqual(resolve_lora(root, "Characters/role.safetensors", extensions), weight)
            self.assertIsNone(resolve_lora(root, "../../external/role.safetensors", extensions))
