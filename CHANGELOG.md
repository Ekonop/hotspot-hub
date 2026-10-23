# Changelog

All notable changes to Hotspot Hub are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- Arch Linux PKGBUILD packaging and idempotent `./install.sh` installation script.
- Core documentation suite: README, ARCHITECTURE, CONFIGURATION, and TROUBLESHOOTING.
