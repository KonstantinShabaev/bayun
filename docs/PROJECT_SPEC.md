# Баюн — русский голос и агентный «мозг» для Home Assistant

> Проект переименован из «Домового» в «Баюна» (кот-сказочник, который говорит и убаюкивает). Open source (Apache-2.0). Не новая платформа умного дома, а два модуля поверх Home Assistant.

---

## 1. Зачем

Алиса закрыта: нельзя выбрать модель, подключить свой сервер, свои камеры и датчики, и данные уходят в облако.
Home Assistant (HA) решает платформенную часть: устройства, ESPHome, Zigbee, камеры через Frigate, локальность.
Но у HA два слабых места для русскоязычного пользователя:

1. **Русский голос** заметно хуже английского: распознавание, нормализация чисел, wake word.
2. **LLM в HA реактивная**: без памяти, без понимания, кто говорит, без проактивности, без «зрения».

Баюн закрывает именно эти две дыры.

### Цели
- Русскоязычные голосовые команды распознаются точно и быстро, и это подтверждено бенчмарком.
- Пользователь сам выбирает LLM: облако, свой сервер или локальную модель.
- Дом помнит людей, их предпочтения и договорённости.
- Опасные действия защищены: права по людям, подтверждение, журнал.
- Все данные хранятся на домашнем сервере пользователя.

### Не цели (сознательно)
- Своя платформа умного дома, свои интеграции устройств, форк HA.
- Своё железо колонки (используем Voice PE / ESP32-S3-BOX / RPi + ReSpeaker).
- Музыка, контент, навыки в духе Алисы.
- Монетизация на старте.

---

## 2. Архитектура

```
[Сателлит: HA Voice PE / ESP32-S3-BOX-3 / RPi + ReSpeaker]
   │ wake word на устройстве (microWakeWord)
   ▼
[Home Assistant — Assist pipeline]
   │ аудио (Wyoming)
   ▼
┌──────────────────────────────┐
│ bayun-stt  (Wyoming STT)   │── speaker hint ──┐
│  GigaAM + нормализация       │                  │
│  + speaker ID (эмбеддинги)   │                  │
└──────────────────────────────┘                  │
   │ текст                                        ▼
   ▼                                   ┌──────────────────────────┐
[HA local intents] ─ не понял ───────► │ bayun-brain (сервис)   │
   │ понял (быстрый путь)              │  LLM-роутер (LiteLLM)    │
   │                                   │  инструменты: MCP HA     │
   │                                   │  память (SQLite+vec)     │
   │                                   │  политика безопасности   │
   │                                   │  проактивность, Frigate  │
   │                                   └──────────────────────────┘
   ▼                                              │ ответ
[Wyoming TTS: Piper-ru] ◄─────────────────────────┘
   ▼
[Сателлит говорит]
```

Принцип: **связка с HA тонкая, вся логика в своих сервисах.** API HA меняются часто, поэтому чем меньше кода внутри HA, тем проще поддержка.

---

## 3. Структура репозитория (монорепо)

```
bayun/
├── README.md
├── LICENSE                      # Apache-2.0
├── docs/
│   ├── architecture.md
│   ├── setup.md                 # как поднять у себя
│   └── security.md              # модель угроз
├── stt/                         # bayun-stt — Wyoming STT-сервер
│   ├── bayun_stt/
│   │   ├── __main__.py          # запуск Wyoming-сервера
│   │   ├── handler.py           # обработка Wyoming-событий
│   │   ├── asr.py               # обёртка GigaAM (+ fallback Whisper)
│   │   ├── normalize.py         # «двадцать два градуса» → «22 градуса»
│   │   └── speaker.py           # эмбеддинги, сравнение с профилями
│   ├── Dockerfile
│   └── pyproject.toml
├── brain/                       # bayun-brain — агентный сервис
│   ├── bayun_brain/
│   │   ├── api.py               # FastAPI: /converse, /speaker-hint, /health
│   │   ├── agent.py             # цикл LLM ↔ инструменты
│   │   ├── llm.py               # LiteLLM, выбор модели из конфига
│   │   ├── tools/
│   │   │   ├── ha_mcp.py        # клиент MCP-сервера HA
│   │   │   ├── memory.py        # remember / recall / forget
│   │   │   └── cameras.py       # «что видно на камере X»
│   │   ├── memory/
│   │   │   ├── store.py         # SQLite + sqlite-vec
│   │   │   └── schema.sql
│   │   ├── policy.py            # уровни действий, права, подтверждения
│   │   ├── proactive.py         # подписка на события HA, уведомления
│   │   └── frigate.py           # MQTT-события Frigate → VLM → память
│   ├── config.example.yaml
│   ├── Dockerfile
│   └── pyproject.toml
├── ha_integration/
│   └── custom_components/bayun/   # тонкий conversation agent для HA
│       ├── __init__.py
│       ├── manifest.json
│       ├── config_flow.py
│       └── conversation.py      # пересылает текст в brain /converse
├── addons/                      # обёртки HA add-on для stt и brain
├── bench/                       # bayun-bench — датасет и замеры
│   ├── commands.csv             # id, текст-эталон, путь к wav, говорящий, шум
│   ├── audio/                   # (не в git, или через git-lfs)
│   ├── run_wer.py               # WER/CER + латентность по движкам
│   └── results/
└── docker-compose.dev.yml       # HA + stt + brain + piper + mosquitto для разработки
```

