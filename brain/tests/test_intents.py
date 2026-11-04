import pytest
from bayun_brain.intents import match


@pytest.mark.parametrize(
    ("text", "tool", "args"),
    [
        ("Джарвис, пауза.", "control_video", {"action": "pause"}),
        ("Поставь на паузу", "control_video", {"action": "pause"}),
        ("Продолжи.", "control_video", {"action": "play"}),
        ("Перемотай на 30 секунд вперёд", "control_video", {"action": "seek", "seconds": 30}),
        ("Назад на две минуты", "control_video", {"action": "seek", "seconds": -120}),
        ("перемотай вперед", "control_video", {"action": "seek", "seconds": 30}),
        ("Джарс, погромче.", "set_volume", {"direction": "up", "steps": 3}),
        ("Сделай тише на пять", "set_volume", {"direction": "down", "steps": 5}),
        ("Следующее видео", "control_video", {"action": "next"}),
        ("Выключи видео", "stop_video", {}),
        ("Включи звук в видео", "control_video", {"action": "volume", "level_pct": 100}),
        ("Включи видео про космос", "play_video", {"query": "космос"}),
        ("Джарвис, включи на ютубе ролик с котиками", "play_video", {"query": "котиками"}),
    ],
)
def test_fast_commands(text: str, tool: str, args: dict[str, object]) -> None:
    intent = match(text)
    assert intent is not None and (intent.tool, intent.args) == (tool, args)


@pytest.mark.parametrize(
    "text",
    ["Какая погода завтра?", "Включи свет на кухне", "Расскажи анекдот", "Сделай громче лампу"],
)
def test_other_phrases_go_to_llm(text: str) -> None:
    assert match(text) is None
