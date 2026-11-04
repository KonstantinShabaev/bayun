"""Отправка кадров лампам: напрямую по Tuya 3.x (tinytuya) или в консоль."""

import logging
import time
from typing import Any, Protocol

from bayun_lights.color import HSV, encode_dp24, encode_dp28
from bayun_lights.devices import Lamp

log = logging.getLogger(__name__)

# DP ламп Tuya v2: 20 — вкл, 21 — режим, 22 — яркость белого, 23 — температура,
# 24 — цвет, 25 — сцена, 28 — управляющие данные (без записи во флеш).
STATE_DPS = ("20", "21", "22", "23", "24")


def restore_steps(saved: dict[str, Any]) -> list[dict[str, Any]]:
    """Как вернуть лампу в сохранённое состояние.

    Запись DP 22/23 (яркость и температура белого) переключает лампу в white,
    поэтому для цветного режима их не трогаем, а режим и цвет шлём дважды:
    сразу после потока DP 28 лампа может проглотить первую команду.
    """
    mode = saved.get("21")
    keys = ("21", "24") if mode == "colour" else ("21", "22", "23")
    state = {k: saved[k] for k in keys if k in saved}
    first = {k: v for k, v in saved.items() if k == "20"} | state
    if saved.get("20") is False:
        return [state, {"20": False}]
    return [first, state]


class Sink(Protocol):
    def start(self) -> None: ...
    def send(self, color: HSV, *, smooth: bool = False) -> None: ...
    def stop(self) -> None: ...


class PrintSink:
    def __init__(self, lamp: Lamp) -> None:
        self.lamp = lamp

    def start(self) -> None:
        print(f"[{self.lamp.name}] start")

    def send(self, color: HSV, *, smooth: bool = False) -> None:
        print(f"[{self.lamp.name}] dp28={encode_dp28(color, smooth=smooth)}")

    def stop(self) -> None:
        print(f"[{self.lamp.name}] stop")


class TuyaSink:
    """Одно постоянное соединение на лампу, кадры без ожидания ответа.

    Лампа держит только одно локальное соединение, поэтому пока работает
    эффект, Tuya Local в HA с ней не поговорит. После остановки
    возвращаем лампе то состояние, что было до старта.
    """

    def __init__(self, lamp: Lamp) -> None:
        try:
            import tinytuya
        except ImportError as e:
            raise SystemExit(
                "Нужен tinytuya: uv sync --extra tuya (или pip install tinytuya)"
            ) from e
        self.lamp = lamp
        self._dev: Any = tinytuya.BulbDevice(lamp.id, lamp.ip, lamp.key, version=lamp.version)
        self._dev.set_socketPersistent(True)
        self._saved: dict[str, Any] = {}

    def start(self) -> None:
        status = self._dev.status()
        dps = status.get("dps", {}) if isinstance(status, dict) else {}
        if not dps:
            raise ConnectionError(f"{self.lamp.name} ({self.lamp.ip}) не ответила: {status}")
        self._saved = {k: dps[k] for k in STATE_DPS if k in dps}
        self._dev.set_multiple_values({"20": True, "21": "colour"}, nowait=True)

    def send(self, color: HSV, *, smooth: bool = False) -> None:
        self._dev.set_value(28, encode_dp28(color, smooth=smooth), nowait=True)

    def stop(self) -> None:
        if not self._saved:
            return
        try:
            for dps in restore_steps(self._saved):
                self._dev.set_multiple_values(dps, nowait=False)
                time.sleep(0.5)
        except Exception:
            log.exception("Не удалось вернуть состояние %s", self.lamp.name)
        finally:
            self._dev.close()

    @staticmethod
    def static_color(color: HSV) -> dict[str, Any]:
        """DPS для постоянного цвета (запишется в память лампы)."""
        return {"20": True, "21": "colour", "24": encode_dp24(color)}
