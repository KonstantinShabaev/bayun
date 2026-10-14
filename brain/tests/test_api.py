from bayun_brain.api import create_app
from fastapi.testclient import TestClient


def test_health() -> None:
    client = TestClient(create_app())
    assert client.get("/health").json()["status"] == "ok"


def test_converse_without_hint_treats_speaker_as_guest() -> None:
    client = TestClient(create_app())
    response = client.post("/converse", json={"text": "включи свет"})
    assert response.status_code == 200
    assert not response.json()["speech"].startswith("masha")


def test_converse_uses_fresh_speaker_hint() -> None:
    client = TestClient(create_app())
    client.post("/speaker-hint", json={"speaker": "masha", "confidence": 0.93})
    speech = client.post("/converse", json={"text": "включи свет"}).json()["speech"]
    assert speech.startswith("masha, ")
