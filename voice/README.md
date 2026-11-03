# voice

bayun-voice — голосовой цикл прямо на сервере с USB-микрофоном, без Home Assistant:

```
микрофон (PipeWire) ─► openWakeWord ─► Silero VAD ─► STT (faster-whisper, потом GigaAM)
   ─► bayun-brain /converse/stream ─► Piper по предложениям ─► динамики
```

Нужен, чтобы слушать и мерить задержки, пока нет ВМ с HA и колонок. Потом его место займёт
сателлит linux-voice-assistant + пайплайн HA (deploy/server, этап 3), а отсюда останутся
движки и страница состояния.

- Слушает только после имени. Пока своей модели «Волтер» нет — встроенная «hey jarvis».
- Вместо имени можно нажать «Говорить» на странице состояния `http://<сервер>:8770`.
- Половинный дуплекс: пока говорит, не слушает. Перебивание — после эхоподавления.
- После ответа ждёт продолжения разговора 5 с без имени.

## Запуск на сервере

```sh
uv sync --extra local --package bayun-voice --package bayun-brain --extra llm
uv run python -m piper.download_voices --download-dir voices ru_RU-dmitri-medium
cp voice/config.example.yaml voice/config.yaml
uv run bayun-brain --config brain/config.yaml &       # или --no-llm без ключа
uv run bayun-voice --config voice/config.yaml
```

Сервисами пользователя (`systemctl --user`): `deploy/voice-dev/`.
