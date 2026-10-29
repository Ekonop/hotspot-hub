#!/usr/bin/env bash
# install.sh - end-to-end installer for Hotspot Hub (Arch/CachyOS).
# Usage: ./install.sh [--no-profile] [--no-ufw] [--no-enable] [--no-autostart]
# Idempotent. Prompts for NM profile passphrase on fresh installs.
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
NO_PROFILE=0; NO_UFW=0; NO_ENABLE=0; NO_AUTOSTART=0
for a in "$@"; do
  case "$a" in
    --no-profile) NO_PROFILE=1;;
    --no-ufw) NO_UFW=1;;
    --no-enable) NO_ENABLE=1;;
    --no-autostart) NO_AUTOSTART=1;;
    -h|--help) sed -n '1,4p' "$0"; exit 0;;
    *) echo "Unknown arg: $a" >&2; exit 2;;
  esac
done
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1" >&2; exit 1; }; }
need install; need nmcli; need iw

echo "==> Installing Hotspot Hub files..."
# Main app: launcher + app modules. app/hotspot-hub.py resolves core/ from the
# parent of app/ (sys.path.insert), so core/ and cli/ are deployed under
# /usr/share/hotspot-hub next to app/.
sudo tee /usr/bin/hotspot-hub >/dev/null <<'EOF'
#!/usr/bin/env bash
# Hotspot Hub primary launcher (defaults to modern Flet UI; use --legacy-qt or HOTSPOT_HUB_UI=qt for Qt)
export FLET_APP_ID="hotspot-hub"
export FLET_HIDE_WINDOW_ON_START="true"
if [ "${HOTSPOT_HUB_UI:-}" = "qt" ] || [ "${1:-}" = "--legacy-qt" ] || [ "${1:-}" = "--qt" ]; then
  if [ "${1:-}" = "--legacy-qt" ] || [ "${1:-}" = "--qt" ]; then
    shift
  fi
  exec /usr/bin/env python3 /usr/share/hotspot-hub/app/hotspot-hub.py "$@"
fi
exec /usr/bin/env python3 /usr/share/hotspot-hub/app/hotspot-hub-flet.py "$@"
EOF
sudo chmod 755 /usr/bin/hotspot-hub

sudo tee /usr/bin/hotspot-hub-qt >/dev/null <<'EOF'
#!/usr/bin/env bash
# Hotspot Hub classic Qt dropdown launcher
exec /usr/bin/env python3 /usr/share/hotspot-hub/app/hotspot-hub.py "$@"
EOF
sudo chmod 755 /usr/bin/hotspot-hub-qt

sudo install -Dm755 "$ROOT/cli/hotspot-hub-cli.py" /usr/bin/hotspot-hub-cli
sudo install -Dm644 "$ROOT/app/hotspot-hub.py" /usr/share/hotspot-hub/app/hotspot-hub.py
sudo install -d /usr/share/hotspot-hub/core /usr/share/hotspot-hub/cli
sudo install -m644 "$ROOT"/core/*.py /usr/share/hotspot-hub/core/
sudo install -m755 "$ROOT"/cli/*.py /usr/share/hotspot-hub/cli/
# Flet UI (modern interface)
sudo install -Dm755 "$ROOT/app/hotspot-hub-tray.py" /usr/share/hotspot-hub/app/hotspot-hub-tray.py
sudo install -Dm755 "$ROOT/app/hotspot-hub-flet.py" /usr/share/hotspot-hub/app/hotspot-hub-flet.py
sudo install -Dm755 "$ROOT/hotspot-hub-flet.sh" /usr/bin/hotspot-hub-flet
for f in "$ROOT"/assets/*; do
  [ -f "$f" ] && sudo install -Dm644 "$f" "/usr/share/hotspot-hub/assets/$(basename "$f")"
done
sudo install -Dm755 "$ROOT/helpers/hotspot-on.sh" /usr/share/hotspot-hub/helpers/hotspot-on.sh
sudo install -Dm755 "$ROOT/helpers/hotspot-off.sh" /usr/share/hotspot-hub/helpers/hotspot-off.sh
sudo install -Dm755 "$ROOT/helpers/hotspot-adblock.sh" /usr/share/hotspot-hub/helpers/hotspot-adblock.sh
sudo install -Dm755 "$ROOT/helpers/hotspot-adblock.sh" /usr/local/bin/hotspot-adblock.sh
sudo install -Dm755 "$ROOT/src/hotspot-channelsync.sh" /usr/local/bin/hotspot-channelsync.sh
sudo install -Dm755 "$ROOT/src/90-hotspot-channelsync" /etc/NetworkManager/dispatcher.d/90-hotspot-channelsync
sudo install -Dm644 "$ROOT/src/hotspot-channelsync.service" /etc/systemd/system/hotspot-channelsync.service
sudo install -Dm644 "$ROOT/src/99-hotspot.conf" /etc/sysctl.d/99-hotspot.conf
sudo install -Dm644 "$ROOT/src/99-hotspot-powersave.conf" /etc/NetworkManager/conf.d/99-hotspot-powersave.conf
sudo install -Dm644 "$ROOT/src/hotspot-adblock-update.service" /etc/systemd/system/hotspot-adblock-update.service
sudo install -Dm644 "$ROOT/src/hotspot-adblock-update.timer" /etc/systemd/system/hotspot-adblock-update.timer

# RustNet Traffic Inspector bridge (if built)
if [ -f "$ROOT/services/rustnet-hub/backend/target/release/rustnet-hub-backend" ]; then
  sudo install -Dm755 "$ROOT/services/rustnet-hub/backend/target/release/rustnet-hub-backend" /usr/bin/rustnet-hub
  sudo install -Dm644 "$ROOT/src/rustnet-hub.service" /etc/systemd/system/rustnet-hub.service
fi

# Icons installation (scalable SVG + multi-resolution PNGs)
sudo install -Dm644 "$ROOT/icons/hotspot-hub.svg" /usr/share/icons/hicolor/scalable/apps/hotspot-hub.svg
sudo install -Dm644 "$ROOT/icons/hotspot-hub.svg" /usr/share/pixmaps/hotspot-hub.svg
sudo install -Dm644 "$ROOT/icons/hotspot-hub.svg" /usr/share/hotspot-hub/icons/hotspot-hub.svg

for png in "$ROOT"/icons/hicolor/*/apps/hotspot-hub.png; do
  if [ -f "$png" ]; then
    res="$(basename "$(dirname "$(dirname "$png")")")"
    sudo install -Dm644 "$png" "/usr/share/icons/hicolor/$res/apps/hotspot-hub.png"
    sudo install -Dm644 "$png" "/usr/share/hotspot-hub/icons/hicolor/$res/apps/hotspot-hub.png"
  fi
