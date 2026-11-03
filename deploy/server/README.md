# Баюн на домашнем сервере

Как поставить Баюна на один ноутбук или мини-ПК с Debian 13: Home Assistant OS в ВМ (libvirt/KVM), голосовые сервисы и сервисы Баюна в Docker на хосте. Почему так, а не Proxmox: встроенная видеокарта нужна хосту сразу для ТВ, OpenVINO и Frigate, см. [архитектуру](../../docs/architecture.md).

| Сервис | Где | Порт | Этап |
|---|---|---|---|
| Home Assistant OS (+ Mosquitto, ESPHome, Music Assistant) | ВМ `haos` | 8123 | 1 |
| `whisper` — штатный STT, точка отсчёта | Docker | 10300 | 2 |
| `piper` — русская озвучка | Docker | 10200 | 2 |
| `openwakeword` — wake word для сателлита на сервере | Docker | 10400 | 2 |
| сателлит + эхоподавление PipeWire | сессия рабочего стола | 6053 | 3 |
| `bayun-gateway` — LiteLLM, единственный выход в облако | Docker, профиль `brain` | 4000 (только localhost) | 4 |
| `bayun-brain` | Docker, профиль `brain` | 8765 | 4 |
| `bayun-stt` (GigaAM) | Docker, профиль `stt` | 10301 | 5 |

Данные и модели — в `/srv/bayun` (переменная `BAYUN_DATA`), секреты — в `deploy/server/.env` с правами 600.

## 0. Хост

```sh
sudo git clone https://github.com/konstantinshabaev/bayun /opt/bayun
sudo chown -R "$USER": /opt/bayun
cd /opt/bayun/deploy/server
sudo ./host-setup.sh "$USER"     # Docker, libvirt, драйверы iGPU, zram, папки
# перелогиниться, чтобы применились группы docker и libvirt
docker run --rm hello-world
```

Порты сервисов открыты в домашнюю сеть. Наружу (на роутере) ничего не пробрасываем.

## 1. Home Assistant в ВМ

```sh
sudo ./haos-vm.sh 16.3 nat       # версия HA OS из релизов на GitHub
virsh domifaddr haos             # адрес ВМ, в режиме nat — 192.168.122.x
```

**Пока сервер на Wi-Fi**, ВМ сидит за NAT: мост через Wi-Fi не работает. Открыть HA с другого компьютера можно через SSH-туннель:

```sh
ssh -L 8123:192.168.122.X:8123 user@сервер   # потом http://localhost:8123
```

Из ВМ хост виден как `192.168.122.1`: этот адрес указываем в интеграциях Wyoming.

**С кабелем Ethernet** переводим ВМ на мост, чтобы HA сам находил ESPHome-устройства, ТВ и Cast:

```sh
sudo nmcli con add type bridge ifname br0 con-name br0 ipv4.method auto
sudo nmcli con add type bridge-slave ifname <eth> master br0
sudo nmcli con up br0
virsh shutdown haos
virsh edit haos     # <interface type='bridge'><source bridge='br0'/>
virsh start haos
```

ВМ получит адрес в домашней сети, его стоит закрепить в DHCP роутера.

В HA: владелец, русский язык; аддоны Mosquitto, ESPHome, Music Assistant; интеграции Android TV Remote и Model Context Protocol Server. Для brain завести отдельного пользователя `bayun` без прав администратора и выпустить ему long-lived token. Потом снапшот ВМ: `virsh snapshot-create-as haos clean`.

## 2. Голос

```sh
docker compose up -d
docker compose logs -f whisper   # первая загрузка скачивает модель
```

В HA добавить три интеграции Wyoming: `192.168.122.1` (или адрес сервера) на портах 10300, 10200, 10400. Затем «Настройки → Голосовые ассистенты»: пайплайн «Баюн», язык русский, STT Whisper, агент Home Assistant, TTS Piper. Проверить из приложения HA на телефоне.

## 3. Сателлит в комнате с сервером

1. Микрофон в USB, ТВ по HDMI, автовход в сессию рабочего стола (PipeWire живёт в сессии пользователя).
2. `desktop/60-bayun-echo-cancel.conf` → `~/.config/pipewire/pipewire.conf.d/`, перезапустить PipeWire. Появится источник «Баюн: микрофон с эхоподавлением».
3. Поставить [linux-voice-assistant](https://github.com/OHF-Voice/linux-voice-assistant) как сервис пользователя (`systemctl --user`), указать ему этот источник. HA найдёт его как устройство ESPHome; привязать к зоне.

## 4. Шлюз и brain

```sh
$EDITOR .env                          # ANTHROPIC_API_KEY, LITELLM_MASTER_KEY
$EDITOR /srv/bayun/brain/config.yaml  # адрес HA и токен пользователя bayun
docker compose --profile brain up -d --build
curl -s localhost:8765/health
```

Модель меняется в `gateway/litellm.yaml` одной строкой, там же дневной лимит трат. Если провайдер недоступен напрямую, шлюз ходит наружу через свой VPN (WireGuard на VPS).

Интеграцию `custom_components/bayun` ставим через HACS как свой репозиторий и выбираем агентом в пайплайне с галочкой «сначала локальные команды».

## 5. Свой STT

Когда `bayun-stt` научится GigaAM (M1):

```sh
echo BAYUN_STT_ENGINE=gigaam-ctc >> .env
docker compose --profile stt up -d --build
```

Добавить в HA ещё одну интеграцию Wyoming на порт 10301, сравнить с Whisper в `bench/`, переключить пайплайн.

## Обновление

```sh
cd /opt/bayun && git pull
cd deploy/server && docker compose --profile brain up -d --build
docker compose pull && docker compose up -d   # готовые образы
```
