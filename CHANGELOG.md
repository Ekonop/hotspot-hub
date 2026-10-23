# Changelog

All notable changes to Hotspot Hub are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-10-24

### Changed
- **UI redesign (instrument-deck, per oil-ui direction)** — see `docs/UI-REDESIGN.md`:
  - Hero card: indicator lamp, big state word (ACTIVE / STANDBY / FAULT),
    one-line instrument readout (SSID · channel · clients · leases · security).
  - **Animated master switch** (`AnimatedToggle`) replaces the On/Off button pair;
    knob slides 180 ms, pressed state shrinks the knob, checked track uses the
    Plasma accent.
  - **Breathing indicator lamp** (`IndicatorLamp`): halo pulses while the hotspot
    is live; solid when off/fault.
  - Design tokens: 16 px canvas / 14 px cards / 8 px controls, 1 px borders,
    engraved letter-spaced section labels, per-section status chips
    (SYNC, LIVE, IDLE), pill chips with state colours.
  - Panel show animation: fade-in 160 ms (OutCubic).
  - Removed developer-facing hint text from the UI bottom bar.
  - Buttons: default/quiet/primary variants; focus = accent ring;
    consistent hover/pressed states across dialogs.
  - Clients / MAC filter / QR dialogs restyled to the same token set;
    empty client state styled as a muted centered label.

## [0.3.0] - 2026-10-24

### Added
- **UI Redesign (Modern Aesthetic)**:
  - New fonts: Manrope (UI) and JetBrains Mono (metrics/IPs/MACs) with fallback chains.
  - Glassmorphism cards: semi-transparent backgrounds, subtle borders, 12px radius.
  - Card drop shadows via `QGraphicsDropShadowEffect` with hover elevation.
  - Solid backgrounds (no compositor flicker); removed `WA_TranslucentBackground`.
  - Resizable dropdown window with `QSizeGrip` and geometry persistence.
  - Tab-friendly layout with consistent 8px grid spacing, 30px min button height.
  - Improved dark/light mode detection via `QPalette` + `KColorScheme`.
  - Icon theme fallback chain extended (`network-wireless-hotspot` → `network-wireless` → `network-modem`).
- **Hotspot Restart Fix**:
  - Manual stop no longer auto-restarts: `/run/hotspot-hub-disabled` flag respected by channel-sync.
- **Scheduled Hotspot**: auto on/off at configured times (`schedule_enabled`, `schedule_on`, `schedule_off`).
- **Battery-Aware Auto-Disable**: disables hotspot when battery ≤ threshold (with hysteresis).
- **Config Validation**: validates band, security, schedule times, battery threshold at load.
- **IPv6 Toggle**: per-profile IPv6 enable/disable via sysctl (sudoers rule added).
- **Multi-Profile Support**: list/add/switch/remove profiles via `hotspot-hub-cli profile`.
- **Bandwidth Throttling**: tc-based total bandwidth cap via `hotspot-hub-cli throttle <iface> <rate>`.
- **Prometheus Metrics Export**: `hotspot-hub-cli metrics` emits textfile-collector format.
- **D-Bus Interface**: session-bus service `org.hotspothub.HotspotHub` (GetStatus, HotspotOn, HotspotOff, GetClientCount, GetWarpStatus).
- **Plasma Applet Scaffold**: QML plasmoid in `plasma-applet/`.
- **Flatpak Manifest**: `flatpak/io.github.vansh.HotspotHub.json`.
- **systemd User Service**: `src/hotspot-hub.service.user`.
- **Env-File Config**: `~/.config/hotspot-hub/config` KEY=VALUE loader for env overrides.
- **Security Audit**: `docs/SECURITY-AUDIT.md` documenting least-privilege sudoers, shell injection, secrets handling.
- **Unit Tests**: 22 tests in `tests/test_parsers.py` (unittest, zero deps).
- **Linting & CI**: ruff config (`pyproject.toml`), GitHub Actions workflow (`.github/workflows/ci.yml`).
- **Screenshots**: added `docs/screenshots/dropdown.png` and README reference.
- **Atomic Profile Updates**: backup + restore on failure in `setup-profile.sh`.
- **Firewall Rules Review**: minimal, interface-scoped UFW rules documented.

