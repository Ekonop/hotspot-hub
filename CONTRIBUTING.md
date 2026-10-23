# Contributing to Hotspot Hub

Thank you for your interest in contributing to Hotspot Hub! This document details development setup, coding standards, the `PanelSection` extension architecture, and the pull request process.

---

## 1. Development Environment Setup

Hotspot Hub is designed for modern Linux systems (developed and tested primarily on Arch Linux and CachyOS with KDE Plasma Wayland).

### Prerequisites

Install core runtime and development dependencies:

```bash
# Arch Linux / CachyOS
sudo pacman -S --needed python python-pyqt6 networkmanager iw qrencode iproute2 base-devel
```

### Running from Source

You can run the tray app and CLI directly from the git checkout without installing:

```bash
# Launch tray UI
python3 app/hotspot-hub.py

# Test CLI
python3 cli/hotspot-hub-cli.py status
python3 cli/hotspot-hub-cli.py band
python3 cli/hotspot-hub-cli.py mac-filter list
```

### Privileged Operations & Sudoers

Certain actions (creating the `ap0` virtual interface, resetting policy routes, kicking stations with `iw dev ap0 station del`) require elevated privileges.

Install the development sudoers configuration for passwordless operation:

```bash
sudo install -Dm440 src/hotspot-hub.sudoers /etc/sudoers.d/hotspot-hub
sudo visudo -c
```

---

## 2. Coding Style & Architectural Guidelines

### Python Conventions
- **PEP 8**: Follow standard Python conventions (4-space indentation, clear variable names).
- **Type Annotations**: All new functions, methods, and class attributes must have strict type hints (`from __future__ import annotations`, `typing.Any`, `collections.abc.Sequence`, etc.).
- **Docstrings**: Provide clear, concise docstrings for all classes, methods, and helper functions explaining arguments, behavior, and return types.
- **Subprocess Security**:
  - Never execute commands with `shell=True`. Always pass commands as argument sequences (`Sequence[str]`).
  - Use `run()` or `run_rc()` helpers to benefit from automatic retries, backoff, and caching.
- **Theme & Wayland Compatibility**:
  - Do not hardcode colors in widget styles; use `get_theme_stylesheet()` and palette references to respect both dark and light modes.
  - Dropdown panels use `Qt.WindowType.Tool` rather than popup grabs to ensure reliability under Wayland compositors.

### PanelSection Extension Point Pattern
Hotspot Hub’s dropdown canvas is built around modular cards extending `PanelSection`. To add a new card to the UI:

1. Subclass `PanelSection`:
   ```python
   class CustomSection(PanelSection):
       title = "Custom Card"

       def build_body(self) -> None:
           """Instantiate widgets and layout for this card."""
           self.label = QLabel("Initial status")
           self.body.addWidget(self.label)

       def refresh(self) -> None:
           """Update widgets from live system state."""
           self.label.setText("Updated status")
   ```
2. Register your subclass in `DropdownPanel._build_sections()`:
   ```python
   def _build_sections(self, root: QVBoxLayout) -> None:
       for cls in (HotspotSection, WarpSection, BandwidthSection, CustomSection, SystemSection):
           sec = cls(self.controller, self)
           self.sections.append(sec)
           root.addWidget(sec)
   ```

### Strict No-Secrets Policy
Never commit personal SSIDs, passphrases, client MAC addresses, private IPs, DHCP lease dumps, or user home paths.

Always audit before staging commits:
```bash
grep -rniE 'psk|passphrase|([0-9a-f]{2}:){5}[0-9a-f]{2}' --exclude-dir=.git .
```

---

## 3. Testing & Verification

Run the automated syntax and compilation checks before submitting changes:

```bash
make check
```

`make check` validates:
- Python bytecode compilation: `python3 -m py_compile app/hotspot-hub.py cli/hotspot-hub-cli.py`
- Shell script syntax: `bash -n install.sh uninstall.sh scripts/setup-profile.sh helpers/*.sh`

### Manual Validation Checklist
When submitting UI or feature changes, verify:
- [ ] Dropdown panel is resizable using the corner size grip, and geometry persists across restarts.
- [ ] MAC filtering allows adding/removing entries via UI and CLI, and kicks unauthorized stations.
- [ ] 5GHz band selection correctly validates channel availability on the Wi-Fi adapter.
- [ ] WPA3 detection correctly probes `iw phy` and displays active security status.
- [ ] CLI commands (`status`, `band`, `mac-filter`, `clients`, `kick`) return expected outputs.

---

## 4. Pull Request Process

1. **Branching**: Create a feature branch off `main` (e.g., `feature/mac-filtering` or `fix/channel-sync`).
2. **Commit Messages**: Write clear, descriptive commit messages summarizing what changed and why.
3. **Hardware Context**: In your pull request description, please include:
   - Wireless hardware info: `lspci -k | grep -A3 Net`
   - Wi-Fi PHY interface combination capabilities: `iw list | grep -A10 'valid interface combinations'`
   - Desktop environment and compositor (e.g., KDE Plasma 6.x Wayland, GNOME 4x, etc.)
4. **Documentation**: If your PR introduces user-facing flags, configuration parameters, or commands, update the relevant files in `docs/`, `CHANGELOG.md`, and the man page (`man/hotspot-hub-cli.1`).
