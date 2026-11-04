import json
from typing import Any

from bayun_voice import audio


def test_ducks_others_and_restores(monkeypatch: Any) -> None:
    calls: list[tuple[str, ...]] = []
    listing = [
        {
            "index": 7,
            "properties": {"application.name": "Chromium"},
            "volume": {"front-left": {"value_percent": "80%"}},
        },
        {"index": 9, "properties": {"application.name": "bayun-voice"}, "volume": {}},
    ]

    def pactl(*args: str) -> str:
        calls.append(args)
        return json.dumps(listing) if "list" in args else ""

    monkeypatch.setattr(audio, "_pactl", pactl)
    ducker = audio.Ducker(0.2)
    ducker.duck()
    ducker.restore()
    assert calls[1:] == [
        ("set-sink-input-volume", "7", "20%"),
        ("set-sink-input-volume", "7", "80%"),
    ]