done
if [ -f "$ROOT/icons/hicolor/256x256/apps/hotspot-hub.png" ]; then
  sudo install -Dm644 "$ROOT/icons/hicolor/256x256/apps/hotspot-hub.png" /usr/share/pixmaps/hotspot-hub.png
fi

sudo install -Dm644 "$ROOT/hotspot-hub.desktop" /usr/share/applications/hotspot-hub.desktop
sudo install -Dm644 "$ROOT/hotspot-hub-flet.desktop" /usr/share/applications/hotspot-hub-flet.desktop
sudo install -Dm644 "$ROOT/hotspot-hub-qt.desktop" /usr/share/applications/hotspot-hub-qt.desktop
sudo install -Dm440 "$ROOT/src/hotspot-hub.sudoers" /etc/sudoers.d/hotspot-hub
sudo install -Dm644 "$ROOT/man/hotspot-hub-cli.1" /usr/share/man/man1/hotspot-hub-cli.1
sudo visudo -c

# Update system icon cache and desktop index
sudo gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || \
  sudo update-icon-caches /usr/share/icons/hicolor 2>/dev/null || true
sudo update-desktop-database /usr/share/applications 2>/dev/null || true

# Refresh KDE Sycoca application index for current user
if command -v kbuildsycoca6 >/dev/null 2>&1; then
  if [ -n "${SUDO_USER:-}" ]; then
    su - "$SUDO_USER" -c "kbuildsycoca6 --noincremental" >/dev/null 2>&1 || true
  else
    kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
  fi
elif command -v kbuildsycoca5 >/dev/null 2>&1; then
  if [ -n "${SUDO_USER:-}" ]; then
    su - "$SUDO_USER" -c "kbuildsycoca5 --noincremental" >/dev/null 2>&1 || true
  else
    kbuildsycoca5 --noincremental >/dev/null 2>&1 || true
  fi
fi

echo "==> Installing Flet UI dependencies (pystray, pillow, flet)..."
if command -v pip3 >/dev/null 2>&1 || command -v pip >/dev/null 2>&1; then
  PIP=$(command -v pip3 2>/dev/null || command -v pip 2>/dev/null)
  $PIP install --break-system-packages --quiet flet flet-desktop pystray pillow 2>/dev/null || \
    echo "  (pip install failed; install manually: pip install --break-system-packages flet flet-desktop pystray pillow)"
else
  echo "  (pip not found; install python-pip and run: pip install --break-system-packages flet flet-desktop pystray pillow)"
fi

echo "==> Applying sysctl..."
sudo sysctl --system >/dev/null 2>&1 || sudo sysctl -w net.ipv4.ip_forward=1

echo "==> Applying NetworkManager settings (wifi.powersave=2)..."
sudo nmcli general reload >/dev/null 2>&1 || true

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
  sudo systemctl enable --now hotspot-adblock-update.timer 2>/dev/null || true
fi

if [ "$NO_PROFILE" = 0 ]; then
  echo "==> NM profile setup..."
  bash "$ROOT/scripts/setup-profile.sh"
fi

if [ "$NO_AUTOSTART" = 0 ]; then
  echo "==> Configuring autostart..."
  TARGET_HOME="${HOME}"
  if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
    TARGET_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
  fi
  if [ -d "$TARGET_HOME" ]; then
    mkdir -p "$TARGET_HOME/.config/autostart"
    cp -f "$ROOT/hotspot-hub.desktop" "$TARGET_HOME/.config/autostart/hotspot-hub.desktop"
    if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
      chown -R "$SUDO_USER:" "$TARGET_HOME/.config/autostart" 2>/dev/null || true
    fi
  fi
else
  echo "==> Skipping autostart (--no-autostart)."
fi
# Point autostart at installed binary + icon (desktop file already does).
echo "Installed. Launch: hotspot-hub &  Docs: ~/doc/hotspot-system/"
