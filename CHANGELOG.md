# Changelog

All notable changes to Hotspot Hub are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0] - 2026-10-26

### Added
- **Smart Limited-Connectivity Detection & Reconnect**:
  - `check_connectivity()` probes gateway ping + DNS resolution.
  - Exponential back-off reconnect (30s → 60s → 120s, capped 300s) with 5-min cooldown.
  - Guards: manual-disable flag, user-idle window (no reconnect during active use).
  - SystemSection shows live "Connectivity: OK / Limited (reconnecting… attempt N)".
  - "Smart reconnect" checkbox in UI, persisted to config.
  - Tray notification on reconnect; logs all events.
- **iOS-Style UI/UX Polish**:
  - Dark background uses `#1C1C1E` (no pure `#000`); light uses Plasma palette.
  - 44px minimum tap targets (iOS HIG).
  - Spring-like fade animation (OutBack easing, 220ms).
  - Kinetic (momentum) scrolling via QScroller on dropdown.
  - Heading letter-spacing -0.15px.

### Changed
- Button min-height bumped from 30px to 44px.
- Fade animation easing changed from OutCubic to OutBack (spring feel).
- DropdownPanel canvas bg avoids pure black in dark mode.

---

## [0.5.0] - 2026-10-25

### Fixed
- **Master toggle: real mouse clicks now register.** This was the actual
  reason the in-app switch "didn't work" (while the tray context menu did):
  QCheckBox only treats its indicator box as clickable, and `hitButton()`
  returned false for the whole right half of the pill — exactly where the
  knob sits when the hotspot is ON, i.e. the very spot you click to turn it
  off. Clicks were silently dropped; the widget never even toggled. The whole
  pill is now the button (`AnimatedToggle.hitButton()` → `rect().contains`).
  Verified with real mouse-event dispatch (`QTest.mouseClick`): click → box
  flips → async off runs → hotspot down in ~2s and stays down.


- **Master toggle now actually sticks.** Two layers were fighting it:
  `hotspot-channelsync.service` (a 10-second ticker that keeps the AP on the
  STA channel) was resurrecting a hotspot the moment you toggled it off —
  `nmcli connection up` ran whenever wlan0 was associated, which on this
  system is always. The disabled flag is now set *before* the `nmcli down`
  (and unconditionally), and the channelsync script re-checks the flag right
  before every activation. Live-verified: toggle off stays off through
  multiple real 10s ticks; toggle on stays on.
- **Bandwidth card now shows live speeds.** The monitor only sampled on the
  30s timer (a 30-second average of bursty phone traffic ≈ 0), and the MB/s
  formatter rounded anything under ~50 kB/s to "0.0MB/s". Added a 2s ticker
  (runs only while the panel is visible), fresh-sample handling when the panel
  reopens, and adaptive units (B/s · kB/s · MB/s · GB/s). Verified: 7.4 MB/s
  down during an active download, kB/s during light traffic.


- **Master toggle syncs on panel open** — the periodic refresh timer only ran
  every 30 s, so the switch could show OFF (stale) while the hotspot was
  already ON and clicks "did nothing" or turned the hotspot the wrong way.
  The panel now refreshes (and floats the knob to the live state) before every
  show.
- **Tray flame geometry** — the flame glyph rendered as an 8×12 px blob;
  it now fills the icon canvas as the brand mark in per-state color
  (green = on, grey = off, red = error).


### Added
- **Configure hotspot name & passphrase** from the UI. The hero card's new
  **Configure** pill opens a dialog that renames the SSID and/or changes the
  WPA2 passphrase on the live NetworkManager profile, with live validation
  (SSID 1–32 chars; passphrase 8–63 chars or 64 hex digits; blank passphrase
  keeps the current one) and an automatic restart when the hotspot is active
  so the new name is broadcast immediately. Also exposed as
  `hotspot-hub-cli set-credentials --ssid NAME [--password PSK]`.
