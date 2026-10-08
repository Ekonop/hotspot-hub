#!/usr/bin/env bash
# stage-and-build.sh - copy sources next to PKGBUILD and run makepkg.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/packaging/arch"
copy() { cp -f "$ROOT/$1" "$DEST/$2"; }
copy app/hotspot-hub.py hotspot-hub.py
copy helpers/hotspot-on.sh hotspot-on.sh
copy helpers/hotspot-off.sh hotspot-off.sh
copy src/hotspot-channelsync.sh hotspot-channelsync.sh
copy src/90-hotspot-channelsync 90-hotspot-channelsync
copy src/hotspot-channelsync.service hotspot-channelsync.service
copy src/99-hotspot.conf 99-hotspot.conf
copy src/hotspot-hub.sudoers hotspot-hub.sudoers
copy icons/hotspot-hub.svg hotspot-hub.svg
copy hotspot-hub.desktop hotspot-hub.desktop
copy scripts/setup-profile.sh setup-profile.sh
cd "$DEST" && makepkg -f
