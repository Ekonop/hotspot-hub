#!/usr/bin/env bash
# uninstall.sh - remove Hotspot Hub (keeps NM profile + UFW rules by default).
# Usage: ./uninstall.sh [--purge]  (--purge also deletes profile + UFW rules)
set -u
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1
sudo systemctl disable --now hotspot-channelsync.service 2>/dev/null || true
sudo rm -f /usr/bin/hotspot-hub \
  /usr/local/bin/hotspot-channelsync.sh \
  /etc/NetworkManager/dispatcher.d/90-hotspot-channelsync \
  /etc/systemd/system/hotspot-channelsync.service \
  /etc/sysctl.d/99-hotspot.conf \
  /etc/sudoers.d/hotspot-hub \
  /usr/share/icons/hicolor/scalable/apps/hotspot-hub.svg \
  /usr/share/applications/hotspot-hub.desktop
sudo rm -rf /usr/share/hotspot-hub
rm -f ~/.config/autostart/hotspot-hub.desktop ~/.config/autostart/hotspot-panel.desktop
sudo systemctl daemon-reload
if [ "$PURGE" = 1 ]; then
  nmcli con delete Hotspot-Shared 2>/dev/null || true
  sudo ufw delete allow in on ap0 >/dev/null 2>&1 || true
  sudo ufw delete route allow in on ap0 out on wlan0 >/dev/null 2>&1 || true
  echo "Purged profile + UFW rules."
fi
echo "Uninstalled (autostart entry removed)."