- **Device names in the client list** — each connected station now resolves to
  its DHCP hostname from the dnsmasq lease file (iOS/Android report their
  device name, e.g. \"Vansh's iPhone\"), then via **mDNS reverse lookup**
  (resolves phones that use private/randomized MACs and send no DHCP hostname —
  verified live on the user's iPhone), then the MAC vendor from
  `/usr/share/hwdata/oui.txt` (`hwdata`). A **Name…** button per client lets
  you assign a persistent friendly name (`~/.config/hotspot-hub/devicenames.json`),
  which overrides all automatic lookups. The dialog header counts named devices.

- **QR dialog now displays the live passphrase** in plaintext (mono,
  selectable) with a Copy button (`Copied ✓` transient feedback).
- **Brand marks everywhere** — the system-tray icon is now the flame glyph
  itself, painted per-state (green = on, grey = off, red = error) on a
  transparent canvas (previously a tiny status dot on a dark tile that didn't
  read as the logo at 22 px), and the app-wide window icon (dialogs, titlebars)
  is set to the flame brand mark.

## [0.4.3] - 2026-10-25

### Fixed
- **Master switch knob stuck on the right in both the on and off states**
  (reported). `refresh()` synced the switch with `blockSignals()`, so the
  `toggled` signal never fired and the animated knob offset froze wherever
  the last animation left it. `AnimatedToggle.setChecked()` now retargets
  the slide on programmatic changes too, and painting falls back to a
  state-consistent knob position whenever the slide isn't running — the
  knob can never desync from `isChecked()` again.
- **Master switch felt unresponsive / "4-5 clicks and it never turned on"**
  (reported). `hotspot_on/off/restart` and `warp` ran `nmcli`/`warp-cli`
  synchronously in the GUI thread (freezing the panel for ~1-3 s), and every
  accepted click stacked another on/off/on/off cycle, so rapid clicking
  ended in the opposite state. Actions now run on a `QThread` worker with a
  busy latch: the panel never blocks, clicks while an action is in flight
  are rejected (knob reverts), and the switch floats at the user's intent
  until the real state settles.

## [0.4.2] - 2026-10-25

### Fixed
- **Hotspot master switch could not turn the hotspot on** (reported on live
  hardware). Two stacked backend causes, neither reachable from the UI:
  1. The kernel's `ap0` could linger as an nl80211 interface of type
     **managed** instead of `__ap` — mt76/MT7922 flips a deactivated AP
     interface back to managed. NetworkManager then rejects activation of the
     `ap` profile on it with
     `Error: Connection activation failed: The device could not be readied
     for configuration` (this had wedged the toggle in FAULT all session,
     266 failed activation attempts in one boot).
  2. The NM profile requested `802-11-wireless.cloned-mac-address permanent`,
     which MT7922 cannot apply to a virtual AP while the STA uses the same
     PHY MAC — another `config-failed` path.
- **Fix**: `ensure_ap0()` now validates the interface type via `iw dev ap0
  info` and, when it is not `__ap`, deletes and recreates it before
  activating (new scoped sudoers rule `/usr/bin/iw dev ap0 del`).
  `setup-profile.sh` now sets `cloned-mac-address preserve` so NM never
  tries to rewrite the AP MAC.
- Verified live on the reported machine: toggle ON in ~0.7 s
  (`Activation: successful, device activated`, dnsmasq serving `10.42.0.0/24`),
  toggle OFF in ~0.2 s; the heal path was exercised against the exact
  wrong-type `managed` ap0 that was wedging the system.


## [0.4.1] - 2026-10-25

### Added
- **Health card**: new fifth section — health readout
  (`GW 192.168.29.1 reachable · AP UP · DHCP ok (1 lease)`) with green `OK`/
  red `ISSUES` chip, joined by the lifecycle controls that used to live on the
  System card: Start on boot, IPv6, Scheduled on/off (08:00/22:00), Battery
  saver + threshold.
- **Flame brand mark**: `assets/flame.svg` and rewritten `icons/hotspot-hub.svg`
  (dual-lobe flame with warm inner core on a deep slate tile); shown in the
  panel header, tray, and desktop icon.

### Changed
- System card is now diagnostics-only: VPN bypass + MAC filter + channels + the
  four actions (Restart sync / Fix routing / MAC Filter / Docs).
- Hero card actions are a single pill-button row: **Restart / Clients / QR Code**
  (spec: three buttons per card).
- **Button grammar → pill buttons**: `border-radius: 999px` on `QPushButton`.
- **Master switch is always brand blue** (`#3b82f6` track) instead of following
  the Plasma accent — network-admin convention per the redesign spec.
- **SVG rendering is now plugin-independent**: `render_svg()` draws through
  `QSvgRenderer` (QVariant paint) instead of the Qt SVG image/icon-engine
  plugins, which the AppImage's PyQt6 wheels don't ship. This fixes the missing
  app icon in the panel header and the Clients empty-state art inside the
  bundled AppImage.
- **FAULT is transient, not sticky**: a failed start/restart/off now raises
  FAULT for only 2 minutes (`FAULT_WINDOW_S = 120`); a clean, steady "off"
  reports STANDBY. The old `_last_error`-based logic kept the header pill red
  forever after a single stale failure even when the hotspot was simply off.


## [0.4.0] - 2026-10-24

### Added
- New app icon: hub-node broadcast mark on a deep slate tile (works on light
  and dark trays; the live status dot overlays it at bottom-right).
- `assets/` bundle: `banner.svg` (README hero with mark + wordmark + status
  chips) and `clients-empty.svg` (illustrated empty state in the Clients
  dialog when no stations are connected).
- `asset_path()` resolver: finds bundled assets across repo / AppImage /
  system install layouts; install.sh and the AppImage build ship `assets/`.

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
  - **Shell redesign**: header bar with app icon + title/subtitle block and a
    live state pill (lamp + ACTIVE/STANDBY/FAULT) that stays visible while the
    cards scroll; hairline separator under the header; roomier canvas
    (min 420x440, default 500x780, freely resizable via the size grip;
    geometry key bumped to panel_geometry_v2 so old small sizes don't stick).
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
