# Баюн — заметки для разработки

- Монорепо на uv workspace: `stt/` (bayun-stt), `brain/` (bayun-brain), `bench/` (bayun-bench). HA-интеграция в `ha_integration/` вне workspace.
- Проверки: `uv sync`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest`.
- План и критерии готовности: `docs/ROADMAP.md`; архитектура: `docs/PROJECT_SPEC.md`.
- Тексты для пользователя, комментарии и документация — по-русски.
- Связка с HA тонкая, логика в своих сервисах. Тяжёлые модели (GigaAM, faster-whisper, LiteLLM) — только как optional extras.
