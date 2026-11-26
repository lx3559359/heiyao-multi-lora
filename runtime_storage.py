"""Keep user state outside the replaceable Manager package directory."""
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def prepare_state(package, folder_paths):
    system_user = getattr(folder_paths, "get_system_user_directory", None)
    if system_user:
        state = Path(system_user("personal_lora"))
    else:
        state = Path(folder_paths.get_user_directory()) / "__personal_lora"

    for name in ("triggers.json", "heiyao_device.json"):
        old = package / "data" / name
        destination = state / name
        if not old.is_file() or destination.exists():
            continue
        state.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=state, suffix=".tmp", delete=False) as stream:
            pending = Path(stream.name)
            try:
                stream.write(old.read_bytes())
                stream.close()
                if not destination.exists():
                    pending.replace(destination)
            finally:
                pending.unlink(missing_ok=True)
    return state


def lora_root(folder_paths):
    module_file = getattr(folder_paths, "__file__", None)
    if not module_file:
        return None
    root = Path(module_file).resolve().parent / "models" / "loras"
    return root if root.resolve() == root else None


def resolve_lora(root, name, extensions):
    if root is None:
        return None
    path = root / name
    if path.suffix.lower() not in extensions:
        return None
    try:
        path = path.resolve()
        if path.is_relative_to(root) and path.is_file():
            return path
    except (OSError, RuntimeError):
        pass
    return None


def unlinked_directory(path):
    try:
        return path.resolve() == path
    except (OSError, RuntimeError):
        return False


def lora_catalog(root, extensions):
    if root is None:
        return {}
    catalog = {}
    for directory, folders, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        # Exclude linked subdirectories, including Windows junctions.
        folders[:] = [name for name in folders if name != ".git" and unlinked_directory(parent / name)]
        for filename in files:
            name = (parent / filename).relative_to(root).as_posix()
            path = resolve_lora(root, name, extensions)
            if path is not None:
                catalog[name] = path
    return dict(sorted(catalog.items()))
