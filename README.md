# Hotspot Hub

KDE system-tray app for concurrent Wi-Fi hotspot (STA/AP, single radio),
WARP toggle, and channel-sync control on Arch/CachyOS (Plasma Wayland,
MediaTek MT7922).

![Hotspot Hub](assets/banner.svg)

![Dropdown canvas](docs/screenshots/dropdown.png)

## Features

### Hotspot Control
- **Concurrent STA + AP** on a single radio (virtual `ap0` on `wlan0` PHY).
- **Instrument-deck dropdown**: hero card with indicator lamp, big state word
  (ACTIVE / STANDBY / FAULT), one-line readout (SSID · channel · clients ·
  leases · security) and an animated **master switch** replacing the On/Off
  button pair. Tray icon stays color-coded (green = on, grey = off, red = error).
- **Channel sync** keeps AP on STA's channel (10s fallback + NM dispatcher events).
- **VPN/WARP bypass** at policy-routing priority 50 (self-repairing).
- **Manual stop respected**: stopping the hotspot no longer auto-restarts via the
  channel-sync daemon (`/run/hotspot-hub-disabled` flag).

### Network & Clients
- **Client list with kick** — show connected stations (MAC, signal) and disconnect.
- **MAC filtering** — allow/deny lists with enforcement (`iw station del`).
- **Bandwidth monitor** — real-time RX/TX throughput for `wlan0` and `ap0`.
- **Bandwidth throttling** — `tc`-based total cap via `hotspot-hub-cli throttle`.
- **QR code** generation for SSID/PSK (via `qrencode`).

### Configuration
- **Multi-profile** support (list/add/switch/remove via CLI).
- **Scheduled hotspot** — auto on/off at configured times.
- **Battery-aware** auto-disable when battery ≤ threshold.
- **5GHz band selection** (2.4 / 5 / Auto) with channel validation.
- **WPA3-SAE detection** — probes `iw phy` for SAE capability.
- **IPv6 toggle** per profile.
- **Dark / light mode** adaptation via `QPalette` + `KColorScheme`.
- **Global keyboard shortcut** (default `Ctrl+Alt+H`, configurable).

### Integration
- **CLI tool** (`hotspot-hub-cli`) with subcommands: `status`, `on`, `off`,
  `restart`, `clients`, `kick`, `qr`, `band`, `mac-filter`, `throttle`,
  `metrics`, `profile`.
- **D-Bus service** (`org.hotspothub.HotspotHub`) for external control.
- **Prometheus metrics** export (textfile-collector format).
- **Plasma QML applet** scaffold.
- **Autostart** toggle (checkbox + installer flag).
- **Desktop notifications** on client connect/disconnect.

### Quality
- **Unit tests** (22 tests, `unittest`, zero deps).
- **ruff** linting + GitHub Actions CI.
- **Security audit** (`docs/SECURITY-AUDIT.md`): scoped sudoers, no shell
  injection, no secrets logged.
- **Man page** for the CLI.

## Layout

- `app/hotspot-hub.py` — tray app with dropdown canvas (`DropdownPanel`).
  Left-click opens cards (**Hotspot**, **WARP**, **Bandwidth**, **System**);
  right-click shows the classic menu. UI design decisions and visual roles are
  documented in `docs/UI-REDESIGN.md`. Add future mods as `PanelSection`
  subclasses, registered in `DropdownPanel._build_sections()` (marked EXTENSION POINT).
- `cli/hotspot-hub-cli.py` — CLI tool with all subcommands above.
- `helpers/` — `hotspot-on.sh` / `hotspot-off.sh` (idempotent, `sudo -n`).
- `icons/hotspot-hub.svg` — app icon (hub-node broadcast mark).
- `assets/` — UI assets: `banner.svg` (README hero) and
  `clients-empty.svg` (empty state in the Clients dialog).
- `src/` — system files: channel-sync, NM dispatcher, sysctl, sudoers,
  systemd user service.
- `scripts/setup-profile.sh` — create/retune `Hotspot-Shared` NM profile.
- `packaging/arch/` — Arch PKGBUILD.
- `flatpak/` — Flatpak manifest.
- `plasma-applet/` — QML plasmoid scaffold.
- `tests/` — unit tests.
- `docs/` — documentation, roadmap, security audit.

No plaintext passwords anywhere.

## Quick install

```bash
./install.sh            # files + sysctl + ufw + service + profile prompt + autostart
hotspot-hub &
```

Options: `./install.sh --no-profile --no-ufw --no-enable --no-autostart`

## Arch package

```bash
make package
sudo pacman -U packaging/arch/hotspot-hub-0.1.0-1-any.pkg.tar.zst
/usr/share/hotspot-hub/setup-profile.sh
```

## Uninstall

```bash
./uninstall.sh          # keeps NM profile + UFW rules
./uninstall.sh --purge  # also deletes profile + UFW rules
```

## CLI usage

```bash
hotspot-hub-cli status
hotspot-hub-cli on | off | restart
hotspot-hub-cli clients
hotspot-hub-cli kick <mac>
hotspot-hub-cli qr
hotspot-hub-cli band [2.4GHz|5GHz|Auto]
hotspot-hub-cli mac-filter <list|allow|deny|block|remove|enable|disable> [mac]
hotspot-hub-cli throttle <iface> <rate_mbps>   # 0 to clear
hotspot-hub-cli metrics [-o FILE]
hotspot-hub-cli profile <list|add|switch|remove> [name]
```

## Configuration

Persistent config: `~/.config/hotspot-hub/config.json`.
Env-style overrides: `~/.config/hotspot-hub/config` (KEY=VALUE lines).
Environment variables: `HOTSPOT_HUB_STA_IF`, `HOTSPOT_HUB_AP_IF`,
`HOTSPOT_HUB_CON`, `HOTSPOT_HUB_DOCS`, `HOTSPOT_HUB_SHORTCUT`.

## Development

```bash
# Lint
ruff check app/ cli/ tests/

# Unit tests
python3 -m unittest discover -s tests -v

# Syntax check
python3 -c "import ast; ast.parse(open('app/hotspot-hub.py').read())"

# Shell validation
bash -n install.sh && bash -n uninstall.sh
```

## Docs

Full setup/ops/troubleshooting: `~/doc/hotspot-system/`
(`README`, `SETUP`, `OPERATIONS`, `TROUBLESHOOTING`, `FILES`).

- [Configuration](docs/CONFIGURATION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Roadmap](docs/ROADMAP.md)
- [Security Audit](docs/SECURITY-AUDIT.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)
