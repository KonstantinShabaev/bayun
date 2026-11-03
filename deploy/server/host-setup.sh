#!/usr/bin/env bash
# Подготовка хоста Debian 13 под Баюна (этап 0). Повторный запуск безопасен.
#   sudo ./host-setup.sh [пользователь]
# Ставит Docker CE, libvirt/KVM, драйверы iGPU для OpenVINO и VA-API, zram,
# создаёт папки данных. Ничего не удаляет.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Запустите через sudo" >&2
  exit 1
fi

TARGET_USER="${1:-${SUDO_USER:-}}"
BAYUN_DATA="${BAYUN_DATA:-/srv/bayun}"
HERE="$(cd "$(dirname "$0")" && pwd)"
. /etc/os-release

echo "== Пакеты"
apt-get update
apt-get install -y ca-certificates curl gnupg git rsync jq \
  qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients virtinst ovmf \
  intel-opencl-icd intel-media-va-driver vainfo intel-gpu-tools \
  zram-tools xz-utils

echo "== Docker CE"
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker libvirtd

echo "== Логи Docker не растут бесконечно"
if [[ ! -f /etc/docker/daemon.json ]]; then
  cat > /etc/docker/daemon.json <<'JSON'
{ "log-driver": "local", "log-opts": { "max-size": "20m", "max-file": "3" } }
JSON
  systemctl restart docker
fi

echo "== zram 4 ГБ"
sed -i -E 's/^#?ALGO=.*/ALGO=zstd/; s/^#?SIZE=.*/SIZE=4096/' /etc/default/zramswap
systemctl restart zramswap || true

if [[ -n "$TARGET_USER" ]]; then
  echo "== Группы docker, libvirt, render, video для $TARGET_USER"
  usermod -aG docker,libvirt,kvm,render,video "$TARGET_USER"
fi

echo "== Папки данных в $BAYUN_DATA"
install -d -m 0755 "$BAYUN_DATA"/{models/whisper,models/piper,models/openwakeword,models/gigaam,brain,vm}
if [[ ! -f "$BAYUN_DATA/brain/config.yaml" ]]; then
  install -m 0600 "$HERE/brain/config.example.yaml" "$BAYUN_DATA/brain/config.yaml"
fi
if [[ ! -f "$HERE/.env" ]]; then
  install -m 0600 "$HERE/.env.example" "$HERE/.env"
fi
if [[ -n "$TARGET_USER" ]]; then
  chown -R "$TARGET_USER": "$BAYUN_DATA/brain" "$HERE/.env"
fi

echo "== Сеть libvirt по умолчанию (NAT для ВМ haos, пока нет кабеля)"
virsh net-autostart default >/dev/null 2>&1 || true
virsh net-start default >/dev/null 2>&1 || true

echo "== Проверка"
docker --version
docker compose version
virt-host-validate qemu || true
ls -l /dev/dri/ || echo "Нет /dev/dri: iGPU не видна"
echo "Готово. Перелогиньтесь, чтобы группы применились."
