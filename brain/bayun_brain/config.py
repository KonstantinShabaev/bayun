"""Конфигурация brain: config.yaml с валидацией."""

from pathlib import Path

import yaml
from pydantic import BaseModel


class LlmConfig(BaseModel):
    main: str = "openai/gpt-4o-mini"
    vision: str | None = None
    api_base: str | None = None
    # Ключ шлюза (LITELLM_MASTER_KEY). Без шлюза LiteLLM берёт ключ провайдера из окружения.
    api_key: str | None = None
    max_tokens: int = 400
    timeout_s: float = 20.0


class AssistantConfig(BaseModel):
    # Домашнее имя: то, на что откликается и как себя называет (см. architecture §7г).
    name: str = "Баюн"
    # Сколько последних реплик держать в рабочей памяти одного разговора.
    history_turns: int = 10


class HomeAssistantConfig(BaseModel):
    url: str = "http://homeassistant.local:8123"
    token: str = ""


class Config(BaseModel):
    llm: LlmConfig = LlmConfig()
    assistant: AssistantConfig = AssistantConfig()
    home_assistant: HomeAssistantConfig = HomeAssistantConfig()
    database: Path = Path("bayun.db")
    speaker_hint_window_s: float = 3.0


def load_config(path: Path | None) -> Config:
    if path is None:
        return Config()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)
