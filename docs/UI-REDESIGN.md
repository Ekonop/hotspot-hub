# UI Redesign — Hotspot Hub dropdown canvas

Direction and decisions for the 0.4.0 UI pass, following the oil-ui workflow.

## Direction card

| Field | Decision |
| --- | --- |
| **北极星 / North star** | The panel reads like an aircraft instrument deck: the user opens it, reads the state in one glance, and flips the master switch. Status is the protagonist; everything else is quiet readout. |
| **调性 / Tone** | 安静 (quiet) · 精致 (refined) · 中密 (medium density) · 中分量 (medium weight) · 庄重 (calm). Evidence: it is a daily-driver system tray utility for a WiFi radio on a laptop — high-frequency control, low celebration. |
| **标杆与取舍 / Benchmarks** | Category: OS network control panels. Studied: macOS System Settings → Sharing/Network, Windows Mobile-Hotspot settings, Tailscale + Cloudflare WARP tray panels. Kept: status-led layout, master toggle as the one hero control, mono readouts for technical data. Replaced: button-pair "On/Off" → single animated master switch; labelled cards → engraved section labels with status chips; removed developer-facing hint text from the UI. |
| **生成引擎 / Engine** | "Night-shift instrument panel": state = indicator lamp (unique: it breathes when live), master switch = the toggle, metrics = JetBrains Mono readouts, section headers = engraved small-caps labels with letter-spacing. Derives from the product being a radio/network controller, not a generic dashboard. |
| **首屏骨架 / First screen** | User comes to check state + flip the master switch. Hero card on top: lamp + big state word + one-line readout (SSID · channel · clients · leases · security) + master toggle with restart; secondary actions below. WARP / Bandwidth / System cards follow. |
| **字体 / Type** | Manrope: hero state 20px/700, card headings 15px/700, section labels 10.5px/700 + 1.2px letter-spacing (QFont), body 13px/400, buttons 13px/600. JetBrains Mono: readouts 16px/600, metrics 12px. |
| **色彩 / Color** | Adaptive dark/light from Plasma palette (KColorScheme + QPalette). Semantic states: `#22c55e` live, `#94a3b8` standby, `#ef4444` fault. Hero surface = card colour tinted 8% toward Plasma accent. Muted text role for secondary info. No generic purple/gradient stock treatment. |
| **图标 / Icons** | None per heading; text glyphs only (✕, ↓, ↑). Keeps the engraved/instrument feel and avoids icon-soup. |
| **控件语法 / Control grammar** | One language everywhere: radius 14px cards / 8px controls / pill toggle; 1px borders; hover = accent border + lighten; pressed = darken; focus = accent ring. Disabled = muted 40%. |
| **动效 / Motion** | 1) Panel show: fade + subtle rise, 160 ms, OutCubic. 2) Master switch: knob slide 180 ms OutCubic with 1.3× overshoot (OutBack) — "咔嗒：拨动主开关". 3) Indicator lamp: 1.6 s breathing halo while live — "指示灯呼吸". Button hover/press state changes complement these (states, not loops). |
| **结构突破 / Structural break** | The state word + lamp behave like an instrument ("ACTIVE / STANDBY / FAULT") instead of a plain info card, and the master switch replaces the On/Off button pair. |
| **记忆点 / Memorable moment** | Flipping the master switch: the lamp turns live and starts breathing, the state word recolours — the result happens on the thing being controlled, not in a toast. |
| **放弃什么 / Dropped** | Dev-facing hint text ("Add future mods as new PanelSection"), glassmorphism blur, per-card icon headers, decorative gradients, extra shadows. |

## Visual roles

