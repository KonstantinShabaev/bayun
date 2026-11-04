"""Список ламп из файла с ключами.

Понимает devices.json от `tinytuya wizard` (список объектов) и словарь
«имя → параметры». Поля: id, key или local_key, ip, version, name.
Ключи — секрет: файл держим с правами 600 и в репозиторий не кладём.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("~/.config/bayun/tuya-devices.json").expanduser()


@dataclass(frozen=True)
class Lamp:
    name: str
    id: str
    key: str
    ip: str
    version: float = 3.5


def _lamp(raw: dict[str, Any], fallback_name: str) -> Lamp | None:
    key = raw.get("key") or raw.get("local_key")
    ip = raw.get("ip") or raw.get("address") or raw.get("host")
    dev_id = raw.get("id") or raw.get("device_id") or raw.get("gwId")
    if not (key and ip and dev_id):
        return None
    return Lamp(
        name=str(raw.get("name") or fallback_name),
        id=str(dev_id),
        key=str(key),
        ip=str(ip),
        version=float(raw.get("version") or raw.get("protocol_version") or 3.5),
    )


def parse(data: Any) -> list[Lamp]:
    if isinstance(data, dict) and "devices" in data:
        data = data["devices"]
    items: list[tuple[str, Any]]
    if isinstance(data, dict):
        items = [(str(k), v) for k, v in data.items()]
    elif isinstance(data, list):
        items = [(f"lamp{i}", v) for i, v in enumerate(data)]
    else:
        raise ValueError("Ожидался список устройств или словарь")
    lamps = [lamp for name, raw in items if isinstance(raw, dict) and (lamp := _lamp(raw, name))]
    return lamps


def load(path: Path = DEFAULT_PATH) -> list[Lamp]:
    return parse(json.loads(path.read_text(encoding="utf-8")))


def select(lamps: list[Lamp], wanted: list[str]) -> list[Lamp]:
    """Лампы по имени, IP или id, в указанном порядке. Пустой список — все с IP."""
    if not wanted:
        return lamps
    out = []
    for w in wanted:
        found = [x for x in lamps if w.lower() in (x.name.lower(), x.ip, x.id)]
        if not found:
            raise ValueError(f"Лампа {w!r} не найдена; есть: {', '.join(x.name for x in lamps)}")
        out.extend(found)
    return out
