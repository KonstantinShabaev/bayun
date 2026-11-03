#!/usr/bin/env bash
# ВМ с Home Assistant OS на libvirt/KVM (этап 1).
#   sudo ./haos-vm.sh <версия HA OS> [nat|bridge]
# Версию брать на https://github.com/home-assistant/operating-system/releases
# nat — сеть libvirt по умолчанию (подходит, пока сервер на Wi-Fi);
# bridge — мост br0 в домашнюю сеть (нужен кабель Ethernet, см. README.md).
set -euo pipefail

VERSION="${1:?Укажите версию HA OS, например 16.3}"
MODE="${2:-nat}"
BAYUN_DATA="${BAYUN_DATA:-/srv/bayun}"
DISK="$BAYUN_DATA/vm/haos.qcow2"
URL="https://github.com/home-assistant/operating-system/releases/download/${VERSION}/haos_ova-${VERSION}.qcow2.xz"

if virsh dominfo haos >/dev/null 2>&1; then
  echo "ВМ haos уже есть, ничего не делаю." >&2
  exit 0
fi

if [[ ! -f "$DISK" ]]; then
  echo "== Скачиваю $URL"
  curl -fL "$URL" -o "$DISK.xz"
  xz -d "$DISK.xz"
  qemu-img resize "$DISK" 32G
fi

case "$MODE" in
  nat) NET="network=default,model=virtio" ;;
  bridge) NET="bridge=br0,model=virtio" ;;
  *) echo "Режим сети: nat или bridge" >&2; exit 1 ;;
esac

virt-install \
  --name haos \
  --memory 4096 \
  --vcpus 2 \
  --cpu host-passthrough \
  --osinfo linux2022 \
  --boot uefi \
  --import \
  --disk "path=$DISK,bus=virtio" \
  --network "$NET" \
  --graphics none \
  --noautoconsole

virsh autostart haos
echo "ВМ haos запущена. Адрес: virsh domifaddr haos (в режиме nat — 192.168.122.x)."
