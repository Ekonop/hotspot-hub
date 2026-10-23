# Hotspot Hub — Roadmap & Optimization Plan

## Current state (v0.1.0)
- PyQt6 tray app with dropdown canvas (Hotspot/WARP/System cards)
- Channel-sync (poll 10s + NM dispatcher)
- VPN bypass at pref 50 (self-repairing)
- Arch packaging + direct install
- Docs: README, CONFIGURATION, ARCHITECTURE, TROUBLESHOOTING

---

## 1. Features to add

### A. UI/UX
- [ ] **Status indicator on tray icon** — change icon color based on hotspot state (green=on, grey=off, red=error).
- [x] **Client list with kick button** — show connected clients with MAC/IP/signal, allow disconnecting a client.
- [x] **QR code for SSID/PSK** — generate and display a QR code for easy client onboarding.
- [ ] **Bandwidth monitor** — show real-time up/down throughput per client and total.
- [ ] **Dark mode support** — respect Plasma theme (already partial).
- [ ] **Keyboard shortcuts** — global shortcut to toggle hotspot on/off.

### B. Functionality
- [ ] **Multi-profile support** — allow multiple hotspot profiles (different SSIDs), switch between them.
- [ ] **Guest network** — separate SSID with client isolation and bandwidth limit.
- [ ] **Scheduled hotspot** — auto on/off at specified times.
- [ ] **Battery-aware** — auto-disable hotspot when battery low (integrate with the hibernate-at-5% work).
- [ ] **Client MAC filtering** — allow/deny list for connected devices.
- [ ] **Bandwidth throttling** — per-client or total bandwidth limits via tc.
- [ ] **IPv6 support** — optional IPv6 on AP interface.
- [ ] **5GHz band support** — currently only 2.4/5GHz auto; allow manual band selection.
- [ ] **WPA3 support detection** — auto-detect firmware SAE capability instead of hardcoding WPA2.

### C. Integration
- [ ] **D-Bus interface** — expose hotspot state/control via D-Bus for other apps/scripts.
- [ ] **CLI tool** — `hotspot-hub status`, `hotspot-hub on/off`, `hotspot-hub clients`.
- [ ] **Plasma applet** — native QML plasmoid instead of PyQt6 tray (better Wayland integration).
- [x] **Notification on client connect/disconnect** — desktop notification.
- [ ] **Auto-start on boot** — optional, configurable.

### D. Monitoring/Logging
- [ ] **Persistent logs** — structured logging to file with rotation.
- [ ] **Health check** — periodic self-test (AP reachable, DHCP working, internet via AP).
- [ ] **Metrics export** — Prometheus/node_exporter compatible metrics.

---

## 2. Optimizations

### A. Performance
- [ ] **Reduce poll frequency** — 10s poll is aggressive; use NM events + 30s fallback.
- [ ] **Lazy refresh** — only refresh UI when visible or on event, not every 5s.
- [ ] **Cache nmcli/iw output** — avoid redundant subprocess calls.
- [ ] **Async subprocess** — use QProcess or asyncio instead of blocking subprocess.run.

### B. Reliability
- [ ] **Retry logic** — retry failed nmcli/iw commands with backoff.
- [ ] **Graceful degradation** — if ap0 can't be created, show helpful error instead of crashing.
- [ ] **Config validation** — validate env vars and profile at startup.
- [ ] **Atomic profile updates** — backup profile before modifying, restore on failure.

### C. Security
- [ ] **Least-privilege sudoers** — scope sudoers to only required commands.
- [ ] **No shell injection** — audit all subprocess calls for injection (currently uses list args, good).
- [ ] **Secrets handling** — ensure PSK never logged or displayed.
- [ ] **Firewall rules review** — verify UFW rules are minimal.

### D. Code quality
- [ ] **Type hints** — add Python type annotations.
- [ ] **Unit tests** — test parsing functions (iface_info, station_dump, etc.) with mock data.
- [ ] **Linting** — ruff/flake8 + black formatting.
- [ ] **CI/CD** — GitHub Actions for lint + package build.
- [ ] **Error handling** — replace bare `except Exception` with specific exceptions.

---

## 3. Bug fixes / known issues

- [ ] **Client routing bypass** — currently re-enforced every 10s; should be event-driven.
- [ ] **AP on wrong channel** — if STA roams, AP restarts; may cause brief disconnect.
- [ ] **WPA3 detection** — hardcoded WPA2; should probe firmware capability.
- [ ] **icon theme fallback** — `QIcon.fromTheme` may not find hotspot-hub icon on all systems.
- [ ] **Wayland popup** — uses Qt.Tool (no grab); focus-out hide may be unreliable.

---

## 4. Documentation improvements

- [ ] **API documentation** — docstrings for all functions (partially done).
- [ ] **Contributing guide** — expand CONTRIBUTING.md with dev setup, coding style.
- [ ] **Changelog** — CHANGELOG.md with version history.
- [ ] **Man page** — `man hotspot-hub` for CLI usage.
- [ ] **Screenshots** — add UI screenshots to README.

---

## 5. Packaging

- [ ] **Flatpak** — package as Flatpak for broader distribution.
- [ ] **AUR submission** — submit to AUR for `yay`/`paru` installation.
- [ ] **systemd user service** — optional user-level service for autostart.
- [ ] **Config file** — `~/.config/hotspot-hub/config` instead of env vars only.

---

## Priority order (suggested)

1. **High**: Client list with kick, QR code, notification on connect
2. **Medium**: Bandwidth monitor, multi-profile, scheduled hotspot
3. **Low**: Flatpak, AUR, metrics export, IPv6

---

## How to contribute

See `CONTRIBUTING.md`. Extension point: `DropdownPanel._build_sections()` in
`app/hotspot-hub.py`. Add a `PanelSection` subclass and register it there.