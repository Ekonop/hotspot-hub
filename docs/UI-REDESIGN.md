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

## Verification note

Redesign was implemented and validated at the source level (`py_compile`,
offscreen render). A screenshot was regenerated in `tools/ui_screenshot.png`
and `docs/screenshots/dropdown.png`; actual visual review on a live Plasma
session was not performed (no image-capable reviewer in this run).