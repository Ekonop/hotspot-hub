#!/usr/bin/env bash
# uninstall.sh - remove Hotspot Hub (keeps NM profile + UFW rules by default).
# Usage: ./uninstall.sh [--purge]  (--purge also deletes profile + UFW rules)
set -u
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1
sudo systemctl disable --now hotspot-channelsync.service 2>/dev/null || true
sudo rm -f /usr/bin/hotspot-hub \
  /usr/bin/hotspot-hub-flet \
  /usr/bin/hotspot-hub-qt \
  /usr/bin/hotspot-hub-cli \
  /usr/local/bin/hotspot-channelsync.sh \
  /etc/NetworkManager/dispatcher.d/90-hotspot-channelsync \
  /etc/NetworkManager/conf.d/99-hotspot-powersave.conf \
  /etc/systemd/system/hotspot-channelsync.service \
  /etc/sysctl.d/99-hotspot.conf \
  /etc/sudoers.d/hotspot-hub \
  /usr/share/man/man1/hotspot-hub-cli.1 \
  /usr/share/icons/hicolor/scalable/apps/hotspot-hub.svg \
  /usr/share/pixmaps/hotspot-hub.svg \
  /usr/share/pixmaps/hotspot-hub.png \
  /usr/share/icons/hicolor/*/apps/hotspot-hub.png \
  /usr/share/applications/hotspot-hub.desktop \
  /usr/share/applications/hotspot-hub-flet.desktop \
  /usr/share/applications/hotspot-hub-qt.desktop
sudo rm -rf /usr/share/hotspot-hub
sudo gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
sudo update-desktop-database /usr/share/applications 2>/dev/null || true
rm -f ~/.config/autostart/hotspot-hub.desktop ~/.config/autostart/hotspot-panel.desktop
sudo systemctl daemon-reload
sudo nmcli general reload >/dev/null 2>&1 || true
if [ "$PURGE" = 1 ]; then
  nmcli con delete Hotspot-Shared 2>/dev/null || true
  sudo ufw delete allow in on ap0 >/dev/null 2>&1 || true
  sudo ufw delete route allow in on ap0 out on wlan0 >/dev/null 2>&1 || true
  echo "Purged profile + UFW rules."
fi
echo "Uninstalled (autostart entry removed)."
