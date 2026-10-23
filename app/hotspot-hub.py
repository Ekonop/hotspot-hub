#!/usr/bin/env python3
"""Hotspot Hub - KDE tray control with dropdown canvas (CachyOS).
Dropdown canvas is extensible: add a PanelSection subclass + register in
DropdownPanel._build_sections(). Privileged ops use passwordless sudo
(/etc/sudoers.d/hotspot-hub), pkexec fallback. No plaintext passwords."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction, QCursor, QFocusEvent, QIcon, QPixmap
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel,
                             QMenu, QMessageBox, QPushButton, QScrollArea,
                             QSystemTrayIcon, QVBoxLayout, QWidget)

AP_IF = os.environ.get("HOTSPOT_HUB_AP_IF", "ap0")
STA_IF = os.environ.get("HOTSPOT_HUB_STA_IF", "wlan0")
HOTSPOT_CON = os.environ.get("HOTSPOT_HUB_CON", "Hotspot-Shared")


def _default_doc() -> str:
    """Return default documentation path from environment or filesystem."""
    env = os.environ.get("HOTSPOT_HUB_DOCS")
    if env:
        return env
    home_doc = Path.home() / "doc" / "hotspot-system" / "README.md"
    if home_doc.exists():
        return str(home_doc)
    return str(Path(__file__).resolve().parent.parent / "docs" / "README.md")


DOC_PATH = _default_doc()
LEASE_FILE = "/var/lib/NetworkManager/dnsmasq-ap0.leases"


def run(cmd: Sequence[str], timeout: int | float = 5) -> str:
    """Run cmd, return stdout. Never raises ('' on error)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except Exception:
        return ""


