"""Keep user state outside the replaceable Manager package directory."""
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
    directories = folder_paths.get_folder_paths("loras")
    return Path(directories[0]).resolve() if directories else None
