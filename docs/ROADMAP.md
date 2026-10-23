# Hotspot Hub — Roadmap & Optimization Plan

## Current state (v0.1.0)
- PyQt6 tray app with dropdown canvas (Hotspot/WARP/System cards)
- Channel-sync (poll 10s + NM dispatcher)
- VPN bypass at pref 50 (self-repairing)
- Arch packaging + direct install
- Docs: README, CONFIGURATION, ARCHITECTURE, TROUBLESHOOTING
- Features: QR code, client kick, connect/disconnect notifications, type hints, status indicator on tray icon, CLI tool, bandwidth monitor, dark mode, auto-start toggle, keyboard shortcut

---

## 1. Features to add

### A. UI/UX
- [x] **Status indicator on tray icon** — change icon color based on hotspot state (green=on, grey=off, red=error). ✅
- [x] **Client list with kick button** — show connected clients with MAC/IP/signal, allow disconnecting a client.
- [x] **QR code for SSID/PSK** — generate and display a QR code for easy client onboarding.
- [x] **Bandwidth monitor** — show real-time throughput on interfaces (wlan0 and ap0). ✅
- [x] **Dark mode support** — detect dark mode via QPalette and adapt cards, text, and buttons to Plasma theme colors. ✅
- [x] **Keyboard shortcuts** — global shortcut (Ctrl+Alt+H, configurable) to toggle hotspot on/off. ✅

### B. Functionality
- [ ] **Multi-profile support** — allow multiple hotspot profiles (different SSIDs), switch between them.
- [ ] **Guest network** — separate SSID with client isolation and bandwidth limit.
- [x] **Scheduled hotspot** — auto on/off at specified times. ✅
- [x] **Battery-aware** — auto-disable hotspot when battery low (integrate with the hibernate-at-5% work). ✅
- [x] **Client MAC filtering** — allow/deny list for connected devices. ✅
- [ ] **Bandwidth throttling** — per-client or total bandwidth limits via tc.
- [x] **IPv6 support** — optional IPv6 on AP interface. ✅
- [x] **5GHz band support** — currently only 2.4/5GHz auto; allow manual band selection. ✅
- [x] **WPA3 support detection** — auto-detect firmware SAE capability instead of hardcoding WPA2. ✅

### C. Integration
- [ ] **D-Bus interface** — expose hotspot state/control via D-Bus for other apps/scripts.
- [x] **CLI tool** — `hotspot-hub-cli status`, `on`, `off`, `restart`, `clients`, `kick <mac>`, `qr`. ✅
- [ ] **Plasma applet** — native QML plasmoid instead of PyQt6 tray (better Wayland integration).
- [x] **Notification on client connect/disconnect** — desktop notification.
- [x] **Auto-start on boot** — optional, configurable via checkbox and `--no-autostart` installer flag. ✅

### D. Monitoring/Logging
- [x] **Persistent logs** — structured logging to file with rotation.
- [x] **Health check** — periodic self-test (AP reachable, DHCP working, internet via AP).
- [x] **Metrics export** — Prometheus/node_exporter compatible metrics.

---

## 2. Optimizations

### A. Performance
- [x] **Reduce poll frequency** — 10s poll is aggressive; use NM events + 30s fallback.
- [x] **Lazy refresh** — only refresh UI when visible or on event, not every 5s.
- [x] **Cache nmcli/iw output** — avoid redundant subprocess calls.
- [ ] **Async subprocess** — use QProcess or asyncio instead of blocking subprocess.run.

### B. Reliability
- [x] **Retry logic** — retry failed nmcli/iw commands with backoff.
- [x] **Graceful degradation** — if ap0 can't be created, show helpful error instead of crashing.
- [x] **Config validation** — validate env vars and profile at startup. ✅
- [ ] **Atomic profile updates** — backup profile before modifying, restore on failure.

### C. Security
- [x] **Least-privilege sudoers** — scope sudoers to only required commands.
- [x] **No shell injection** — audit all subprocess calls for injection (currently uses list args, good).
- [x] **Secrets handling** — ensure PSK never logged or displayed.
- [ ] **Firewall rules review** — verify UFW rules are minimal.

### D. Code quality
- [ ] **Type hints** — add Python type annotations. ✅ (done via agy)
- [x] **Unit tests** — test parsing functions (iface_info, station_dump, etc.) with mock data.
- [x] **Linting** — ruff/flake8 + black formatting.
- [x] **CI/CD** — GitHub Actions for lint + package build.
- [x] **Error handling** — replace bare `except Exception` with specific exceptions.

---

## 3. Bug fixes / known issues

- [ ] **Client routing bypass** — currently re-enforced every 10s; should be event-driven.
- [ ] **AP on wrong channel** — if STA roams, AP restarts; may cause brief disconnect.
- [ ] **WPA3 detection** — hardcoded WPA2; should probe firmware capability.
- [x] **icon theme fallback** — `QIcon.fromTheme` may not find hotspot-hub icon on all systems.
- [ ] **Wayland popup** — uses Qt.Tool (no grab); focus-out hide may be unreliable.
- [x] **Hardcoded phy0** — fixed, now auto-detects WiFi PHY. ✅

---

## 4. Documentation improvements

- [ ] **API documentation** — docstrings for all functions (partially done).
- [x] **Contributing guide** — expand CONTRIBUTING.md with dev setup, coding style. ✅
- [x] **Changelog** — CHANGELOG.md with version history. ✅
- [x] **Man page** — `man hotspot-hub-cli` for CLI usage. ✅
- [x] **Screenshots** — add UI screenshots to README.

---

## 5. Packaging

- [ ] **Flatpak** — package as Flatpak for broader distribution.
- [ ] **AUR submission** — submit to AUR for `yay`/`paru` installation.
- [ ] **systemd user service** — optional user-level service for autostart.
- [ ] **Config file** — `~/.config/hotspot-hub/config` instead of env vars only.

---

## Priority order (suggested)

1. **High**: Status indicator, CLI tool, bandwidth monitor, dark mode, auto-start
2. **Medium**: Reduce poll, lazy refresh, cache, retry, graceful degradation, health check, persistent logs
3. **Low**: MAC filtering, throttling, 5GHz, WPA3, multi-profile, guest, scheduled, battery-aware, D-Bus, Plasma applet, metrics

---

## Implementation Notes

- All features should maintain the `PanelSection` extension point pattern.
- Use `priv()` for privileged operations.
- Use env vars for configuration (consistent with existing pattern).
- Test on CachyOS/Arch with MediaTek MT7922.
- Commit after each feature.