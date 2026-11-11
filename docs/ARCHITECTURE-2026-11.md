# Баюн как продукт: целевая архитектура

Дата: 11.11.2026. Автор: архитектор (агент). Основано на BRIEF.md, коде ветки `claude/project-thread-sjtqda` (voice/, brain/, dev/tv/, deploy/voice-dev/), docs/ и исследовании рынка. В репозитории и на ноуте ничего не менялось.

---

## 0. Ключевые решения (коротко)

| # | Решение | Почему |
|---|---|---|
| 1 | **Уши переезжают к дивану, а не стоят у ТВ.** Микрофон рядом с человеком и далеко от динамиков ТВ — это 80 % «уровня Алисы». Сегодня fifine стоит у ноута у ТВ: максимально далеко от Кости и максимально близко к шуму. | Любой far-field массив на расстоянии 0,5–1,5 м от говорящего работает лучше, чем идеальный массив в 4 м у источника шума. |
| 2 | **Один XMOS-массив как USB-звуковая карта ноута: ReSpeaker XVF3800 (USB), через него же выходит весь звук (фильм + голос Баюна) на активные колонки/саундбар.** Динамики ТВ выключаем. | Так эхоподавление XMOS получает честный опорный сигнал всего, что играет, — ровно как у Станции/Алисы, которая слышит поверх собственной музыки. Это единственный способ слышать команду поверх громкого фильма. ESPHome/HA-пайплайн для этого не нужен: плата просто становится `parec`-источником в PipeWire. |
| 3 | **Своё wake word «Волтэр» (microWakeWord, обучение в Colab, бесплатно) + подтверждение STT.** Двухступенчато: модель даёт дальность и устойчивость к шуму, GigaAM по тому же куску отсекает ложные срабатывания. Текущий `SttWake` остаётся как запасной движок. | `SttWake` распознаёт каждый кусок речи, включая речь из ТВ: при фильме это постоянная нагрузка и ложные «Волтэр»/пропуски. Wake-модель обучается с шумом и реверберацией специально для дальнего поля. Русский прецедент («Ирина») показал 100 % recall на реальных записях. |
| 4 | **Второй канал управления — PWA «пульт Баюна» на телефоне** (кнопка «говорить» + текст + карточки фильмов с постерами + плеер), и **Telegram-бот** как третий (голосовые сообщения → GigaAM, те же инструменты brain). | Голос не должен быть единственным входом: ночью, при гостях, при шумном фильме удобнее ткнуть в телефон. Выбор фильма «по номеру в браузере» заменяется карточками на телефоне и на ТВ. |
| 5 | **Видео — в нативных приложениях ТВ** (SmartTube/YouTube, VK Видео, Кинопоиск) через deep links по Android TV Remote, **ноут остаётся «экраном Баюна»** на своём HDMI-входе, а **поверх любых приложений ТВ показываем оверлей через PiPup/«Notifications for Android TV»** (сторонний APK на ТВ, HTTP-API). Chromium-киоск — запасной путь для сайтов без приложений. | Приложения ТВ дают 4K/HDR, аппаратный декодер, без курсора и без «качество не выбирается»; SmartTube — без рекламы. Оверлей на ТВ без ноута возможен: PiPup рисует popup поверх любого приложения. |
| 6 | **Голос ответа: Yandex SpeechKit v3 (`kirill`/`anton`/`marina` с ролями) как основной, Silero v5 локально как офлайн-запас и для «Готово/Угу».** | Единственный доступный из РФ облачный TTS с эмоциями и русской просодией; ~0,16 ₽ за фразу до 250 символов, то есть десятки рублей в месяц. Silero остаётся без интернета. |
| 7 | **Качество = наблюдаемость.** Один журнал «фраза → wake → STT → brain → TTS → действие» с таймингами и аудио-кусками для разбора, страница «что случилось» на телефоне. | «Микробагов очень много» лечится не по одному, а тем, что каждый случай можно посмотреть за 10 секунд. |

---

## 1. Диагноз: почему сейчас неудобно

Привязка к коду и железу:

