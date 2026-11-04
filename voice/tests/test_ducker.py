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


def test_recover_resets_left_ducked(monkeypatch: Any) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(audio, "_other_sink_inputs", lambda: [("3", "20%"), ("4", "60%")])
    monkeypatch.setattr(audio, "_pactl", lambda *args: calls.append(args) or "")
    audio.Ducker(0.2).recover()
    assert calls == [("set-sink-input-volume", "3", "100%")]


def test_player_ducker_restores_previous_volume(tmp_path: Any) -> None:
    import sys

    log = tmp_path / "calls"
    script = (
        f"import sys; a=sys.argv[1:]; open({str(log)!r},'a').write(' '.join(a)+'\\n');"
        "print('{\"playing\": true, \"volume\": 70}' if a==['--status'] else '')"
    )
    ducker = audio.PlayerDucker([sys.executable, "-c", script], 0.2)
    ducker.duck()
    ducker.restore()
    assert log.read_text().split("\n")[:3] == ["--status", "--volume 14", "--volume 70"]
