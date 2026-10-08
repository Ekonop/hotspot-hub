#!/usr/bin/env bash
# install.sh - end-to-end installer for Hotspot Hub (Arch/CachyOS).
# Usage: ./install.sh [--no-profile] [--no-ufw] [--no-enable]
# Idempotent. Prompts for NM profile passphrase on fresh installs.
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
NO_PROFILE=0; NO_UFW=0; NO_ENABLE=0
for a in "$@"; do
  case "$a" in
    --no-profile) NO_PROFILE=1;;
    --no-ufw) NO_UFW=1;;
    --no-enable) NO_ENABLE=1;;
    -h|--help) sed -n '1,4p' "$0"; exit 0;;
    *) echo "Unknown arg: $a" >&2; exit 2;;
  esac
done
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1" >&2; exit 1; }; }
need install; need nmcli; need iw

echo "==> Installing Hotspot Hub files..."
sudo install -Dm755 "$ROOT/app/hotspot-hub.py" /usr/bin/hotspot-hub
sudo install -Dm755 "$ROOT/helpers/hotspot-on.sh" /usr/share/hotspot-hub/helpers/hotspot-on.sh
sudo install -Dm755 "$ROOT/helpers/hotspot-off.sh" /usr/share/hotspot-hub/helpers/hotspot-off.sh
sudo install -Dm755 "$ROOT/src/hotspot-channelsync.sh" /usr/local/bin/hotspot-channelsync.sh
sudo install -Dm755 "$ROOT/src/90-hotspot-channelsync" /etc/NetworkManager/dispatcher.d/90-hotspot-channelsync
sudo install -Dm644 "$ROOT/src/hotspot-channelsync.service" /etc/systemd/system/hotspot-channelsync.service
sudo install -Dm644 "$ROOT/src/99-hotspot.conf" /etc/sysctl.d/99-hotspot.conf
sudo install -Dm644 "$ROOT/icons/hotspot-hub.svg" /usr/share/icons/hicolor/scalable/apps/hotspot-hub.svg
sudo install -Dm644 "$ROOT/hotspot-hub.desktop" /usr/share/applications/hotspot-hub.desktop
sudo install -Dm440 "$ROOT/src/hotspot-hub.sudoers" /etc/sudoers.d/hotspot-hub
sudo visudo -c
sudo gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || \
  sudo update-icon-caches /usr/share/icons/hicolor 2>/dev/null || true

echo "==> Applying sysctl..."
sudo sysctl --system >/dev/null 2>&1 || sudo sysctl -w net.ipv4.ip_forward=1

if [ "$NO_UFW" = 0 ] && command -v ufw >/dev/null 2>&1; then
  echo "==> Applying UFW rules..."
  sudo ufw allow in on ap0 >/dev/null
  sudo ufw route allow in on ap0 out on wlan0 >/dev/null
else
  echo "==> Skipping UFW (flag or ufw missing)."
fi

if [ "$NO_ENABLE" = 0 ]; then
  echo "==> Enabling channel-sync service..."
  sudo systemctl daemon-reload
  sudo systemctl enable --now hotspot-channelsync.service
fi

if [ "$NO_PROFILE" = 0 ]; then
  echo "==> NM profile setup..."
  bash "$ROOT/scripts/setup-profile.sh"
fi

mkdir -p ~/.config/autostart
cp -f "$ROOT/hotspot-hub.desktop" ~/.config/autostart/hotspot-hub.desktop
# Point autostart at installed binary + icon (desktop file already does).
echo "Installed. Launch: hotspot-hub &  Docs: ~/doc/hotspot-system/"
