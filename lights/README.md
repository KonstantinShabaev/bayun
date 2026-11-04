# bayun-lights

Свои эффекты и светомузыка для ламп Tuya (Smart Life) напрямую по локальной сети, без облака и без сцен HA.

Кадры идут в DP 28 («управляющие данные»): цвет меняется сразу и не пишется во флеш лампы, поэтому поток в 10–15 кадров/с лампе не вредит. Замер на W509Z1: 12 команд/с с ожиданием ответа, задержка 20–30 мс; без ожидания ответа можно больше.

## Запуск на сервере

```sh
uv sync --extra tuya            # или: pip install tinytuya
bayun-lights list               # лампы из ~/.config/bayun/tuya-devices.json
bayun-lights rainbow --fps 12
bayun-lights candle --lamp "LED BULB W5K"
bayun-lights music              # светомузыка из того, что играет ноутбук (parec, PipeWire)
bayun-lights breathe --dry-run --duration 3   # кадры в консоль, лампы не трогаем
```

Ctrl+C останавливает эффект, и лампы возвращаются в прежнее состояние.

Файл ключей — `devices.json` от `tinytuya wizard` или словарь «имя → {id, key, ip, version}». Держать с правами 600, в репозиторий не класть.

## Сервис для HA и голоса

`bayun-lights serve` держит один эффект за раз и принимает команды по HTTP:

```sh
curl -X POST -H "Authorization: Bearer $TOKEN" http://192.168.122.1:8765/effect/rainbow
curl -X POST -H "Authorization: Bearer $TOKEN" http://192.168.122.1:8765/stop
curl -H "Authorization: Bearer $TOKEN" http://192.168.122.1:8765/state
```

- `deploy/bayun-lights.service` — пользовательский systemd-сервис на ноутбуке.
- `deploy/ha-package.yaml` — выбор «Эффект за диваном» в HA: `input_select` + `rest_command` + автоматизация. Баюн и голос управляют тем же `input_select`.

## Ограничения

- Лампа держит одно локальное соединение. Пока идёт эффект, Tuya Local в HA с ней не поговорит.
- Частоту ограничивает стоковая прошивка. Если её не хватит, следующий шаг — перепрошивка на OpenBeken (UART), тогда лампы управляются по MQTT/HTTP своей прошивкой.

## Эффекты

| Имя | Что делает |
|---|---|
| `rainbow` | радуга бежит по лампам |
| `breathe` | дыхание одним цветом, лампы вразнобой |
| `candle` | тёплое мерцание свечи |
| `music` | громкость → яркость, удар → новый цвет и следующая лампа |

Новый эффект — класс с методом `frame(t, n, audio) -> list[HSV]` в `effects.py`.
