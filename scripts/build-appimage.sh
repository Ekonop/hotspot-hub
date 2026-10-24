#!/usr/bin/env bash
# build-appimage.sh - package the Hotspot Hub GUI as a self-contained AppImage.
#
# Bundles a relocatable CPython (via uv) + PyQt6 (pip wheels) + the app into
# an AppDir, then packs it with appimagetool. Backend (NM, sudoers, channel-sync
# service) must be installed system-wide as usual - the AppImage is the GUI
# launcher with its own Python/Qt runtime.
#
# Prereqs: uv, curl, a downloaded appimagetool (auto-fetched on first run).
# Usage: ./scripts/build-appimage.sh [out-dir]
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${1:-$ROOT/packaging/appimage}"
APP="$OUT_DIR/AppDir"
BUNDLE="$APP/usr/lib/hotspot-hub"
VERSION="0.4.3"
ARCH="x86_64"
PY_VERSION="3.12.14"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1" >&2; exit 1; }; }
need uv; need curl

TOOL="$OUT_DIR/appimagetool-x86_64.AppImage"
if [ ! -x "$TOOL" ]; then
  echo "==> Fetching appimagetool (continuous)..."
  curl -sL --max-time 180 -o "$TOOL" \
    https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x "$TOOL"
fi

echo "==> Ensuring CPython $PY_VERSION via uv..."
uv python install "$PY_VERSION"

UVPY="$(uv python dir)/cpython-$PY_VERSION-linux-$ARCH-gnu"
[ -d "$UVPY" ] || { echo "uv python not found at $UVPY" >&2; exit 1; }

echo "==> Staging AppDir at $APP"
rm -rf "$APP"
mkdir -p "$APP"/usr/bin
mkdir -p "$BUNDLE"/{python,pylibs,app,cli,helpers,icons}
mkdir -p "$APP/usr/share/applications"
mkdir -p "$APP/usr/share/icons/hicolor/scalable/apps"

echo "==> Bundling CPython ($PY_VERSION)..."
cp -a "$UVPY"/. "$BUNDLE/python/"

echo "==> Installing PyQt6 wheels into pylibs..."
uv pip install --target "$BUNDLE/pylibs" \
  --python "$BUNDLE/python/bin/python3" \
  PyQt6

echo "==> Copying app sources..."
cp "$ROOT/app/hotspot-hub.py" "$BUNDLE/app/"
cp "$ROOT/cli/hotspot-hub-cli.py" "$BUNDLE/cli/"
cp "$ROOT/helpers/hotspot-on.sh" "$ROOT/helpers/hotspot-off.sh" "$BUNDLE/helpers/"
cp -r "$ROOT/assets" "$BUNDLE/assets"
cp "$ROOT/icons/hotspot-hub.svg" "$BUNDLE/icons/"
cp "$ROOT/icons/hotspot-hub.svg" "$APP/hotspot-hub.svg"
cp "$ROOT/icons/hotspot-hub.svg" "$APP/usr/share/icons/hicolor/scalable/apps/"

# AppRun: runs the bundled python + PyQt6 on the app
cat > "$APP/AppRun" <<'APP_RUN'
#!/bin/sh
SELF=$(readlink -f "$0" 2>/dev/null || echo "$0")
HERE=$(dirname "$SELF")
HH="$HERE/usr/lib/hotspot-hub"
export PYTHONHOME="$HH/python"
export PYTHONPATH="$HH/pylibs"
unset PYTHONSTARTUP
exec "$HH/python/bin/python3" "$HH/app/hotspot-hub.py" "$@"
APP_RUN
chmod +x "$APP/AppRun"

# Wrapper scripts (used when the AppDir is unpacked / by the desktop entry)
make_wrapper() { # name, script
  local name="$1" script="$2"
  cat > "$APP/usr/bin/$name" <<WRAP
#!/bin/sh
HERE=\$(dirname "\$(readlink -f "\$0")")/..
HH="\$HERE/lib/hotspot-hub"
export PYTHONHOME="\$HH/python"
export PYTHONPATH="\$HH/pylibs"
exec "\$HH/python/bin/python3" "\$HH/$script" "\$@"
WRAP
  chmod +x "$APP/usr/bin/$name"
}
make_wrapper "hotspot-hub" "app/hotspot-hub.py"
make_wrapper "hotspot-hub-cli" "cli/hotspot-hub-cli.py"

cat > "$APP/hotspot-hub.desktop" <<DESKTOP
[Desktop Entry]
Name=Hotspot Hub
Comment=Tray control for concurrent Wi-Fi hotspot, WARP and channel-sync
Exec=hotspot-hub
Icon=hotspot-hub
Type=Application
Categories=Network;
Terminal=false
StartupNotify=false
X-AppImage-Version=$VERSION
X-AppImage-Architecture=$ARCH
DESKTOP
cp "$APP/hotspot-hub.desktop" "$APP/usr/share/applications/"
echo "==> Packing AppImage..."
OUT="$OUT_DIR/HotspotHub-$VERSION-$ARCH.AppImage"
rm -f "$OUT"
"$TOOL" --appimage-extract-and-run "$APP" "$OUT"

echo "==> Cleaning intermediate AppDir ($APP)..."
rm -rf "$APP"

echo "Built: $OUT ($(du -h "$OUT" | cut -f1))"