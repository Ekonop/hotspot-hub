# Agent Context — Hotspot Hub

KDE system-tray app for concurrent Wi-Fi hotspot (STA/AP, single radio),
WARP toggle, and channel-sync control on Arch/CachyOS (Plasma Wayland,
MediaTek MT7922).

## Quick orientation

- **Main app:** `app/hotspot-hub.py` (PyQt6 tray + dropdown canvas)
- **Helpers:** `helpers/hotspot-on.sh`, `helpers/hotspot-off.sh`
- **System files:** `src/` (channel-sync, NM dispatcher, sysctl, sudoers)
- **Scripts:** `scripts/setup-profile.sh`, `scripts/stage-and-build.sh`
- **Packaging:** `packaging/arch/{PKGBUILD, hotspot-hub.install}`
- **Docs:** `docs/` (README, CONFIGURATION, ARCHITECTURE, TROUBLESHOOTING)
- **Install:** `./install.sh` (direct) or `make package` (Arch)

## Architecture

Single-radio concurrent STA+AP. STA (wlan0) and AP (ap0) share one PHY/channel.
Channel-sync keeps AP on STA's channel. VPN/WARP bypass at pref 50.

## Extension point

`DropdownPanel._build_sections()` in `app/hotspot-hub.py` — add new
`PanelSection` subclasses there.

## Key patterns

- Privileged ops use `sudo -n` (sudoers) with `pkexec` fallback (`priv()`).
- No plaintext passwords; profile setup prompts for SSID/PSK.
- Env overrides: `HOTSPOT_HUB_STA_IF`, `HOTSPOT_HUB_AP_IF`, `HOTSPOT_HUB_CON`,
  `HOTSPOT_HUB_DOCS`.

## Docs location

Full setup/ops/troubleshooting: `~/doc/hotspot-system/` (README, SETUP,
OPERATIONS, TROUBLESHOOTING, FILES). Also `docs/` in repo.
Roadmap: `docs/ROADMAP.md`.

## Git

Branch: main. Remote: origin/main. 4 commits. Clean working tree.