### Changed
- Refactored `DropdownPanel` to use `QScrollArea` for sections (no compression/overlap).
- Removed `QGraphicsDropShadowEffect` from `PanelSection` (was causing render overlap).
- PanelSection size policy set to `(Expanding, Minimum)` with trailing stretch.
- `HotspotPanel.refresh()` now calls `check_schedule()` and `check_battery()`.
- `get_base_icon()` extended fallback chain (network-modem).
- `cmd_status` in CLI now includes MAC filter status and WPA3 support.
- `setup-profile.sh` adds atomic backup/restore and sysctl rule for IPv6.

### Fixed
- Hotspot auto-restart on manual stop (channel-sync respects disabled flag).
- Infinite recursion crash (apply_theme ↔ changeEvent) — reentrancy guard.
- UI widget overlap (drop shadow + padding removed, scroll area added).
- Compositor flickering (removed WA_TranslucentBackground, solid backgrounds).
- Button sizing (min-height 30px, padding 6px 12px).
- Missing sudoers rule for IPv6 sysctl (was falling back to pkexec prompt).

---

## [0.2.0] - 2026-10-23

### Added
- **Resizable Dropdown Panel**:
  - Removed maximum width constraint (`420px`) on `DropdownPanel`.
  - Added bottom-right corner `QSizeGrip` resize handle.
  - Geometry and dimensions saved and restored across sessions using `QSettings`.
- **Client MAC Filtering**:
  - Added support for `allowed_macs.txt` and `blocked_macs.txt` in `~/.config/hotspot-hub/`.
  - Added "MAC Filter" configuration dialog accessible from `SystemSection` with allow/deny list management.
  - Ability to add currently connected clients or manual MACs to allow/block lists directly in the UI.
  - Active enforcement: unauthorized clients are automatically kicked using `iw dev <iface> station del <mac>`.
  - Filter state and mode (allowlist vs denylist) persisted to `~/.config/hotspot-hub/config.json`.
- **5GHz Band Support**:
  - Added band selector dropdown (`2.4GHz` / `5GHz` / `Auto`) in `HotspotSection`.
  - Dynamic channel validation via `iw phy` before applying band changes.
  - Automatically updates NetworkManager profile (`802-11-wireless.band` and channel).
  - Band preference saved in configuration file.
  - CLI subcommand `hotspot-hub-cli band [2.4GHz|5GHz|Auto]`.
- **WPA3-SAE Support Detection**:
  - Probes `iw phy` for hardware Simultaneous Authentication of Equals (SAE) capability.
  - Offers WPA3-SAE option during profile setup in `setup-profile.sh` (`--wpa3`).
  - Auto-detects and stores WPA3 support status in `~/.config/hotspot-hub/config.json`.
  - Displays active security standard (`WPA2` vs `WPA3`) in status labels, tray tooltip, and CLI.
- **Command-Line Interface (`hotspot-hub-cli`)**:
  - Subcommands: `status`, `on`, `off`, `restart`, `clients`, `kick <mac>`, `qr`, `band`, `mac-filter`.
  - Full UNIX manual page (`hotspot-hub-cli.1`).
- **Real-Time Bandwidth Monitor**:
  - Throughput calculation (RX/TX bytes per second) for `wlan0` and `ap0` from `/proc/net/dev`.
- **Theme & System Integration**:
  - Adaptive styling for KDE Plasma dark/light themes via `QPalette`.
  - Configurable global keyboard shortcut (`HOTSPOT_HUB_SHORTCUT`, default `Ctrl+Alt+H`).
  - Autostart toggle via checkbox and installer flag `--no-autostart`.

### Changed
- Refactored `DropdownPanel` positioning to respect user-resized window geometry.
- Upgraded `setup-profile.sh` with `--band`, `--wpa3`, and interactive SAE capability prompts.
- Expanded `CONTRIBUTING.md` with complete developer setup, coding style standards, and PR process.

---

## [0.1.0] - 2026-10-08

### Added
- Initial release of Hotspot Hub for Arch Linux and CachyOS.
- PyQt6 system tray control application with frameless dropdown canvas.
- Extensible `PanelSection` card architecture (`HotspotSection`, `WarpSection`, `SystemSection`).
- Concurrent Wi-Fi STA and AP operation on single-radio chips supporting virtual interfaces (`ap0` on `wlan0` PHY).
- Automated channel synchronization via `hotspot-channelsync` (10s fallback loop + NetworkManager dispatcher hook).
- Self-repairing policy routing bypass at priority 50 for Cloudflare WARP and VPN coexistence.
- QR code generation using `qrencode` for rapid client connection.
- Desktop notifications on Wi-Fi client connection and disconnection.
- Passwordless privileged execution through `/etc/sudoers.d/hotspot-hub`.
