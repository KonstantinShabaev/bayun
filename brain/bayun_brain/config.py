"""Конфигурация brain: config.yaml с валидацией."""

from pathlib import Path

import yaml
from pydantic import BaseModel


class LlmConfig(BaseModel):
    main: str = "openai/gpt-4o-mini"
    vision: str | None = None
    api_base: str | None = None


class HomeAssistantConfig(BaseModel):
    url: str = "http://homeassistant.local:8123"
    token: str = ""


class Config(BaseModel):
    llm: LlmConfig = LlmConfig()
    home_assistant: HomeAssistantConfig = HomeAssistantConfig()
    database: Path = Path("bayun.db")
    speaker_hint_window_s: float = 3.0


def load_config(path: Path | None) -> Config:
    if path is None:
        return Config()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)