def run_rc(cmd: Sequence[str], timeout: int | float = 15) -> tuple[int, str]:
    """Run cmd, return (rc, output). Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:
        return 1, str(e)


def active_cons() -> dict[str, str]:
    """Parse `nmcli -t -f NAME,DEVICE con show --active` -> {name: device}."""
    m = {}
    for line in run(["nmcli", "-t", "-f", "NAME,DEVICE",
                     "con", "show", "--active"]).splitlines():
        if ":" in line:
            n, _, d = line.rpartition(":")
            m[n.strip()] = d.strip()
    return m


def iface_info(dev: str) -> dict[str, str]:
    """Parse `iw dev <dev> info` -> {ssid, channel} or {} if missing."""
    out = run(["iw", "dev", dev, "info"])
    if not out or "No such device" in out:
        return {}
    s = re.search(r"^\s*ssid\s+(.+)$", out, re.M)
    c = re.search(r"channel\s+(\d+)", out)
    return {"ssid": s.group(1).strip() if s else "",
            "channel": c.group(1) if c else ""}


def station_dump() -> list[tuple[str, str]]:
    """Parse `iw dev ap0 station dump` -> [(mac, signal)]. [] if AP down."""
    stations, mac, sig = [], "", ""
    for line in run(["iw", "dev", AP_IF, "station", "dump"]).splitlines():
        m = re.match(r"Station\s+([0-9a-fA-F:]{17})", line)
        if m:
            if mac:
                stations.append((mac, sig))
            mac, sig = m.group(1).lower(), ""
        s = re.search(r"signal:\s*(-?\d+\s*dBm)", line)
        if s and mac:
            sig = s.group(1)
    if mac:
        stations.append((mac, sig))
    return stations


def qr_code_png(ssid: str, psk: str) -> bytes | None:
    """Generate WiFi QR code PNG bytes using qrencode CLI. Return None on error."""
    if not shutil.which("qrencode"):
        return None
    payload = f"WIFI:T:WPA;S:{ssid};P:{psk};;"
    try:
        p = subprocess.run(["qrencode", "-s", "6", "-o", "-", payload],
                           capture_output=True, timeout=10)
        if p.returncode == 0 and p.stdout:
            return p.stdout
    except Exception:
        pass
    return None


def warp_status() -> str:
    """One-line `warp-cli --accept-tos status`. Never raises."""
    if not shutil.which("warp-cli"):
        return "not installed"
    out = run(["warp-cli", "--accept-tos", "status"])
    if not out:
        return "unknown"
    m = re.search(r"Status update:\s*(.+)", out)
    return m.group(1).strip().splitlines()[0] if m else out.splitlines()[0][:60]


def sta_name() -> str:
    """Active wifi connection on STA_IF excluding the hotspot itself, or ''."""
    for line in run(["nmcli", "-t", "-f", "NAME,DEVICE",
                     "con", "show", "--active"]).splitlines():
        if ":" in line:
            n, _, d = line.rpartition(":")
            if d.strip() == STA_IF and n.strip() != HOTSPOT_CON:
                return n.strip()
    return ""


def vpn_bypass_on() -> bool:
    """True if hotspot traffic bypasses VPN policy tables into main."""
    out = run(["ip", "rule", "show"])
    return "iif ap0 lookup main" in out or "from 10.42.0.0/24 lookup main" in out


def hotspot_state() -> tuple[str, str, int]:
    """Return (sta_text, ap_text, n_clients); safe with missing interfaces."""
    cons = active_cons()
    sta, ap = iface_info(STA_IF), iface_info(AP_IF)
    on = cons.get(HOTSPOT_CON) == AP_IF
    name = sta_name()
    sta_t = f"STA {sta.get('ssid') or name or '?'} ch{sta.get('channel') or '?'}" if (sta or name) \
        else "STA down"
    ap_t = f"AP {ap.get('ssid') or AP_IF} ch{ap.get('channel') or '?'} ON" if (ap and on) \
        else (f"AP {AP_IF} idle" if ap else "AP off (no ap0)")
    try:
        n = len(station_dump())
    except Exception:
        n = 0
    return sta_t, ap_t, n


def priv(cmd: list[str], timeout: int | float = 30) -> tuple[int, str]:
    """Run privileged cmd without prompt: sudo -n first (sudoers), pkexec fallback."""
    rc, out = run_rc(["sudo", "-n"] + cmd, timeout=timeout)
    if rc == 0:
        return rc, out
    if "password" in out.lower() or "sudo" in out.lower()[:200]:
        if shutil.which("pkexec"):
            return run_rc(["pkexec"] + cmd, timeout=timeout)
    return rc, out


def ensure_ap0() -> tuple[bool, str]:
    """Create ap0 passwordless (sudoers) if missing. Returns (ok, msg)."""
    if iface_info(AP_IF):
        return True, "ap0 exists"
    rc, out = priv(["iw", "phy", "phy0", "interface",
                    "add", AP_IF, "type", "__ap"])
    if rc != 0 and not iface_info(AP_IF):
        return False, f"create ap0 failed: {out[-200:]}"
    priv(["ip", "link", "set", AP_IF, "up"])
    return True, "ap0 created"


def lease_count() -> str:
    """DHCP lease count; sudo -n cat (sudoers). Returns str."""
    try:
        with open(LEASE_FILE) as f:
            return str(sum(1 for ln in f if ln.strip()))
    except (PermissionError, OSError):
        pass
    rc, out = run_rc(["sudo", "-n", "cat", LEASE_FILE], timeout=15)
    if rc == 0 and out:
        return str(sum(1 for ln in out.splitlines() if ln.strip()))
    return "0 (no leases yet)"


# ---------------------------------------------------------------------------
# Dropdown canvas: extensible sections.
# To add future mods: subclass PanelSection, implement refresh(), append an
# instance in DropdownPanel._build_sections().
# ---------------------------------------------------------------------------
class PanelSection(QFrame):
    """Base card for the dropdown canvas. Subclass + override refresh()."""
    title = "Section"

    def __init__(self, controller: HotspotPanel, parent: QWidget | None = None) -> None:
        """Initialize the panel section with controller reference and setup layout."""
        super().__init__(parent)
        self.controller = controller
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 8, 10, 8)
        self.layout.setSpacing(6)
        head = QLabel(f"<b>{self.title}</b>")
        self.layout.addWidget(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(4)
        self.layout.addLayout(self.body)
        self.build_body()

    def build_body(self) -> None:
        """Create widgets. Override in subclass."""

    def refresh(self) -> None:
        """Update widgets from live state. Override in subclass."""

    def row(self, *widgets: QWidget) -> QHBoxLayout:
        """Add a horizontal row of widgets to the section layout."""
        h = QHBoxLayout()
        h.setSpacing(6)
        for w in widgets:
            h.addWidget(w)
        self.body.addLayout(h)
        return h


class HotspotSection(PanelSection):
    title = "Hotspot"

    def build_body(self) -> None:
        """Build Hotspot section widgets and connect button handlers."""
        self.info = QLabel("…")
        self.info.setWordWrap(True)
        self.body.addWidget(self.info)
        self.btn_on = QPushButton("On")
        self.btn_off = QPushButton("Off")
        self.btn_restart = QPushButton("Restart")
        self.btn_clients = QPushButton("Clients…")
        self.btn_qr = QPushButton("QR Code")
        self.btn_on.clicked.connect(self.controller.hotspot_on)
        self.btn_off.clicked.connect(self.controller.hotspot_off)
        self.btn_restart.clicked.connect(self.controller.hotspot_restart)
        self.btn_clients.clicked.connect(self.controller.show_clients)
        self.btn_qr.clicked.connect(self.controller.show_qr_code)
        self.row(self.btn_on, self.btn_off, self.btn_restart)
        self.row(self.btn_clients, self.btn_qr)

    def refresh(self) -> None:
        """Update hotspot and station status labels from live system state."""
        try:
            sta, ap, n = hotspot_state()
            self.info.setText(f"{sta}\n{ap} | clients {n} | leases {lease_count()}")
        except Exception as e:
            self.info.setText(f"Status error: {e}")


class WarpSection(PanelSection):
    title = "WARP"

    def build_body(self) -> None:
        """Build Cloudflare WARP section widgets and connect button handlers."""
        self.info = QLabel("…")
        self.body.addWidget(self.info)
        self.btn_on = QPushButton("Connect")
        self.btn_off = QPushButton("Disconnect")
        self.btn_on.clicked.connect(lambda: self.controller.warp("connect"))
        self.btn_off.clicked.connect(lambda: self.controller.warp("disconnect"))
        self.row(self.btn_on, self.btn_off)

    def refresh(self) -> None:
        """Update Cloudflare WARP connection status label."""
        self.info.setText(f"Status: {warp_status()}")


class SystemSection(PanelSection):
    title = "System"

    def build_body(self) -> None:
        """Build System section widgets and connect action buttons."""
        self.info = QLabel("…")
        self.body.addWidget(self.info)
        self.btn_sync = QPushButton("Restart channel-sync")
        self.btn_route = QPushButton("Fix client routing")
        self.btn_docs = QPushButton("Docs")
        self.btn_sync.clicked.connect(self.controller.sync_restart)
        self.btn_route.clicked.connect(self.controller.fix_routing)
        self.btn_docs.clicked.connect(lambda: run(["xdg-open", DOC_PATH]))
        self.row(self.btn_sync, self.btn_route)
        self.row(self.btn_docs)

    def refresh(self) -> None:
        """Update channel-sync service status, VPN bypass state, and channels."""
        _, out = run_rc(["systemctl", "is-active",
                         "hotspot-channelsync.service"], timeout=10)
        state = out.strip().splitlines()[0] if out else "unknown"
        sta, ap = iface_info(STA_IF), iface_info(AP_IF)
        bypass = "on" if vpn_bypass_on() else "OFF"
        self.info.setText(f"channel-sync: {state} | VPN bypass: {bypass}\n"
                          f"wlan0 ch{sta.get('channel', '?')} | "
                          f"ap0 ch{ap.get('channel', '?')}")


class ClientsDialog(QDialog):
    """Dialog displaying connected Wi-Fi clients with kick capability."""

    def __init__(self, controller: HotspotPanel, parent: QWidget | None = None) -> None:
        """Initialize the client list dialog."""
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle(f"Connected Clients ({AP_IF})")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        self.summary_label = QLabel("…")
        layout.addWidget(self.summary_label)

        self.clients_container = QWidget()
        self.clients_layout = QVBoxLayout(self.clients_container)
        self.clients_layout.setContentsMargins(0, 0, 0, 0)
        self.clients_layout.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.clients_container)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh_clients)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        bottom.addWidget(self.btn_refresh)
        bottom.addStretch()
        bottom.addWidget(self.btn_close)
        layout.addLayout(bottom)

        self.refresh_clients()

    def refresh_clients(self) -> None:
        """Refresh station list and rebuild client rows."""
        while self.clients_layout.count():
            item = self.clients_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            stations = station_dump()
            err = ""
        except Exception as e:
            stations, err = [], str(e)

        summary = f"Stations: {len(stations)} | DHCP leases: {lease_count()}"
        if err:
            summary += f"\nNote: {err}"
        self.summary_label.setText(summary)

        if not stations:
            empty = QLabel(f"No clients (or AP interface {AP_IF} is down).")
            empty.setStyleSheet("color: gray;")
            self.clients_layout.addWidget(empty)
        else:
            for mac, sig in stations:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(4, 2, 4, 2)
                row_layout.setSpacing(8)

                info = QLabel(f"{mac}  ({sig or 'signal ?'})")
                btn_kick = QPushButton("Disconnect")
                btn_kick.setFixedWidth(90)
                btn_kick.clicked.connect(lambda _, m=mac: self._kick(m))

                row_layout.addWidget(info, 1)
                row_layout.addWidget(btn_kick)
                self.clients_layout.addWidget(row)

        self.clients_layout.addStretch()

    def _kick(self, mac: str) -> None:
        """Disconnect specified station MAC and refresh the view."""
        self.controller.disconnect_client(mac)
        self.refresh_clients()


class DropdownPanel(QWidget):
    """Frameless dropdown canvas near the tray. Add sections below.

    Wayland note: Qt.Popup grab is rejected by compositors without a
    transient parent, so this uses Qt.Tool (no grab) + hide-on-focus-out.
    """

    def __init__(self, controller: HotspotPanel) -> None:
        """Initialize frameless dropdown window and add section components."""
        super().__init__(
            None,
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint)
        self.controller = controller
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setMinimumWidth(340)
        self.setMaximumWidth(420)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        top = QHBoxLayout()
        header = QLabel("<b>Hotspot Hub</b> — dropdown canvas")
        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(32)
        close_btn.clicked.connect(self.hide)
        top.addWidget(header, 1)
        top.addWidget(close_btn)
        root.addLayout(top)
        self.sections: list[PanelSection] = []
        self._build_sections(root)
        hint = QLabel("Right-click tray for menu • Quit there. "
                      "Add future mods as new PanelSection.")
        hint.setWordWrap(True)
        hint.setEnabled(False)
        root.addWidget(hint)

    def _build_sections(self, root: QVBoxLayout) -> None:
        """Instantiate and register panel section cards into the layout."""
        # EXTENSION POINT: append future PanelSection instances here.
        for cls in (HotspotSection, WarpSection, SystemSection):
            sec = cls(self.controller, self)
            self.sections.append(sec)
            root.addWidget(sec)

    def refresh_all(self) -> None:
        """Refresh all registered panel sections."""
        for s in self.sections:
            try:
                s.refresh()
            except Exception:
                pass

    def focusOutEvent(self, event: QFocusEvent) -> None:
        """Hide panel when focus is lost and cursor is outside the window."""
        # Wayland-safe auto-hide: Popup grab unavailable, so hide when
        # focus leaves the canvas (clicking outside moves focus away).
        if not self.underMouse():
            self.hide()
        super().focusOutEvent(event)


class HotspotPanel:
    def __init__(self) -> None:
        """Initialize Qt application, tray icon, context menu, and refresh timer."""
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        try:  # silence "Icon theme gnome not found" on KDE; prefer breeze
            if QIcon.themeName() in ("", "gnome"):
                QIcon.setThemeName("breeze")
        except Exception:
            pass
        if not QSystemTrayIcon.isSystemTrayAvailable():
            print("WARNING: no system tray detected", file=sys.stderr)
        icon = QIcon.fromTheme("hotspot-hub",
                               QIcon.fromTheme("network-wireless-hotspot",
                                               QIcon.fromTheme("network-wireless")))
        self.tray = QSystemTrayIcon(icon, self.app)
        self.menu = QMenu()  # right-click fallback menu
        self.tray.setContextMenu(self.menu)
        self.panel = DropdownPanel(self)
        self._build_menu()
        self.tray.activated.connect(self._on_tray_activated)
        self.timer = QTimer()  # auto-refresh tooltip + menu + panel every 5 s
        self.timer.timeout.connect(self.refresh)
        self.timer.start(5000)
        self._known_macs: set[str] = {mac.lower() for mac, _ in station_dump()}
        self.client_timer = QTimer()  # poll station_dump every 10 s for notifications
        self.client_timer.timeout.connect(self._poll_stations)
        self.client_timer.start(10000)
        self.refresh()
        self.tray.show()

    def _act(self, label: str, slot: Callable[..., Any], disabled: bool = False) -> QAction:
        """Add an action item to the context menu and return it."""
        a = QAction(label, self.menu)
        a.setEnabled(not disabled)
        a.triggered.connect(slot)
        self.menu.addAction(a)
        return a

    def _build_menu(self) -> None:
        """Build actions and separators for tray context menu."""
        self.status_action = self._act("Status…", lambda: None, disabled=True)
        self.menu.addSeparator()
        self._act("Open panel", self.toggle_panel)
        self._act("Hotspot On", self.hotspot_on)
        self._act("Hotspot Off", self.hotspot_off)
        self.menu.addSeparator()
        self._act("Quit", self.app.quit)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray icon trigger events to toggle dropdown panel."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_panel()

    def toggle_panel(self) -> None:
        """Toggle panel visibility and position near the current mouse cursor."""
        if self.panel.isVisible():
            self.panel.hide()
            return
        self.panel.refresh_all()
        pos = QCursor.pos()
        geo = self.panel.sizeHint()
        self.panel.move(pos.x() - geo.width() // 2, pos.y() - geo.height() - 20)
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def refresh(self) -> None:
        """Refresh tray tooltip, status action text, and visible panel sections."""
        try:
            sta, ap, n = hotspot_state()
            text = f"{sta} | {ap} | clients {n} | WARP {warp_status()}"
        except Exception as e:
            text = f"Status error: {e}"
        self.status_action.setText(text[:160])
        self.tray.setToolTip("Hotspot Hub\n" + text)
        if self.panel.isVisible():
            self.panel.refresh_all()

    def notify(self, title: str, msg: str) -> None:
        """Show a tray notification popup and refresh panel."""
        try:
            self.tray.showMessage(title, msg,
                                  QSystemTrayIcon.MessageIcon.Information, 5000)
        except Exception:
            pass
        if self.panel.isVisible():
            self.panel.refresh_all()

    def _nm_up(self) -> tuple[int, str]:
        """Activate the NetworkManager connection profile."""
        return run_rc(["nmcli", "con", "up", HOTSPOT_CON], timeout=30)

    def hotspot_on(self) -> None:
        """Ensure ap0 exists and activate hotspot connection."""
        ok, msg = ensure_ap0()
        if not ok:
            return self.notify("Hotspot On failed", msg)
        rc, out = self._nm_up()
        self.notify("Hotspot On", "Hotspot-Shared activated on ap0." if rc == 0
                    else f"Hotspot On failed: {(out[-300:] or 'nmcli failed')}")
        self.refresh()

    def hotspot_off(self) -> None:
        """Deactivate hotspot connection."""
        rc, out = run_rc(["nmcli", "con", "down", HOTSPOT_CON], timeout=30)
        self.notify("Hotspot Off", "Hotspot-Shared deactivated." if rc == 0
                    else (out[-300:] or "nmcli down failed"))
        self.refresh()

    def hotspot_restart(self) -> None:
        """Cycle hotspot connection off and on."""
        ok, msg = ensure_ap0()
        if not ok:
            return self.notify("Hotspot Restart failed", msg)
        run_rc(["nmcli", "con", "down", HOTSPOT_CON], timeout=30)
        rc, out = self._nm_up()
        self.notify("Hotspot Restart", "Hotspot-Shared restarted." if rc == 0
                    else f"Restart failed: {(out[-300:] or 'nmcli failed')}")
        self.refresh()

    def _poll_stations(self) -> None:
        """Poll station_dump() every 10s and notify on client connect or disconnect."""
        try:
            current_stations = station_dump()
            current_macs = {mac.lower() for mac, _ in current_stations}
        except Exception:
            return

        connected = current_macs - self._known_macs
        disconnected = self._known_macs - current_macs

        for mac in sorted(connected):
            self.tray.showMessage("Client Connected",
                                  f"Client {mac} connected to hotspot",
                                  QSystemTrayIcon.MessageIcon.Information, 5000)
        for mac in sorted(disconnected):
            self.tray.showMessage("Client Disconnected",
                                  f"Client {mac} disconnected from hotspot",
                                  QSystemTrayIcon.MessageIcon.Information, 5000)

        self._known_macs = current_macs

    def disconnect_client(self, mac: str) -> tuple[int, str]:
        """Disconnect a station via `iw dev ap0 station del <mac>` and refresh."""
        rc, out = priv(["iw", "dev", AP_IF, "station", "del", mac])
        if rc == 0:
            self.notify("Client Disconnected", f"Station {mac} disconnected.")
        else:
            self.notify("Disconnect Failed", f"Failed to disconnect {mac}: {(out[-200:] or 'iw failed')}")
        self.refresh()
        self._poll_stations()
        return rc, out

    def show_clients(self) -> None:
        """Display dialog showing connected clients with kick capability."""
        dialog = ClientsDialog(self, self.panel if self.panel.isVisible() else None)
        dialog.exec()

    def show_qr_code(self) -> None:
        """Read SSID and PSK, generate WiFi QR code via qrencode, and display in a dialog."""
        if not shutil.which("qrencode"):
            QMessageBox.warning(
                self.panel if self.panel.isVisible() else None,
                "QR Code",
                "The 'qrencode' CLI tool is not installed.\n"
                "Please install qrencode (e.g. pacman -S qrencode) to generate QR codes.",
            )
            return

        psk = run(["nmcli", "-s", "-g", "802-11-wireless-security.psk",
                   "connection", "show", HOTSPOT_CON])
        ssid = run(["nmcli", "-s", "-g", "802-11-wireless.ssid",
                    "connection", "show", HOTSPOT_CON])
        if not ssid:
            ssid = iface_info(AP_IF).get("ssid", "")
        if not ssid:
            ssid = "Hotspot-Hub"

        if not psk:
            QMessageBox.warning(
                self.panel if self.panel.isVisible() else None,
                "QR Code",
                f"Could not read hotspot password for connection '{HOTSPOT_CON}'.",
            )
            return

        png_data = qr_code_png(ssid, psk)
        if not png_data:
            QMessageBox.warning(
                self.panel if self.panel.isVisible() else None,
                "QR Code",
                "Failed to generate QR code using qrencode.",
            )
            return

        pix = QPixmap()
        if not pix.loadFromData(png_data, "PNG"):
            QMessageBox.warning(
                self.panel if self.panel.isVisible() else None,
                "QR Code",
                "Failed to render QR code image.",
            )
            return

        dialog = QDialog(self.panel if self.panel.isVisible() else None)
        dialog.setWindowTitle("Hotspot QR Code")
        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setSpacing(10)
        dlg_layout.setContentsMargins(16, 16, 16, 16)

        info_lbl = QLabel(f"<b>SSID:</b> {ssid}")
        info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dlg_layout.addWidget(info_lbl)

        img_lbl = QLabel()
        img_lbl.setPixmap(pix)
        img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dlg_layout.addWidget(img_lbl)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        dlg_layout.addWidget(close_btn)

        dialog.exec()

    def warp(self, op: str) -> None:
        """Run warp-cli connect or disconnect command."""
        rc, out = run_rc(["warp-cli", "--accept-tos", op], timeout=30)
        self.notify(f"WARP {op}", (out[-300:] or "done") if rc == 0
                    else (out[-300:] or "warp-cli failed"))
        self.refresh()

    def sync_restart(self) -> None:
        """Restart hotspot-channelsync systemd service."""
        rc, out = priv(["systemctl", "restart",
                        "hotspot-channelsync.service"])
        self.notify("Channel-sync", "Service restarted." if rc == 0
                    else (out[-300:] or "restart failed (sudoers?)"))

    def fix_routing(self) -> None:
        """Run hotspot-channelsync script to enforce VPN bypass routing."""
        rc, out = priv(["/usr/local/bin/hotspot-channelsync.sh"])
        if rc == 0:
            self.notify("Client routing",
                        "VPN bypass enforced (pref 50) + channel synced."
                        if vpn_bypass_on() else "Sync ran; verify bypass below.")
        else:
            self.notify("Client routing", (out[-300:] or "sync failed"))

    def run(self) -> None:
        """Run the Qt application event loop."""
        sys.exit(self.app.exec())


if __name__ == "__main__":
    HotspotPanel().run()