1. **Микрофон стоит в худшей точке.** `voice/bayun_voice/audio.py` читает `parec` с устройства по умолчанию (USB fifine у ноута, ноут у ТВ). Расстояние до говорящего 3–4 м, до динамиков ТВ — 0,5 м. SNR на диване заведомо отрицательный при работающем ТВ.
2. **Эхоподавление не включено.** В `deploy/voice-dev/60-bayun-echo-cancel.conf` есть WebRTC AEC по монитору выхода, но по `config.example.yaml` `audio.input_device: null`, то есть сырой микрофон. При звуке фильма через HDMI → динамики ТВ опорный сигнал всё равно запаздывает на обработку ТВ (десятки–сотни мс) — WebRTC с `delay_agnostic` это тянет, XMOS тянет лучше, потому что звук идёт через него.
3. **Wake word через STT (`voice/bayun_voice/wake.py: SttWake`)** — умный костыль: любое слово без обучения, команда в одной фразе. Цена: GigaAM распознаёт каждый VAD-кусок, включая речь из ТВ (6 с кусок при непрерывной речи), усиление ×30 поднимает шум вместе с голосом, имя ищется по расстоянию Левенштейна среди 11 написаний. На 1–2 м работает, дальше — лотерея; журнал (`agents/brain_voice.log`) показывает «Микрофон присылает тишину» (fifine с кнопкой mute) и «Речи так и не услышал» после имени.
4. **Видео в Chromium-киоске (`dev/tv/bayun_tv/agent.py`)**: YouTube/VK в браузере, качество решает сайт (VP9 1080p без аппаратного 4K через VAAPI на Iris Xe — не гарантировано), курсор Wayland не прячется из приложения, реклама — uBlock + скрипт `YOUTUBE_PAGE_JS` (перематывал фильм, уже чинили), выбор фильма — «номера на экране». HDMI после сна выбирает 4096×2160 (кино-4K из EDID ТВ) → «нет сигнала».
5. **Один канал ввода — голос.** Страница :8770 и веб-пульт `bayun-remote-ui` есть, но не являются полноценным интерфейсом.
6. **Нет сквозного журнала события**: тайминги пишутся в `status`, но разбор «почему не услышал» требует `journalctl`.

---

## 2. Целевая картина: как этим живут каждый день

- **Диван.** На журнальном столике/подлокотнике стоит маленькое «ухо» с LED-кольцом (ReSpeaker XVF3800 в корпусе). Говоришь «Волтэр, поставь паузу» обычным голосом при работающем фильме — кольцо зажигается, фильм ставится на паузу, ответ «Пауза» звучит из тех же колонок. Фильм приглушается, пока Баюн слушает.
- **Фильм.** «Волтэр, найди „Дюна 2“» → на ТВ поверх текущего приложения всплывают 3–5 карточек с постерами и номерами (PiPup), те же карточки — в PWA на телефоне. Говоришь «вторую» или тапаешь на телефоне → открывается Кинопоиск/VK Видео/SmartTube сразу на фильме, 4K, без курсора и без рекламы.
- **Телефон.** PWA «Баюн»: большая кнопка «говорить» (держать), поле текста, последние ответы, плеер (пауза/перемотка/громкость/стоп), свет, таймеры, «что помнит». Работает в LAN; снаружи — Telegram-бот с теми же функциями.
- **Без имени.** Пульт ТВ остаётся для «тупых» действий (громкость), и Баюн их видит (`androidtvremote2` даёт `current_app`, громкость), чтобы не спорить с пультом.
- **Экран Баюна** (часы, погода, таймеры, лента ответов) — HDMI-вход ноута, на который ТВ переключается, когда видео не играет; во время видео Баюн общается оверлеем и голосом.
- **Когда что-то не так** — на телефоне страница «последние 20 фраз»: что услышал, что распознал, сколько заняло, кнопка «это было неправильно» (сохраняет аудио-кусок для разбора).

---

## 3. Слух издалека

### 3.1 Что определяет дальность
Три фактора в порядке важности: **расстояние до рта и до шума**, **эхоподавление с опорным сигналом** (чтобы собственный звук не считался речью), **устойчивая wake-модель** (обучена с шумом/ревербом). Алиса выигрывает потому, что (а) Станция стоит рядом с человеком, (б) проигрывает звук сама и вычитает его, (в) wake word — специально обученная модель. Ни один пункт не про «качество микрофона».

### 3.2 Варианты железа (цены за рубежом подтверждены, в РФ — оценка через Ozon/AliExpress-реселлеров, проверить перед покупкой)

