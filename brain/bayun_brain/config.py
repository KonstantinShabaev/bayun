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
    # Если основная модель перегружена или недоступна.
    fallbacks: list[str] = []
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
    # Или файл с токеном (права 600), чтобы не держать его в конфиге.
    token_file: Path | None = None

    def resolved_token(self) -> str:
        if self.token:
            return self.token
        if self.token_file and self.token_file.expanduser().exists():
            return self.token_file.expanduser().read_text(encoding="utf-8").strip()
        return ""


class VideoConfig(BaseModel):
    # tv-agent (dev/tv): киоск Chromium и пульт ТВ. Пусто — телевизора нет.
    agent_url: str = ""


class CodeConfig(BaseModel):
    """Задачи Claude Code голосом (docs/claude-code-voice.md). Пусто — выключено."""

    # Как проект называют голосом → рабочий клон. Не тот клон, из которого работает Баюн.
    projects: dict[str, str] = {}
    model: str | None = None
    max_turns: int = 60
    max_budget_usd: float | None = None
    push_branch_prefix: str = "claude/voice-"
    # Push в свою ветку без вопроса: включать, только когда main защищён ruleset в GitHub.
    push_without_asking: bool = False
    # Сколько ждать ответа на вопрос или разрешение, потом «нет».
    answer_timeout_min: float = 120
    # С каких устройств можно поручать задачи и подтверждать (до speaker ID, M3).
    allowed_devices: list[str] = ["server"]


class Config(BaseModel):
    llm: LlmConfig = LlmConfig()
    assistant: AssistantConfig = AssistantConfig()
    home_assistant: HomeAssistantConfig = HomeAssistantConfig()
    video: VideoConfig = VideoConfig()
    code: CodeConfig = CodeConfig()
    database: Path = Path("bayun.db")
    speaker_hint_window_s: float = 3.0


def load_config(path: Path | None) -> Config:
    if path is None:
        return Config()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)