---

## 4. Компоненты подробно

### 4.1 bayun-stt

- **Протокол:** Wyoming (Python-пакет `wyoming`). Обработка событий: `Describe` → `Info`, затем `Transcribe` / `AudioStart` / `AudioChunk` / `AudioStop` → `Transcript`.
- **ASR:** GigaAM (`salute-developers/GigaAM`). Актуальные имена моделей смотреть в репозитории. CTC-вариант быстрее, RNNT точнее, выбор задаётся в конфиге. Fallback: faster-whisper.
- **Нормализация** (`normalize.py`): числа, проценты, градусы, время («в полвосьмого» → 07:30), убрать слова-паразиты. Покрыть тестами из датасета.
- **Speaker ID** (`speaker.py`): эмбеддинг (ECAPA из SpeechBrain или WeSpeaker) по тому же аудио, cosine-сравнение с профилями, порог задаётся в конфиге. Регистрация голоса: CLI `bayun-stt enroll --name masha *.wav`.
- **Передача говорящего в brain (MVP, боковой канал):** после распознавания `POST brain:/speaker-hint {speaker, confidence, ts}`. Brain сопоставляет подсказку с ближайшим запросом `/converse` по времени (окно ~3 с).
  *Ограничение:* Wyoming STT не знает, с какого сателлита пришло аудио, поэтому при одновременной речи в двух комнатах возможна путаница. Долгосрочное решение: предложить в HA upstream передачу метаданных говорящего через пайплайн.
- **Конфиг:** модель, устройство (cpu/cuda), порог speaker ID, адрес brain.

### 4.2 bayun-brain

**API**
- `POST /converse` — `{text, conversation_id, device_id, language}` → `{speech, continue_conversation}`
- `POST /speaker-hint` — от STT
- `GET /health`

**Цикл агента**
1. Определить говорящего (подсказка из STT, иначе «гость») и загрузить его права.
2. Собрать контекст: системный промпт, релевантные факты и предпочтения из памяти (векторный поиск + по говорящему), краткое состояние дома.
3. Вызвать LLM с инструментами. Каждый вызов инструмента проходит через `policy.py`.
4. Сохранить эпизод и, если LLM вызвала `remember`, новые факты.
5. Вернуть короткий ответ для TTS: без markdown, 1–2 фразы.

**LLM:** LiteLLM. Модель задаётся в конфиге, можно указать отдельную модель для зрения:
```yaml
llm:
  main: "openai/gpt-..."            # или "anthropic/claude-...", "ollama/qwen..."
  vision: "ollama/qwen2.5vl"        # для Frigate-снапшотов
  api_base: null                    # свой OpenAI-совместимый сервер
```

**Инструменты**
- HA через MCP-сервер HA (интеграция «Model Context Protocol Server», доступ по long-lived token). Конкретный транспорт и URL проверить в документации HA.
- `memory.remember(fact, about, kind)`, `memory.recall(query)`, `memory.forget(id)`
- `cameras.describe(camera, question)` — снапшот → VLM
- `timer`, `reminder` (через сущности HA или свои)

**Память (`schema.sql`)**
```sql
CREATE TABLE people (
  id TEXT PRIMARY KEY,          -- 'masha'
  display_name TEXT,
  role TEXT,                    -- adult | child | guest
  voice_profile BLOB
);
CREATE TABLE facts (
  id INTEGER PRIMARY KEY,
  kind TEXT,                    -- fact | preference | episode
  about TEXT,                   -- people.id или 'home'
  text TEXT,                    -- «Маша спит во второй спальне»
  source TEXT,                  -- voice | camera | manual
  created_at TEXT,
  expires_at TEXT               -- для эпизодов: «уехали до воскресенья»
);
-- + виртуальная таблица sqlite-vec с эмбеддингами facts.text
CREATE TABLE audit_log (
  ts TEXT, speaker TEXT, tool TEXT, args TEXT,
  decision TEXT,                -- allowed | confirmed | denied
  result TEXT
);
```

**Политика безопасности (`policy.yaml`)**
```yaml
levels:
  safe:     [light.*, media_player.*, cover.curtain_*]
  confirm:  [lock.*, climate.*, cover.garage*, alarm_control_panel.*]
  deny:     [switch.gas_*, homeassistant.restart]
roles:
  adult: { safe: allow, confirm: ask }
  child: { safe: allow, confirm: deny }
  guest: { safe: allow, confirm: deny }
untrusted_sources: [camera, web]   # текст из этих источников не может инициировать действия
```
Подтверждение: brain отвечает «Открыть замок? Скажите „да“» и ставит `continue_conversation: true`. Действие выполняется только после подтверждения от того же говорящего.