| Role | Implementation |
| --- | --- |
| Canvas | `QWidget#dropdownCanvas`, 16px radius, 1px border, canvas bg |
| Card | `QFrame[panelCard="true"]`, 14px radius, hover = accent border |
| Hero | `QFrame[heroCard="true"]`, accent-tinted surface + accent border on hover |
| Section label | `QLabel[sectionLabel="true"]`, QFont letter-spacing, muted |
| Status chip | `QLabel[statusChip="true"]`, pill, per-state bg set in code |
| Readout | `QLabel[heroMetric="true"]`, JetBrains Mono 16px |
| Master switch | `AnimatedToggle` (custom-painted QCheckBox, 56×30) |
| Lamp | `IndicatorLamp` (custom widget, breathing halo) |
| Buttons | default / `primary` (accent fill) / `quiet` (borderless, hover bg) |

## 0.4.1 pass (five-card spec + flame brand + FAULT fix)

Follow-up user spec on top of the 0.4.0 deck — adjustments, not a new direction:

- **Flame brand mark** replaces the hub-node mark (`assets/flame.svg` +
  `icons/hotspot-hub.svg`): dual-lobe flame, warm outer gradient
  (`#fb923c → #dc2626`) with a gold inner core, on the same deep slate tile.
  Used in the panel header, tray, and desktop icon.
- **Fifth card: Health** — `GW <ip> reachable · AP UP · DHCP ok (n lease)`
  mono readout with green `OK` / red `ISSUES` chip; the lifecycle controls
  (Start on boot, IPv6, Scheduled 08:00/22:00, Battery saver + threshold)
  moved here from System. The System card is now diagnostics-only
  (VPN bypass · MAC filter · channels · four actions).
- **Hero actions** are a three-button pill row: Restart / Clients / QR Code
  (per spec: three buttons per card).
- **Pill buttons**: `border-radius: 999px` on QPushButton — compact pill
  grammar per the network-admin control panel spec.
- **Blue master switch**: `AnimatedToggle` checked track is fixed brand blue
  `#3b82f6` (network-admin convention) instead of the Plasma accent.
- **SVG, plugin-independent** (`render_svg` via `QSvgRenderer`): the AppImage's
  PyQt6 wheels ship no SVG icon/image-engine plugin, which silently dropped
  the header icon and the Clients empty-state art in bundled builds.
- **FAULT is transient** (`FAULT_WINDOW_S = 120`): only failed
  start/restart/off actions raise FAULT, and only for 2 minutes. A steady
  OFF reports STANDBY — the old sticky `_last_error` left the header pill
  permanently red after a single stale failure (exactly what the
  2026-10-25 report screenshot showed).

## Verification note

Redesign was implemented and validated at the source level (`py_compile`,
ruff, 22 unit tests, offscreen render with forced Breeze-Dark palette). The
FAULT-window logic was verified with simulated states (off + stale error →
off; failure 5 s ago → error; active on ap0 → on; nmcli broken → error). A
screenshot was regenerated in `tools/ui_screenshot.png` and
`docs/screenshots/dropdown.png`; actual visual review on a live Plasma
session was not performed (no image-capable reviewer in this run).
## 0.5.0 pass (credentials + device names + QR passphrase)

Feature additions on top of the shipped five-card deck (no design-direction
change):

- **Configure pill** on the Hotspot hero card (right of the band selector).
  Opens the name & passphrase dialog: SSID + WPA2 passphrase fields with
  live validation (1–32 / 8–63 chars or 64 hex), "Show passphrase", blank
  passphrase keeps the current one, Save gated on validity. Applied to the
  live NM profile; the hotspot restarts through the async action bus when
  active so the new SSID broadcasts immediately. Mirror in the CLI:
  `hotspot-hub-cli set-credentials --ssid X [--password Y]`.
- **Client names** in ClientsDialog. Per-station resolution priority:
  user-assigned name (persisted `~/.config/hotspot-hub/devicenames.json`
  via the per-client **Name…** button) → DHCP hostname from the dnsmasq
  lease → **mDNS reverse lookup** (`getent`/`avahi-resolve-address`), which
  is what names phones using private MACs that send no DHCP hostname →
  MAC OUI vendor (`/usr/share/hwdata/oui.txt`). Header counts named devices.
- **QR dialog** now displays the live passphrase in plaintext (mono,
  selectable) with a Copy button (`Copied ✓` transient feedback).