| Вариант | Что это | Цена | Плюсы | Минусы | Вердикт |
|---|---|---|---|---|---|
| **ReSpeaker XVF3800 USB (4 мик., XMOS)** | USB-звуковая карта UAC2: 4 PDM-мика, AEC/beamforming/NS/DoA в XMOS, 3,5 мм выход + 5 Вт усилитель, 12 LED [[seeed]](https://wiki.seeedstudio.com/respeaker_xvf3800_introduction/) | $50–65 у Seeed/ThePiHut, $56 на AliExpress без корпуса [[cnx]](https://www.cnx-software.com/2025/07/29/respeaker-xmos-xvf3800-4-mic-array-board-features-esp32-s3-module-works-over-usb/); в РФ оценочно 7–11 тыс. ₽ | В сравнительном тесте на 5 м с ТВ и музыкой — лучший из трёх (Voice PE, Satellite1, XVF3800) [[ha-forum]](https://community.home-assistant.io/t/voice-satellite-comparison-ha-voice-pe-vs-futureproofhomes-satellite1-vs-seeed-respeaker-xvf3800/1026509). Как USB-карта встаёт в текущий `parec`-цикл без HA/ESPHome. Звук, проигранный через плату, — опорный сигнал AEC | Нужен USB-кабель до дивана (активный удлинитель 5 м ~600–1000 ₽) или переезд ноута | **Купить первым.** Максимум дальности за минимум переделок |
| ReSpeaker XVF3800 + XIAO ESP32S3 (Wi-Fi сателлит) | То же, но ESPHome-сателлит HA | $55–65 | Без кабеля, LED, кнопка; путь к «комната = сателлит» из docs/architecture.md | Через HA Assist-пайплайн (нужна наша Wyoming-обвязка, `stt/` ещё минимален); звук фильма через плату не прогнать → AEC не видит ТВ | Вторая комната позже |
| Home Assistant Voice PE | 2 мика, XMOS XU316, 3,5 мм выход, ESPHome | $59 (ameridroid $45–59) [[ha-blog]](https://home-assistant.io/blog/2024/12/19/voice-preview-edition-the-era-of-open-voice); в РФ оценочно 8–12 тыс. ₽ | Из коробки в HA | Отзывы: в тихой комнате ~4 м, при ТВ — слабее XVF3800; в тесте проиграл [[ha-forum]](https://community.home-assistant.io/t/voice-satellite-comparison-ha-voice-pe-vs-futureproofhomes-satellite1-vs-seeed-respeaker-xvf3800/1026509) | Нет |
| ReSpeaker Lite (2 мик., XU316) | Плата из satellite/README.md | $25–37 у Seeed | Дёшево, уже в плане | 2 микрофона, слабее при ТВ | Для спальни/кухни позже |
| ESP32-S3-BOX-3 | Экран + 2 мика без XMOS | $75–100 | Экран | Без аппаратного AEC — хуже fifine в шуме | Нет |
| Микрофон пульта Google TV | — | 0 | — | Аудио уходит в Google Assistant, по сети недоступно; протокол Remote v2 умеет **отправлять** голос на ТВ, а не принимать [[androidtvremote2]](https://github.com/tronikos/androidtvremote2) | Нет |
| Телефон/часы как микрофон | PWA (`getUserMedia`, нужен HTTPS в LAN) или HA Companion (Assist, с 2026.3 — wake word на телефоне) [[ha-assist]](https://community.home-assistant.io/t/assist-on-android-with-2026-3-wake-word-only-works-once/994274) | 0 | Идеальная дальность (телефон в руке), нет шума ТВ | Нужно достать телефон | **Делать как второй канал** (раздел 4) |
| Кнопка (Zigbee/BLE) | Кнопка «говорить» на столике | 300–800 ₽ | Без имени, 100 % срабатывание | Всё равно нужен микрофон рядом | Бонус к XVF3800 (кнопка на плате уже есть) |

### 3.3 Программная часть (без покупок, делается сразу)

1. **Включить AEC**: `audio.input_device: "bayun_aec_source"` + конфиг PipeWire из `deploy/voice-dev/` с добавлением `webrtc.delay_agnostic = true` (ТВ задерживает звук непредсказуемо) [[pipewire-issue]](https://gitlab.freedesktop.org/pipewire/pipewire/-/issues/2165). Эффект: перебивание и команды во время ответа Баюна; при фильме — частично.
2. **Передвинуть fifine** на USB-удлинителе к дивану (если есть любой кабель 3 м — сегодня). Эффект: расстояние 3–4 м → 0,5–1,5 м, то есть «как сейчас в упор».
3. **Wake word «Волтэр» (microWakeWord)**: обучение в Colab по готовому ноутбуку [[colab]](https://community.home-assistant.io/t/colab-notebooks-to-train-your-custom-openwakeword-or-microwakeword/1026635), ~20 минут на T4; русский рецепт из модели «Ирина»: голоса Piper `ruslan` + Silero (`xenia`, `kseniya`, `eugene`) + Vosk, ударение по-разному для каждого TTS («Волт+эр» для Silero), SNR −5…+10 дБ (шире не делать), hard negatives («вольт», «Вальтер», «Волга», «альтернатива», «воротник»), выбор лучшего из 10–15 прогонов по реальным записям [[irina]](https://huggingface.co/droman42/microwakeword-irina-ru). На сервере работает через `pymicro-wakeword` (CPU, копейки). В `voice/bayun_voice/wake.py` — новый класс `MicroWakeWord` (как `OpenWakeWord`) плюс режим `wake.engine: "hybrid"`: модель триггерит, STT того же буфера (последние 2 с) подтверждает имя «мягким» порогом; в фразе после имени команда берётся как сейчас (`remainder`). Ожидаемый эффект: уверенные 3–4 м в тишине на fifine, при ТВ — вместе с AEC и переносом микрофона.
4. **Ducking по-настоящему**: при срабатывании имени громкость видео уже уменьшается (`Ducker`); добавить приглушение до 0,1 и паузу плеера, если фраза длиннее 2 с. Эффект: STT команды при фильме почти как в тишине.
5. **Подавление речи ТВ в `SttWake`** (пока он основной): не распознавать куски длиннее 3 с непрерывной речи без паузы (это ТВ), ограничить усиление ×10, в `_is_name` требовать, чтобы имя было в первых двух словах **и** кусок начался с паузы ≥ 0,5 с. Снижает ложные срабатывания и нагрузку.
6. **Диагностика слуха**: сохранять последние N кусков с «почти имя» (`NEAR_MISS`) в `~/.cache/bayun/wake/` (кольцевой буфер 50 файлов) — материал для дообучения модели.

### 3.4 С XVF3800 USB (после покупки)
- Плата = `alsa_input.usb-SEEED_reSpeaker…` (2 канала 16 кГц, канал 0 — обработанный ASR-сигнал) и `alsa_output…` одновременно. В PipeWire: default sink → плата (3,5 мм → активные колонки или саундбар у ТВ; HDMI-аудио на ТВ выключить), default source → плата. В `voice/config.yaml`: `input_device`/`output_device` = плата. Эффект: AEC XMOS вычитает фильм и голос Баюна, beamforming смотрит на говорящего, DoA можно показывать на кольце. Конфиг PipeWire AEC становится не нужен.
- Звук фильма с ноута при видео в нативных приложениях ТВ (раздел 5) идёт из самого ТВ, не через плату → для этого случая опорного сигнала нет, остаются beamforming + NS + перенос микрофона к дивану + ducking через `set_volume` ТВ при имени. Если хочется «как Алиса» при любых приложениях — звук ТВ выводить по оптике/ARC в усилитель, а не в колонки платы; это уже уровень «AV-ресивер», сейчас не нужно.

---

## 4. Управление и интерфейс

### 4.1 Принцип: три равноправных входа в один brain
`brain /converse/stream` уже принимает текст от любого клиента (`lat.py` это показывает). Все каналы ниже — тонкие клиенты одного API, без логики.

| Канал | Что делает | Как |
|---|---|---|
| Голос (уши) | Основной дома | как сейчас |
| **PWA «Баюн» на телефоне** (LAN, `brain` отдаёт статику; HTTPS через самоподписанный сертификат или `mkcert`, иначе микрофон в браузере не даётся) | «Держи и говори» (WebM/Opus → GigaAM), текст, лента диалога, карточки фильмов, плеер, свет, таймеры, память, журнал «что услышал» | Новый `brain/bayun_brain/web/` (FastAPI static + WebSocket), использует уже существующие `/api/dashboard`, `/announcements` |
| **Telegram-бот** | То же вне дома: голосовое → GigaAM; фото/ссылка → «открой на ТВ»; уведомления (таймеры, «Клод спрашивает») | `python-telegram-bot` в brain, allow-list chat_id. Telegram доступен из РФ; brain ходит через тот же VPN при необходимости |
| Пульт ТВ | Громкость, пауза в приложениях | ТВ сам; Баюн только читает состояние |
| Экран Баюна (HDMI ноута) | Витрина: часы, погода, таймеры, ответы, карточки | уже есть `status.html`; дополнить карточками фильмов |
| Оверлей на ТВ | Фраза/результат поверх любого приложения | PiPup HTTP :7979 (раздел 5) |

### 4.2 Выбор фильма без «браузер + цифра»
1. «Найди фильм X» → brain ищет **метаданные**, не страницы: Кинопоиск неофициальный API (`kinopoiskapiunofficial.tech`, бесплатный ключ) или TMDB (через VPN) — название, год, постер, рейтинг; YouTube — Data API v3 (бесплатно, через VPN) или `yt-dlp --dump-json ytsearch5:` без ключа; VK Видео — `video.search` VK API (нужен токен пользователя).
2. brain формирует 3–5 карточек `{n, title, year, poster, source, deep_link}` и шлёт их **одновременно**: на экран Баюна (SSE, уже есть), в PiPup-оверлей (картинка-коллаж с номерами), в PWA/Telegram (inline-кнопки).
3. Выбор любым способом: «вторую», тап, кнопка в Telegram → `play_result(n)` открывает deep link в приложении ТВ (раздел 5) или, если приложения нет, в киоске (как сейчас).
4. Память: «продолжи фильм» — brain хранит `last_played` с таймкодом (из `androidtvremote2` таймкод не достать; хранить время старта и «продолжить» = открыть снова, Кинопоиск/VK сами помнят позицию).

### 4.3 Что убрать
- «Номера на всех кликабельных элементах» (`screen.js`) оставить только как запасной режим для сайтов без приложений; из основного сценария убрать.
- Инструмент `screen` в промпте LLM спрятать за флагом, чтобы модель не тянулась к нему.

---

## 5. Видео и экран

### 5.1 Решение: нативные приложения ТВ + ноут как экран Баюна
| | Киоск Chromium на ноуте (сейчас) | Приложения Google TV через deep links |
|---|---|---|
| Качество | 1080p; 4K упирается в HDMI/EDID и VAAPI | 4K/HDR, аппаратный декодер ТВ |
| Реклама YouTube | uBlock + скрипт (ломается) | SmartTube: без рекламных элементов в коде [[smarttube]](https://github.com/yuliskov/SmartTube) |
| Курсор | Wayland: из приложения не спрятать, нужен плагин/`wtype`-хак [[kiosk]](https://help.pisignage.com/hc/en-us/articles/59094755442201) | нет курсора |
| Кинопоиск/VK/Okko | Веб-версии, защита DRM в Chromium Linux (Widevine L3, не 4K) | родные приложения |
| Управление | полное (CDP) | запуск по ссылке + клавиши пульта; таймкод/статус плеера не читаются |
| Экран Баюна | это и есть экран | только оверлей (PiPup) + отдельный HDMI-вход |

Вывод: видео — в приложениях ТВ, киоск — запасной. Ноут продолжает рисовать экран Баюна на своём HDMI-входе; ТВ переключается на него `send_launch_app_command(LAPTOP_INPUT_URI)` (уже реализовано в `atv.py`), когда видео не играет или Костя просит «покажи экран».

### 5.2 Deep links (проверить на ТВ, запуск через `androidtvremote2.send_launch_app_command`)
- YouTube: `https://www.youtube.com/watch?v=<id>` или `vnd.youtube://<id>`; если установлен SmartTube и он выбран обработчиком ссылок youtube.com — откроется в нём [[ha-deeplinks]](https://community.home-assistant.io/t/android-tv-remote-app-links-deep-linking-guide/567921). SmartTube ставится через Downloader (`kutt.to/stn_stable`) [[smarttube]](https://github.com/yuliskov/SmartTube); с осторожностью — у проекта был инцидент с вредоносной сборкой, брать только с GitHub-релизов.
- VK Видео (`com.vk.tv`, RuStore) [[rustore]](https://www.rustore.ru/catalog/app/com.vk.tv): пробовать `https://vkvideo.ru/video-<owner>_<id>`; схему уточнить из манифеста APK (`adb shell cmd package dump com.vk.tv` или декомпиляция) — метод из гайда HA.
- Кинопоиск Android TV: пробовать `https://hd.kinopoisk.ru/film/<kp_id>` и `https://www.kinopoisk.ru/film/<id>/`; `kp://` не подтверждён.
- Если ссылка не перехватывается: запуск приложения по пакету + «ввод текста» невозможен (отправка текста в протоколе не реализована [[ha-text]](https://community.home-assistant.io/t/android-tv-remote-sending-text/585643)), поэтому запасной путь — киоск.
- Известный риск: в HA Core 2026.8 запуск приложений ломался у части пользователей [[ha-break]](https://community.home-assistant.io/t/android-tv-remote-no-longer-launching-apps/1021224) — держать киоск как fallback.

### 5.3 Оверлей поверх приложений ТВ
- **PiPup** (APK на ТВ, HTTP POST на :7979 с текстом/картинкой/позицией/длительностью) [[pipup]](https://community.home-assistant.io/t/a-short-guide-for-setting-up-tv-pip-notifications-with-pipup/537084) или **Notifications for Android TV** (есть интеграция HA `nfandroidtv`) [[nfandroidtv]](https://www.home-assistant.io/integrations/nfandroidtv/). PiPup удобнее: произвольная картинка → коллаж карточек с номерами, фаза «слушаю/думаю» с иконкой.
- В `tv-agent` добавить `/overlay` → PiPup, когда `current_app != INPUT_PLAYER`, иначе — CDP-оверлей как сейчас.

### 5.4 HDMI и 4K (ноут как экран Баюна)
- Экран Баюна не требует 4K; зафиксировать 1920×1080@60 и не трогать: `kscreen-doctor output.HDMI-A-1.mode.1920x1080@60` в автостарте tv-agent и после `resume` (`systemd` user-unit на `suspend.target`/`post`). Причина «4096×2160 → нет сигнала»: ТВ объявляет кино-4K как предпочтительный режим, а HDMI-тракт ноута его не вывозит [[arch]](https://bbs.archlinux.org/viewtopic.php?id=302832).
- Если 4K с ноута всё же понадобится (киоск как fallback): проверить `edid-decode /sys/class/drm/card*-HDMI-A-1/edid` на наличие 3840×2160@60 и HDMI 2.0 (у Latitude 5420 порт HDMI 2.0; кабель — «Premium High Speed»); при отсутствии — EDID-override через `drm.edid_firmware`. 4K-видео в Chromium на Iris Xe — только с VAAPI и VP9/AV1 hw-декодом, HDR нет. Поэтому 4K — через приложения ТВ.
- Курсор в киоске: `cursor: none` в `overlay.js` + при старте киоска `ydotool mousemove` в угол; радикально — запускать киоск в отдельной сессии `cage` (kiosk-компоситор), где курсор прячется. Не приоритет, так как киоск становится запасным.

---

## 6. Голос ответа (TTS)

| Вариант | Доступность из РФ | Эмоции | Цена | Задержка | Вердикт |
|---|---|---|---|---|---|
| **Yandex SpeechKit API v3 (`general`)** | да, Yandex Cloud/AI Studio, оплата российской картой | роли: `neutral/good/evil/strict/friendly/whisper` по голосам: `kirill` (neutral, strict, good), `anton`, `dasha` (good, friendly), `marina` (whisper, friendly), `masha` (good, strict, friendly), `jane` (good, evil) [[voices]](https://aistudio.yandex.ru/docs/ru/speechkit/tts/voices) | 0,1626 ₽ за запрос до 250 символов (v3), `livetts` 0,25 ₽ [[pricing]](https://aistudio.yandex.ru/docs/ru/speechkit/pricing); 100 фраз/день ≈ 500 ₽/мес | ~0,3–0,6 с на фразу, стриминг есть | **Основной.** В `voice/bayun_voice/tts.py` уже есть ветка `yandex` (ключ `YANDEX_API_KEY`), осталось v3 gRPC/REST с ролью |
| LiveTTS (`denis`, `irina`, `sofia`) | да | роли `casual/formal/support/narrator` | 0,25 ₽/запрос | realtime | Попробовать как «живой» голос для длинных ответов |
| Silero v5 (сейчас `eugene`) | локально | нет (только SSML-паузы/темп) | 0 | RTF ~0,04 на CPU | Офлайн-запас и кэш коротких реплик (`COMMON_REPLIES`) |
| Piper `ru_RU-dmitri` | локально | нет | 0 | быстрее всех | уже есть, звучит хуже Silero |
| SaluteSpeech (Сбер) | да, Freemium для физлиц, эмоции в API с 05.2025 [[sber]](https://developers.sber.ru/docs/ru/salutespeech/changelog/latest) | да | пакеты в Studio | — | Альтернатива Яндексу, если не понравятся голоса |
| Vosk-TTS 0.9 | локально | нет | 0 | xRT ~0,35 CPU | нет преимуществ над Silero |
| CosyVoice 3 / Fish Speech S1 / XTTS | локально, GPU | клонирование голоса | 0 | на CPU i7-1185G7 слишком медленно для диалога | нет (до появления GPU) |
| ElevenLabs | нет (карта) | лучшие | — | — | нет |

Как применять эмоции: brain уже умеет audio-теги (`tts.audio_tags`), для Яндекса маппинг тегов → роль (`[радостно]` → `good`, `[строго]` → `strict`, `[шёпотом]` → `whisper`) в `tts.py`; по умолчанию — `neutral`, «Готово/Включаю» — из кэша Silero, чтобы не ждать сеть. Fallback: при таймауте Яндекса > 1,5 с — Silero на ту же фразу (у `reply()` уже есть филлеры).

---

## 7. Надёжность и «микробаги»

- **Единый журнал события** (`brain`, SQLite, таблица `events`): `utterance_id`, тайминги wake/STT/first_token/TTS, текст STT, ответ, инструменты, ошибки; `voice` отправляет записи в brain (`POST /events`) вместе с ссылкой на аудио-кусок в `~/.cache/bayun/utt/`. Страница в PWA «последние фразы» + кнопка «не так» (помечает для разбора). Это главный инструмент против «микробагов».
- **Здоровье**: `/health` уже есть; добавить в PWA светофор «уши / мозг / ТВ / VPN / Яндекс TTS» и голосовую фразу «что с тобой» → без LLM.
- **Микрофон с кнопкой mute (fifine)**: событие «микрофон присылает тишину» уже ловится — озвучивать один раз и показывать на оверлее. Уйдёт с XVF3800.
- **Сон ТВ/HDMI**: ноут не должен зависеть от EDID — фиксированный режим (5.4); после `resume` — `kscreen-doctor` и `to_laptop()` только когда нужно показать экран.
- **Перезапуски**: оставить `Restart=always` и `bayun-watchdog`; добавить `WatchdogSec` + `sd_notify` в voice (зависание `parec` ловится).

---

## 8. План

### 8.1 Сегодня (программно, без покупок) — эффект для Кости: «слышит с дивана при тихом ТВ, не путает, видно, что услышал»
1. **Микрофон к дивану** на любом USB-удлинителе; динамики ноута/ТВ — как есть. (10 минут; +2 м дальности.)
2. **Включить AEC** (`bayun_aec_source`, PipeWire конфиг + `delay_agnostic`), `audio.duck_volume: 0.1`, `wake_screen_command` оставить. (30 минут; команды поверх голоса Баюна и тихого фильма.)
3. **Патч `SttWake`**: без кусков > 3 с непрерывной речи, усиление ≤ ×10, имя только в первых двух словах после паузы; сохранять `NEAR_MISS`-куски. (1–2 часа + тесты в `voice/tests/`.)
4. **Запустить обучение «Волтэр»** в Colab по рецепту «Ирины» (данные генерируются из Piper/Silero/Vosk, 20–40 минут обучения; выбрать из 5–10 прогонов). Параллельно — класс `MicroWakeWord` и режим `hybrid` в `wake.py`. (Полдня; к вечеру — модель на сервере, порог подобрать по записям.)
5. **Yandex TTS v3** в `tts.py` (роли, стриминг, fallback на Silero) — ключ Яндекс Cloud, 1–2 часа. Эффект: живой голос с интонацией.
6. **HDMI**: фиксированный 1080p60 в автостарте tv-agent и после resume. (30 минут; «нет сигнала» после сна уходит.)
7. **Deep link-разведка**: `send_launch_app_command` с `https://www.youtube.com/watch?v=…`, `https://vkvideo.ru/video-…`, `https://hd.kinopoisk.ru/film/…` — проверить, что открывается (делает Костя на ТВ, 15 минут). Это определяет шаг 8.2.

### 8.2 Неделя — эффект: «фильм в 4K без рекламы и курсора, выбор карточками с телефона или голосом, Баюн виден поверх любого приложения»
1. **PWA «Баюн»** в brain: держать-и-говорить, текст, лента, плеер, свет, карточки, журнал фраз. HTTPS в LAN (mkcert). (2 дня.)
2. **Поиск фильмов по метаданным** (Кинопоиск unofficial API / yt-dlp / VK API) → карточки → `play_result(n)` через deep link; киоск как fallback. (1 день.)
3. **PiPup на ТВ** (APK с GitHub, установка Downloader/ADB) + `/overlay` в tv-agent → карточки и фазы поверх приложений. SmartTube — по желанию, с проверкой подписи. (Полдня.)
4. **Telegram-бот** (голосовые → GigaAM, кнопки карточек, уведомления). (Полдня.)
5. **Журнал событий + страница «что услышал»** + «не так». (1 день.)
6. **Дообучить «Волтэр»** на накопленных `NEAR_MISS`/ложных кусках, подобрать порог/окно. (2 часа.)

### 8.3 Купить (по приоритету) — эффект: «слышит как Алиса при громком фильме»
| Что | Цена (ориентир) | Эффект |
|---|---|---|
| **ReSpeaker XVF3800 USB с корпусом** (Seeed $50–65; Ozon/AliExpress-реселлеры — оценочно 7–11 тыс. ₽, проверить) | 7–11 тыс. ₽ | 4-мик beamforming + аппаратный AEC по всему, что играет через плату: команды при фильме с дивана, DoA на кольце |
| **Активный USB-удлинитель 5 м** (или USB-C хаб с длинным кабелем) | 600–1 000 ₽ | плата у дивана, ноут у ТВ |
| **Активные колонки/саундбар с 3,5 мм входом** (можно любые старые компьютерные) | 0–5 тыс. ₽ | весь звук через плату → AEC видит фильм; динамики ТВ выключить |
| HDMI-кабель Premium High Speed 2 м (если киоск останется нужен в 4K) | 500–800 ₽ | 4K60 с ноута |
| Позже: ReSpeaker XVF3800 + XIAO ESP32S3 или ReSpeaker Lite для второй комнаты | $55–65 / $25–37 | «уши» в спальне/кухне по плану из docs/architecture.md (через HA Assist) |

Чего **не** покупать: Voice PE (проигрывает XVF3800 при ТВ), ESP32-S3-BOX-3 (нет XMOS), новый USB-микрофон (проблема не в микрофоне).

---

## 9. Открытые вопросы (проверить на месте)
- Какие deep links перехватывают Кинопоиск/VK Видео на этом Changhong (манифесты APK).
- Разрешены ли на ТВ установки из неизвестных источников (PiPup/SmartTube) — Google TV требует включить «Developer options → Unknown sources».
- Проверить, что XVF3800 USB в режиме UAC2 действительно использует USB-playback как AEC-опору на Linux (по архитектуре XVF3x00 — да; на странице Seeed это явно не сказано).
- Ключ Yandex Cloud: аккаунт, биллинг, сервисный аккаунт с ролью `ai.speechkit-tts.user`.

## 10. Источники
- Сравнение сателлитов Voice PE / Satellite1 / XVF3800 на 5 м с ТВ: https://community.home-assistant.io/t/voice-satellite-comparison-ha-voice-pe-vs-futureproofhomes-satellite1-vs-seeed-respeaker-xvf3800/1026509
- ReSpeaker XVF3800 (wiki Seeed, USB/I2S прошивки, 3,5 мм выход, усилитель): https://wiki.seeedstudio.com/respeaker_xvf3800_introduction/
- Цены XVF3800 (CNX, июль 2025): https://www.cnx-software.com/2025/07/29/respeaker-xmos-xvf3800-4-mic-array-board-features-esp32-s3-module-works-over-usb/
- ESPHome-интеграция XVF3800: https://github.com/formatBCE/Respeaker-XVF3800-ESPHome-integration
- Voice PE, цена и описание: https://home-assistant.io/blog/2024/12/19/voice-preview-edition-the-era-of-open-voice ; отзывы о дальности: https://community.home-assistant.io/t/home-assistant-voice-preview-edition-weak-mic/814551
- Русский microWakeWord «Ирина» (рецепт обучения, метрики): https://huggingface.co/droman42/microwakeword-irina-ru
- Colab-ноутбуки для microWakeWord/openWakeWord: https://community.home-assistant.io/t/colab-notebooks-to-train-your-custom-openwakeword-or-microwakeword/1026635
- microWakeWord в ESPHome: https://esphome.io/components/micro_wake_word/
- Android TV Remote deep links (гайд, метод через манифест): https://community.home-assistant.io/t/android-tv-remote-app-links-deep-linking-guide/567921 ; интеграция HA: https://www.home-assistant.io/integrations/androidtv_remote ; библиотека: https://github.com/tronikos/androidtvremote2 ; отправка текста не реализована: https://community.home-assistant.io/t/android-tv-remote-sending-text/585643 ; поломка запуска в 2026.8: https://community.home-assistant.io/t/android-tv-remote-no-longer-launching-apps/1021224
- SmartTube: https://github.com/yuliskov/SmartTube
- VK Видео для Android TV (RuStore): https://www.rustore.ru/catalog/app/com.vk.tv
- PiPup: https://community.home-assistant.io/t/a-short-guide-for-setting-up-tv-pip-notifications-with-pipup/537084 , https://github.com/desertblade/PiPup ; Notifications for Android TV: https://www.home-assistant.io/integrations/nfandroidtv/
- Yandex SpeechKit цены: https://aistudio.yandex.ru/docs/ru/speechkit/pricing ; голоса и роли: https://aistudio.yandex.ru/docs/ru/speechkit/tts/voices
- SaluteSpeech (эмоции в API, Freemium): https://developers.sber.ru/docs/ru/salutespeech/changelog/latest , https://incrussia.ru/news/salutespeech-zapustilas-dlya-fizlits/
- Оценка русских TTS (Silero/Vosk/XTTS, CER/xRT): https://alphacephei.com/nsh/2024/07/12/russian-tts.html ; Silero v5: https://huggingface.co/Dimitrius174/silero-v5-tts-ru
- PipeWire echo-cancel, delay_agnostic: https://docs.pipewire.org/page_module_echo_cancel.html , https://gitlab.freedesktop.org/pipewire/pipewire/-/issues/2165
- HDMI 4096×2160 на Wayland/KDE: https://bbs.archlinux.org/viewtopic.php?id=302832 , https://bugs.kde.org/show_bug.cgi?id=484037
- Курсор в Wayland-киоске: https://help.pisignage.com/hc/en-us/articles/59094755442201
- HA Companion/Assist на телефоне, wake word 2026.3: https://community.home-assistant.io/t/assist-on-android-with-2026-3-wake-word-only-works-once/994274 ; Voice Satellite для браузера/планшета: https://community.home-assistant.io/t/voice-satellite-turn-any-tablet-phone-or-browser-into-a-private-alexa-google-home-siri-for-home-assistant/1008350
- GigaAM v3 (e2e, пунктуация, ноябрь 2025): https://app.alphaneural.io/models/thanrl/GigaAM-v3