**Проактивность (`proactive.py`)**
- Подписка на websocket событий HA.
- Сначала простые правила (утюг включён > 1 ч и никого нет дома), LLM только для формулировки и для сложных случаев.
- Доставка: `assist_satellite.announce` на сателлит в комнате или Telegram.
- Ограничение частоты и «тихие часы» обязательны.

**Frigate (`frigate.py`)**
- MQTT `frigate/events` → снапшот → VLM-описание → `facts` (kind=episode, source=camera, с TTL).
- Описания с камер помечаются как недоверенные (prompt injection через надписи в кадре).

### 4.3 ha_integration (custom component)

- Регистрирует `ConversationEntity`, в `async_process` пересылает текст и `device_id` в brain и возвращает ответ.
- Config flow: URL brain и токен.
- В пайплайне Assist включить «сначала локальные команды», тогда простые команды не доходят до LLM.
- Распространение через HACS.

### 4.4 bayun-bench

- `commands.csv`: 200–500 команд × несколько говорящих (взрослые, ребёнок, пожилой) × условия (тишина, ТВ, кухня).
- `run_wer.py`: WER/CER и латентность для `whisper`, `gigaam-ctc`, `gigaam-rnnt`, до и после нормализации.
- Результаты публикуются в README. Это главный аргумент проекта.

---

## 5. Стек

| Что | Чем |
|---|---|
| Язык | Python 3.12+, `uv` для зависимостей |
| Wyoming | `wyoming` |
| ASR | GigaAM, faster-whisper (fallback) |
| Speaker ID | SpeechBrain ECAPA / WeSpeaker |
| TTS | Piper (русские голоса), опционально Silero (*лицензия CC BY-NC, только для некоммерческого использования*) |
| Wake word | microWakeWord (на сателлите) |
| LLM | LiteLLM → любой провайдер / Ollama / vLLM |
| Сервис | FastAPI, uvicorn |
| Память | SQLite + sqlite-vec, эмбеддинги — мультиязычная модель (например, e5 / bge-m3) |
| MQTT | aiomqtt, Mosquitto |
| Тесты | pytest, ruff, mypy |
| Поставка | Docker, HA add-ons, HACS |

**Железо для разработки:** мини-ПК N100 / 16 ГБ (HA OS или Proxmox), один сателлит (HA Voice PE или ESP32-S3-BOX-3). Для локальной LLM опционально: машина с GPU или Mac mini.

---

## 6. Этапы и критерии готовности

| # | Срок | Этап | Готово, когда |
|---|---|---|---|
| M0 | нед. 1 | Стенд: HA + сателлит + штатный Whisper-ru | Есть замер WER и латентности на 50 своих командах |
| M1 | нед. 2–3 | `bayun-stt` с GigaAM + нормализация + бенчмарк | Таблица Whisper vs GigaAM опубликована. STT ≤ 0,6 с на N100 для короткой команды. Первый релиз |
| M2 | нед. 4–6 | `bayun-brain` + custom component + MCP + память фактов | «Запомни, что Маша спит во второй спальне» → «выключи свет у Маши» работает |
| M3 | нед. 7–8 | Политика безопасности + журнал | Замок без подтверждения не открывается, ребёнку отказ, всё видно в audit_log |
| M4 | нед. 9–10 | Speaker ID | ≥ 90 % правильной идентификации в семье из 3–4 человек, права работают по голосу |
| M5 | нед. 11+ | Проактивность, Frigate + VLM, свой русский wake word | «Что в холодильнике?» отвечает по последнему снапшоту, напоминания приходят сами |

**Бюджет задержки:** простая команда ≤ 1 с (быстрый путь), команда через LLM ≤ 2,5 с от конца фразы до начала ответа.

---

## 7. Риски

| Риск | Что делать |
|---|---|
| API HA ломается между версиями | Тонкая интеграция, CI против актуальной версии HA |
| Локальная LLM на N100 слишком слабая для агента | По умолчанию облако или свой сервер, честные требования в README |
| Ложные срабатывания и лишние подтверждения раздражают | Тестировать на семье, настраиваемые уровни |
| Prompt injection через камеры и внешние данные | Недоверенные источники не могут вызывать действия (см. policy) |
| Лицензии моделей | Проверять перед включением, некоммерческое держать опцией |
| Конкуренция с самим HA (они улучшат русский) | Хорошо: отдавать наработки upstream, наша ценность в «мозге» |

---

## 8. Первые задачи (чек-лист)

- [ ] Создать репозиторий `bayun` с этой структурой, лицензией, README и этим документом в `docs/`
- [ ] Поднять HA на N100, подключить сателлит, включить штатный Whisper + Piper-ru
- [ ] Записать 50 команд (2–3 голоса, тишина + ТВ), заполнить `bench/commands.csv`
- [ ] Поискать на GitHub готовые `wyoming gigaam`: дорабатывать чужое, а не писать с нуля
- [ ] `stt/`: минимальный Wyoming-сервер с GigaAM, подключить в HA
- [ ] `bench/run_wer.py`: сравнить Whisper и GigaAM, закоммитить результаты
- [ ] `normalize.py` + тесты на числа и время
- [ ] Написать в русский Telegram-чат и на форум HA, найти 5–10 первых тестеров
