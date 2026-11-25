"""Prompt composition and persistent defaults for Personal LoRA Loader."""
from dataclasses import dataclass
import json
import math
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock


def lora_key(name):
    if not isinstance(name, str):
        raise ValueError("LoRA 路径必须是字符串")
    path = name.replace("\\", "/")
    if path in ("", "None"):
        return ""
    if ":" in path or "\0" in path or any(part in ("", ".", "..") for part in path.split("/")):
        raise ValueError(f"无效的 LoRA 相对路径：{name}")
    return path


def text_field(value):
    if not isinstance(value, str):
        raise ValueError("触发提示词必须是字符串")
    return value.strip()


@dataclass(frozen=True)
class Slot:
    name: str
    enabled: bool
    strength: float
    inject: bool
    overrides: dict

    @property
    def active(self):
        return bool(self.name and self.enabled and self.strength != 0)

    def trigger(self, defaults):
        return self.overrides.get(self.name, defaults.get(self.name, ""))


def decode_slots(payload):
    rows = json.loads(payload)
    if not isinstance(rows, list):
        raise ValueError("LoRA 槽必须是 JSON 数组")
    slots = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"第 {index + 1} 槽必须是 JSON 对象")
        strength = row.get("strength", 0.9)
        if (type(strength) not in (int, float) or not -100 <= strength <= 100
                or not math.isfinite(strength)):
            raise ValueError(f"第 {index + 1} 槽的权重必须介于 -100 和 100")
        enabled = row.get("enabled", False)
        inject = row.get("inject", index == 1)
        if type(enabled) is not bool or type(inject) is not bool:
            raise ValueError(f"第 {index + 1} 槽的开关必须是布尔值")
        overrides = row.get("overrides", {})
        if not isinstance(overrides, dict):
            raise ValueError(f"第 {index + 1} 槽的工作流触发词必须是 JSON 对象")
        slots.append(Slot(
            lora_key(row.get("name", "")), enabled, strength, inject,
            {lora_key(key): text_field(value) for key, value in overrides.items()},
        ))
    return slots


def compose(slots, scene, defaults):
    scene = scene.strip()
    sections = {}
    for slot in slots:
        if slot.active and slot.inject:
            trigger = slot.trigger(defaults).strip()
            if trigger:
                sections[trigger] = None
    if scene:
        sections[scene] = None
    return "\n\n".join(sections)


class Library:
    def __init__(self, path, examples):
        self.path = Path(path)
        self.examples = examples
        self.lock = RLock()

    def _read_user(self):
        try:
            with self.path.open(encoding="utf-8-sig") as stream:
                return json.load(stream)
        except FileNotFoundError:
            return {}

    def read(self):
        with self.lock:
            return self.examples | self._read_user()

    def remember_import(self, name, prompt):
        with self.lock:
            if name not in self._read_user():
                self.save(name, prompt)

    def save(self, name, prompt):
        key = lora_key(name)
        if not key:
            raise ValueError("请先选择一个 LoRA")
        prompt = text_field(prompt)
        with self.lock:
            records = self._read_user()
            records[key] = prompt
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                    prefix="lora-triggers-", suffix=".tmp", delete=False) as stream:
                pending = Path(stream.name)
                try:
                    json.dump(records, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                except BaseException:
                    stream.close()
                    pending.unlink(missing_ok=True)
                    raise
            try:
                pending.replace(self.path)
            finally:
                pending.unlink(missing_ok=True)
            return self.examples | records
