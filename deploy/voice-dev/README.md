# Черновой голосовой стенд на сервере

Голосовой цикл `voice/` и `brain` как сервисы пользователя, без Docker и HA. Нужен, чтобы
разговаривать с Баюном и мерить задержки, пока поднимаются ВМ с HA и шлюз (deploy/server).

```sh
git clone https://github.com/konstantinshabaev/bayun ~/bayun && cd ~/bayun
uv sync --all-packages --extra local --extra llm --extra video --extra whisper   # whisper — только для сравнения
uv run python -m piper.download_voices --download-dir voices ru_RU-dmitri-medium
cp voice/config.example.yaml voice/config.yaml
cp deploy/voice-dev/brain.config.yaml brain/config.yaml
install -D -m 600 /dev/null ~/.config/bayun/brain.env   # ANTHROPIC_API_KEY=...
cp deploy/voice-dev/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now bayun-brain bayun-voice
journalctl --user -fu bayun-voice
```

Эхоподавление (чтобы Баюна можно было перебить, пока он говорит):
`cp deploy/voice-dev/60-bayun-echo-cancel.conf ~/.config/pipewire/pipewire.conf.d/`,
`systemctl --user restart pipewire pipewire-pulse wireplumber`, затем в `voice/config.yaml`
`audio.input_device: "bayun_aec_source"`.

Микрофон по умолчанию: `pactl set-default-source <источник>` (список: `pactl list short sources`).
Страница состояния: `http://<сервер>:8770`, кнопка «Говорить» работает вместо имени.

Без доступа к Anthropic brain отвечает «Облако сейчас недоступно». Когда появится шлюз,
в `brain/config.yaml` ставим `main: litellm_proxy/bayun-main` и `api_base` шлюза, ключ
провайдера из brain.env убираем.

## Автозапуск и сторожа (с 2026-11-11)

Сервисы пользователя `walter` (linger включён, стартуют без входа в KDE):
`bayun-brain`, `bayun-voice`, `bayun-tv-agent`, `bayun-remote-ui` — все `Restart=always`.
Киоск Chromium поднимает сам tv-agent, когда он нужен (нужна сессия Wayland).

```sh
cp bayun-*.service bayun-watchdog.timer ~/.config/systemd/user/
install -m 755 bayun-watchdog ~/bayun/bin/
systemctl --user daemon-reload
systemctl --user enable --now bayun-brain bayun-voice bayun-tv-agent bayun-remote-ui bayun-watchdog.timer
```

`bayun-watchdog` раз в 2 минуты: сервис, не ответивший дважды подряд, перезапускается.

## VPN: Xray + раздельный туннель

`xray.service` (VLESS+Reality, socks 127.0.0.1:10808, конфиг `/etc/xray/config.json`, 600)
→ `sing-box` даёт только TUN `sb0` (drop-in `sing-box-xray.conf`) → `vpnsplit` метит 0x51
трафик к Anthropic и YouTube. Системный `bayun-vpn-watchdog.timer` раз в 3 минуты
проверяет Anthropic через Xray и перезапускает xray и sing-box после двух неудач подряд.
