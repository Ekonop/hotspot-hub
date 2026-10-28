#!/usr/bin/env python3
"""Hotspot Hub — Flet + pystray system tray application.

Full-featured modern UI with system tray integration.
All backend logic shared with core services and app/hotspot-hub.py.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from core import config as _cfg
from core import services as hub

# Merge config exports into hub for backwards compatibility with hub.* calls
for _name in (
    "ALLOWED_MACS_FILE",
    "BLOCKED_MACS_FILE",
    "CONFIG_FILE",
    "load_app_config",
    "save_app_config",
):
    setattr(hub, _name, getattr(_cfg, _name))

import flet as ft
from PIL import Image, ImageDraw


def resolve_asset(filename: str, subdir: str = "assets") -> str | None:
    """Resolve asset or icon across local repository and system installation."""
    for base in (REPO, "/usr/share/hotspot-hub", "/usr/share"):
        if base == "/usr/share" and subdir == "icons":
            candidates = [
                f"/usr/share/icons/hicolor/scalable/apps/{filename}",
                f"/usr/share/icons/hicolor/128x128/apps/{filename}",
                f"/usr/share/pixmaps/{filename}",
            ]
            for c in candidates:
                if os.path.isfile(c):
                    return c
        p = os.path.join(base, subdir, filename)
        if os.path.isfile(p):
            return p
    return None

# ── Shared state & events ────────────────────────────────────────────
_show_window = threading.Event()
_show_clients = threading.Event()
_show_qr = threading.Event()
_show_configure = threading.Event()
_show_mac_filter = threading.Event()
_quit_app = threading.Event()

# ── Design tokens ────────────────────────────────────────────────────
PRIMARY = "#3B82F6"
SUCCESS = "#22C55E"
WARNING = "#EAB308"
DANGER = "#EF4444"
MUTED_GRAY = "#94A3B8"

BG_DARK = "#1C1C1E"
BG_LIGHT = "#F2F2F7"
CARD_DARK = "#2C2C2E"
CARD_LIGHT = "#FFFFFF"
TEXT_DARK = "#F8FAFC"
TEXT_LIGHT = "#0F172A"
MUTED_DARK = "#94A3B8"
MUTED_LIGHT = "#64748B"

RADIUS_CARD = 16
RADIUS_BTN = 12
FAULT_WINDOW_S = 120


def _default_doc() -> str:
    env = os.environ.get("HOTSPOT_HUB_DOCS")
    if env:
        return env
    home_doc = Path.home() / "doc" / "hotspot-system" / "README.md"
    if home_doc.exists():
        return str(home_doc)
    return str(Path(REPO) / "docs" / "README.md")


DOC_PATH = _default_doc()


def is_dark(page: ft.Page) -> bool:
    return page.theme_mode == ft.ThemeMode.DARK


def card_bg(page: ft.Page) -> str:
    return CARD_DARK if is_dark(page) else CARD_LIGHT


def text_color(page: ft.Page) -> str:
    return TEXT_DARK if is_dark(page) else TEXT_LIGHT


def muted_color(page: ft.Page) -> str:
    return MUTED_DARK if is_dark(page) else MUTED_LIGHT


def page_bg(page: ft.Page) -> str:
    return BG_DARK if is_dark(page) else BG_LIGHT


def copy_to_clipboard(text: str) -> bool:
    """Best effort clipboard copy on Linux."""
    if shutil.which("wl-copy"):
        try:
            subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=False)
            return True
        except Exception:
            pass
    if shutil.which("xclip"):
        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text.encode("utf-8"),
                check=False,
            )
            return True
        except Exception:
            pass
    return False


# ── Tray icon helpers ────────────────────────────────────────────────
def create_tray_icon(state: str = "off") -> Image.Image:
    """Create a colored tray icon based on hotspot state."""
    colors = {"on": "#22C55E", "off": "#94A3B8", "error": "#EF4444"}
    color = colors.get(state, colors["off"])
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Broadcast rings
    draw.ellipse([12, 12, 52, 52], fill=color)
    draw.ellipse([22, 22, 42, 42], fill="#FFFFFF")
    draw.ellipse([28, 28, 36, 36], fill=color)
    # Status dot (bottom-right)
    draw.ellipse([44, 44, 60, 60], fill=color, outline="#FFFFFF", width=2)
    return img


_tray_icon_ref: dict[str, Any] = {"icon": None, "state": "off"}


def update_tray_icon(state: str) -> None:
    """Update the tray icon color based on hotspot state."""
    if _tray_icon_ref["icon"] is not None and _tray_icon_ref["state"] != state:
        try:
            _tray_icon_ref["icon"].icon = create_tray_icon(state)
            _tray_icon_ref["state"] = state
        except Exception:
            pass


def tray_thread() -> None:
    """Run pystray icon with comprehensive right-click context menu."""
    import pystray

    def run_bg(fn: Any) -> None:
        threading.Thread(target=fn, daemon=True).start()

    def on_open(icon: Any, item: Any) -> None:
        _show_window.set()

    def on_clients(icon: Any, item: Any) -> None:
        _show_clients.set()

    def on_qr(icon: Any, item: Any) -> None:
        _show_qr.set()

    def on_configure(icon: Any, item: Any) -> None:
        _show_configure.set()

    def on_mac_filter(icon: Any, item: Any) -> None:
        _show_mac_filter.set()

    def on_quit(icon: Any, item: Any) -> None:
        _quit_app.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open Hotspot Hub", on_open, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Hotspot On", lambda i, it: run_bg(hub.hotspot_on_service)),
        pystray.MenuItem("Hotspot Off", lambda i, it: run_bg(hub.hotspot_off_service)),
        pystray.MenuItem("Restart Hotspot", lambda i, it: run_bg(hub.hotspot_restart_service)),
        pystray.MenuItem(
            "Band Preference",
            pystray.Menu(
                pystray.MenuItem("Auto", lambda i, it: run_bg(lambda: hub.modify_hotspot_band("Auto"))),
                pystray.MenuItem("2.4GHz", lambda i, it: run_bg(lambda: hub.modify_hotspot_band("2.4GHz"))),
                pystray.MenuItem("5GHz", lambda i, it: run_bg(lambda: hub.modify_hotspot_band("5GHz"))),
            ),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("WARP Connect", lambda i, it: run_bg(lambda: hub.warp("connect"))),
        pystray.MenuItem("WARP Disconnect", lambda i, it: run_bg(lambda: hub.warp("disconnect"))),
        pystray.MenuItem("Restart Channel Sync", lambda i, it: run_bg(hub.sync_restart_service)),
        pystray.MenuItem("Fix Routing Table", lambda i, it: run_bg(hub.fix_routing_service)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Connected Clients…", on_clients),
        pystray.MenuItem("QR Code…", on_qr),
        pystray.MenuItem("Configure SSID & Passphrase…", on_configure),
        pystray.MenuItem("MAC Address Filter…", on_mac_filter),
        pystray.MenuItem("Documentation", lambda i, it: subprocess.Popen(["xdg-open", DOC_PATH])),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon("hotspot-hub", create_tray_icon("off"), "Hotspot Hub", menu)
    _tray_icon_ref["icon"] = icon
    try:
        icon.run()
    except Exception:
        pass


# ── Flet UI ──────────────────────────────────────────────────────────
def main(page: ft.Page) -> None:
    page.title = "Hotspot Hub"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = page_bg(page)
    page.padding = 16

    if not page.web:
        page.window.width = 540
        page.window.height = 840
        page.window.resizable = True
        page.window.min_width = 440
        page.window.min_height = 560
        page.window.prevent_close = True

        try:
            icon_p = resolve_asset("hotspot-hub.svg", "icons")
            if icon_p:
                page.window.icon = icon_p
        except Exception:
            pass

    # Theme tracking registry for dynamic recoloring without page reloads
    themed_cards: list[ft.Container] = []
    themed_texts: list[ft.Text] = []
    themed_muted: list[ft.Text] = []

    def register_card(c: ft.Container) -> ft.Container:
        themed_cards.append(c)
        return c

    def register_text(t: ft.Text) -> ft.Text:
        themed_texts.append(t)
        return t

    def register_muted(t: ft.Text) -> ft.Text:
        themed_muted.append(t)
        return t

    def ios_card(content: ft.Control, padding: int = 16) -> ft.Container:
        c = ft.Container(
            content=content,
            bgcolor=card_bg(page),
            border_radius=RADIUS_CARD,
            padding=ft.Padding(
                left=padding, top=padding, right=padding, bottom=padding
            ),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=16,
                color=ft.Colors.with_opacity(0.12, ft.Colors.BLACK),
                offset=ft.Offset(0, 2),
            ),
        )
        return register_card(c)

    def ios_button(
        label: str,
        on_click: Any,
        variant: str = "primary",
        icon: Any = None,
        compact: bool = False,
    ) -> ft.FilledButton:
        colors = {
            "primary": (PRIMARY, ft.Colors.WHITE),
            "success": (SUCCESS, ft.Colors.WHITE),
            "danger": (DANGER, ft.Colors.WHITE),
            "quiet": (
                ft.Colors.with_opacity(0.12, PRIMARY),
                PRIMARY,
            ),
            "warning": (WARNING, ft.Colors.WHITE),
        }
        bg, fg = colors.get(
            variant, (ft.Colors.with_opacity(0.12, PRIMARY), PRIMARY)
        )
        h = 34 if compact else 42
        pv = 6 if compact else 10
        ph = 14 if compact else 18
        items: list[ft.Control] = []
        if icon:
            items.append(ft.Icon(icon, size=15 if compact else 18, color=fg))
        items.append(
            ft.Text(
                label,
                size=12 if compact else 14,
                weight=ft.FontWeight.W_600,
                color=fg,
            )
        )
        return ft.FilledButton(
            content=ft.Row(
                items,
                spacing=5,
                alignment=ft.MainAxisAlignment.CENTER,
                tight=True,
            ),
            style=ft.ButtonStyle(
                color=fg,
                bgcolor=bg,
                shape=ft.RoundedRectangleBorder(radius=999),
                padding=ft.Padding(left=ph, top=pv, right=ph, bottom=pv),
                elevation=0,
                overlay_color=ft.Colors.with_opacity(0.12, fg),
            ),
            on_click=on_click,
            height=h,
        )

    def status_chip(text: str, color: str) -> ft.Container:
        return ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        width=7, height=7, border_radius=4, bgcolor=color
                    ),
                    ft.Text(
                        text, size=11, weight=ft.FontWeight.W_700, color=color
                    ),
                ],
                spacing=5,
                tight=True,
            ),
            bgcolor=ft.Colors.with_opacity(0.12, color),
            border_radius=999,
            padding=ft.Padding(left=10, top=4, right=10, bottom=4),
        )

    def show_snack(msg: str, color: str = PRIMARY) -> None:
        try:
            page.show_dialog(
                ft.SnackBar(
                    ft.Text(msg, color=ft.Colors.WHITE),
                    bgcolor=color,
                    duration=3000,
                )
            )
        except Exception:
            pass

    # ── Action Worker (non-blocking async operations) ─────────────────
    action_lock = threading.Lock()
    action_busy = False
    last_error_time: float = 0.0
    pending_toggle: bool | None = None

    def run_async_action(
        fn: Any, on_done: Any = None, feedback: str | None = None
    ) -> None:
        nonlocal action_busy, last_error_time
        if action_busy:
            show_snack("Another action is in progress…", WARNING)
            return
        action_busy = True

        def worker() -> None:
            nonlocal action_busy, last_error_time
            try:
                res = fn()
                if on_done:
                    on_done(res)
                if feedback:
                    show_snack(feedback, SUCCESS)
            except Exception as exc:
                last_error_time = time.monotonic()
                show_snack(f"Error: {exc}", DANGER)
            finally:
                action_busy = False
                refresh_all()

        threading.Thread(target=worker, daemon=True).start()

    # ── State and Bandwidth Tracking ─────────────────────────────────
    bw_last_stats: dict[str, tuple[int, int]] = hub.read_net_dev_bytes()
    bw_last_time: float = time.monotonic()
    schedule_last_action: str | None = None
    battery_action_taken = False

    # ── Header controls ──────────────────────────────────────────────
    header_lamp = ft.Container(
        width=10, height=10, border_radius=5, bgcolor=MUTED_GRAY
    )
    header_pill_text = ft.Text(
        "STANDBY", size=12, weight=ft.FontWeight.W_700, color=MUTED_GRAY
    )
    header_pill = ft.Container(
        content=ft.Row(
            [header_lamp, header_pill_text], spacing=6, tight=True
        ),
        bgcolor=ft.Colors.with_opacity(0.12, MUTED_GRAY),
        border_radius=999,
        padding=ft.Padding(left=12, top=6, right=12, bottom=6),
    )

    theme_btn = ft.IconButton(
        icon=ft.Icons.LIGHT_MODE,
        icon_color=MUTED_DARK,
        tooltip="Toggle Dark/Light Mode",
        on_click=lambda e: toggle_theme(),
    )

    flame_path = resolve_asset("flame.svg", "assets")
    flame_widget: ft.Control
    if flame_path:
        flame_widget = ft.Image(src=flame_path, width=28, height=28)
    else:
        flame_widget = ft.Icon(ft.Icons.WIFI, size=24, color=PRIMARY)

    title_text = register_text(
        ft.Text("Hotspot Hub", size=22, weight=ft.FontWeight.W_700)
    )
    subtitle_text = register_muted(
        ft.Text("Concurrent STA + AP control", size=13)
    )

    header = ft.Row(
        [
            ft.Row(
                [
                    flame_widget,
                    ft.Column([title_text, subtitle_text], spacing=1),
                ],
                spacing=10,
                expand=True,
            ),
            header_pill,
            theme_btn,
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    # ── Card 1: Hotspot (Hero Card) ──────────────────────────────────
    hero_lamp = ft.Container(
        width=14, height=14, border_radius=7, bgcolor=MUTED_GRAY
    )
    hero_state_text = ft.Text(
        "STANDBY", size=20, weight=ft.FontWeight.W_800, color=MUTED_GRAY
    )

    cfg = hub.load_app_config()

    band_dropdown = ft.Dropdown(
        label="Band",
        options=[
            ft.dropdown.Option("Auto"),
            ft.dropdown.Option("2.4GHz"),
            ft.dropdown.Option("5GHz"),
        ],
        value=cfg.get("band", "Auto"),
        width=110,
        text_size=12,
        content_padding=ft.Padding(left=10, top=4, right=10, bottom=4),
        on_select=lambda e: on_band_change(e.control.value),
    )

    def on_band_change(val: str) -> None:
        if not val:
            return
        run_async_action(
            lambda: hub.modify_hotspot_band(val),
            feedback=f"Band changed to {val}",
        )

    btn_configure = ios_button(
        "Configure",
        lambda e: show_configure_dialog(),
        variant="quiet",
        compact=True,
    )

    hero_readout = register_text(
        ft.Text("Loading…", size=14, font_family="monospace")
    )

    master_switch = ft.Switch(
        value=False,
        active_color=PRIMARY,
        on_change=lambda e: on_master_toggle(e.control.value),
    )
    master_label = register_text(
        ft.Text("Off", size=14, weight=ft.FontWeight.W_700)
    )

    def on_master_toggle(checked: bool) -> None:
        nonlocal pending_toggle
        if action_busy:
            master_switch.value = not checked
            page.update()
            show_snack("Busy executing another operation…", WARNING)
            return

        is_on = hub.detect_state() == "on"
        if checked == is_on:
            return

        pending_toggle = checked
        if checked:
            run_async_action(hub.hotspot_on_service, feedback="Hotspot ON")
        else:
            run_async_action(hub.hotspot_off_service, feedback="Hotspot OFF")

    btn_restart = ios_button(
        "Restart",
        lambda e: run_async_action(
            hub.hotspot_restart_service, feedback="Hotspot restarted"
        ),
        variant="quiet",
        compact=True,
    )
    btn_clients = ios_button(
        "Clients",
        lambda e: show_clients_dialog(),
        variant="quiet",
        compact=True,
    )
    btn_qr = ios_button(
        "QR Code", lambda e: show_qr_dialog(), variant="quiet", compact=True
    )

    hotspot_card = ios_card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Row(
                            [hero_lamp, hero_state_text],
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Container(expand=True),
                        band_dropdown,
                        btn_configure,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                hero_readout,
                ft.Row(
                    [master_switch, master_label],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    [btn_restart, btn_clients, btn_qr],
                    spacing=8,
                    alignment=ft.MainAxisAlignment.START,
                ),
            ],
            spacing=12,
        )
    )

    # ── Card 2: Wi-Fi Uplink (Prominent & Highlighted) ────────────────
    wifi_chip = status_chip("DISCONNECTED", MUTED_GRAY)
    wifi_icon = ft.Icon(ft.Icons.WIFI_ROUNDED, size=26, color=PRIMARY)
    wifi_ssid_text = register_text(
        ft.Text("Scanning…", size=18, weight=ft.FontWeight.W_800)
    )

    badge_speed_text = ft.Text("… MBit/s", size=11, weight=ft.FontWeight.W_700, color=PRIMARY)
    badge_speed = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.SPEED, size=14, color=PRIMARY),
                badge_speed_text,
            ],
            spacing=4,
            tight=True,
        ),
        bgcolor=ft.Colors.with_opacity(0.14, PRIMARY),
        border_radius=999,
        padding=ft.Padding(left=10, top=4, right=10, bottom=4),
    )

    badge_sig_text = ft.Text("… dBm", size=11, weight=ft.FontWeight.W_700, color=SUCCESS)
    badge_signal = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.SIGNAL_WIFI_4_BAR, size=14, color=SUCCESS),
                badge_sig_text,
            ],
            spacing=4,
            tight=True,
        ),
        bgcolor=ft.Colors.with_opacity(0.14, SUCCESS),
        border_radius=999,
        padding=ft.Padding(left=10, top=4, right=10, bottom=4),
    )

    badge_ch_text = ft.Text("ch…", size=11, weight=ft.FontWeight.W_700, color=MUTED_GRAY)
    badge_channel = ft.Container(
        content=badge_ch_text,
        bgcolor=ft.Colors.with_opacity(0.14, MUTED_GRAY),
        border_radius=999,
        padding=ft.Padding(left=10, top=4, right=10, bottom=4),
    )

    wifi_health_text = register_text(
        ft.Text("Health: checking gateway…", size=12, font_family="monospace")
    )

    wifi_card = ios_card(
        ft.Column(
            [
                ft.Row(
                    [
                        register_text(
                            ft.Text(
                                "Wi-Fi Uplink",
                                size=16,
                                weight=ft.FontWeight.W_700,
                            )
                        ),
                        wifi_chip,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Row(
                    [wifi_icon, wifi_ssid_text],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    [badge_speed, badge_signal, badge_channel],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                wifi_health_text,
            ],
            spacing=10,
        )
    )

    # ── Card 3: Bandwidth ────────────────────────────────────────────
    bw_chip = status_chip("IDLE", MUTED_GRAY)
    bw_readout = register_text(
        ft.Text("Sampling…", size=13, font_family="monospace")
    )

    bw_card = ios_card(
        ft.Column(
            [
                ft.Row(
                    [
                        register_text(
                            ft.Text(
                                "Bandwidth",
                                size=16,
                                weight=ft.FontWeight.W_600,
                            )
                        ),
                        bw_chip,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                bw_readout,
            ],
            spacing=10,
        )
    )

    # ── Card 3: WARP ─────────────────────────────────────────────────
    warp_chip = status_chip("OFF", MUTED_GRAY)
    warp_info = register_muted(
        ft.Text(
            "Status: unknown",
            size=12,
            font_family="monospace",
            color=muted_color(page),
        )
    )

    btn_warp_connect = ios_button(
        "Connect",
        lambda e: run_async_action(
            lambda: hub.warp("connect"), feedback="WARP connected"
        ),
        variant="primary",
        compact=True,
    )
    btn_warp_disconnect = ios_button(
        "Disconnect",
        lambda e: run_async_action(
            lambda: hub.warp("disconnect"), feedback="WARP disconnected"
        ),
        variant="quiet",
        compact=True,
    )

    warp_card = ios_card(
        ft.Column(
            [
                ft.Row(
                    [
                        register_text(
                            ft.Text(
                                "WARP", size=16, weight=ft.FontWeight.W_600
                            )
                        ),
                        warp_chip,
                        ft.Container(expand=True),
                        ft.Row(
                            [btn_warp_connect, btn_warp_disconnect], spacing=6
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                warp_info,
            ],
            spacing=10,
        )
    )

    # ── Card 4: System ───────────────────────────────────────────────
    sys_chip = status_chip("SYNC OFF", DANGER)
    sys_info = register_muted(
        ft.Text("Loading…", size=12, font_family="monospace")
    )
    sys_conn_info = register_text(
        ft.Text("Connectivity: checking…", size=12, font_family="monospace")
    )

    chk_reconnect = ft.Switch(
        value=bool(cfg.get("reconnect_enabled", True)),
        on_change=lambda e: on_toggle_reconnect(e.control.value),
    )

    def on_toggle_reconnect(val: bool) -> None:
        c = hub.load_app_config()
        c["reconnect_enabled"] = val
        hub.save_app_config(c)
        refresh_sys()

    btn_sync_restart = ios_button(
        "Restart sync",
        lambda e: run_async_action(
            hub.sync_restart_service, feedback="Channel sync restarted"
        ),
        variant="quiet",
        compact=True,
    )
    btn_fix_routing = ios_button(
        "Fix routing",
        lambda e: run_async_action(
            hub.fix_routing_service, feedback="Routing table restored"
        ),
        variant="quiet",
        compact=True,
    )
    btn_mac_filter = ios_button(
        "MAC Filter",
        lambda e: show_mac_filter_dialog(),
        variant="quiet",
        compact=True,
    )
    btn_docs = ios_button(
        "Docs",
        lambda e: subprocess.Popen(["xdg-open", DOC_PATH]),
        variant="quiet",
        compact=True,
    )

    sys_card = ios_card(
        ft.Column(
            [
                ft.Row(
                    [
                        register_text(
                            ft.Text(
                                "System", size=16, weight=ft.FontWeight.W_600
                            )
                        ),
                        sys_chip,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                sys_info,
                sys_conn_info,
                ft.Row(
                    [
                        register_text(
                            ft.Text("Smart reconnect", size=13, expand=True)
                        ),
                        chk_reconnect,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Row(
                    [
                        btn_sync_restart,
                        btn_fix_routing,
                        btn_mac_filter,
                        btn_docs,
                    ],
                    spacing=6,
                    wrap=True,
                ),
            ],
            spacing=10,
        )
    )

    # ── Card 6: System Lifecycle ─────────────────────────────────────
    health_chip = status_chip("HEALTHY", SUCCESS)
    health_info = register_text(
        ft.Text("AP: checking… · DHCP: checking…", size=12, font_family="monospace")
    )

    chk_autostart = ft.Switch(
        value=hub.is_autostart_enabled(),
        on_change=lambda e: on_toggle_autostart(e.control.value),
    )

    def on_toggle_autostart(val: bool) -> None:
        ok = hub.set_autostart(val)
        if not ok:
            chk_autostart.value = hub.is_autostart_enabled()
            page.update()
            show_snack("Failed to update autostart setting", DANGER)
        else:
            show_snack(
                f"Autostart {'enabled' if val else 'disabled'}", SUCCESS
            )

    chk_ipv6 = ft.Switch(
        value=bool(cfg.get("ipv6_enabled", False)),
        disabled=not hub.detect_ipv6_support(),
        on_change=lambda e: on_toggle_ipv6(e.control.value),
    )

    def on_toggle_ipv6(val: bool) -> None:
        c = hub.load_app_config()
        c["ipv6_enabled"] = val
        hub.save_app_config(c)
        if hub.detect_state() == "on":
            flag = "0" if val else "1"
            hub.priv([
                "sysctl",
                "-w",
                f"net.ipv6.conf.{hub.AP_IF}.disable_ipv6={flag}",
            ])
        show_snack(f"IPv6 {'enabled' if val else 'disabled'}", SUCCESS)

    chk_schedule = ft.Switch(
        value=bool(cfg.get("schedule_enabled", False)),
        on_change=lambda e: on_toggle_schedule(e.control.value),
    )

    txt_sched_on = ft.TextField(
        value=str(cfg.get("schedule_on", "08:00")),
        label="On",
        width=85,
        dense=True,
        text_size=12,
        on_change=lambda e: on_sched_time_change(),
    )
    txt_sched_off = ft.TextField(
        value=str(cfg.get("schedule_off", "22:00")),
        label="Off",
        width=85,
        dense=True,
        text_size=12,
        on_change=lambda e: on_sched_time_change(),
    )

    def on_toggle_schedule(val: bool) -> None:
        c = hub.load_app_config()
        c["schedule_enabled"] = val
        hub.save_app_config(c)

    def on_sched_time_change() -> None:
        c = hub.load_app_config()
        c["schedule_on"] = txt_sched_on.value.strip()
        c["schedule_off"] = txt_sched_off.value.strip()
        hub.save_app_config(c)

    chk_battery = ft.Switch(
        value=bool(cfg.get("battery_saver_enabled", False)),
        on_change=lambda e: on_toggle_battery(e.control.value),
    )

    slider_battery = ft.Slider(
        min=5,
        max=50,
        divisions=9,
        value=float(cfg.get("battery_threshold", 20)),
        label="{value}%",
        on_change=lambda e: on_battery_threshold_change(int(e.control.value)),
    )
    lbl_battery_val = register_muted(
        ft.Text(f"{cfg.get('battery_threshold', 20)}%", size=12)
    )

    def on_toggle_battery(val: bool) -> None:
        c = hub.load_app_config()
        c["battery_saver_enabled"] = val
        hub.save_app_config(c)

    def on_battery_threshold_change(val: int) -> None:
        lbl_battery_val.value = f"{val}%"
        page.update()
        c = hub.load_app_config()
        c["battery_threshold"] = val
        hub.save_app_config(c)

    health_card = ios_card(
        ft.Column(
            [
                ft.Row(
                    [
                        register_text(
                            ft.Text(
                                "System Lifecycle", size=16, weight=ft.FontWeight.W_600
                            )
                        ),
                        health_chip,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                health_info,
                ft.Row(
                    [
                        register_text(
                            ft.Text("Start on boot", size=13, expand=True)
                        ),
                        chk_autostart,
                        ft.Container(width=16),
                        register_text(ft.Text("IPv6", size=13, expand=True)),
                        chk_ipv6,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Row(
                    [
                        register_text(
                            ft.Text("Scheduled", size=13, expand=True)
                        ),
                        chk_schedule,
                        ft.Container(width=10),
                        txt_sched_on,
                        txt_sched_off,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Row(
                    [
                        register_text(
                            ft.Text("Battery saver", size=13, expand=True)
                        ),
                        chk_battery,
                        ft.Container(width=10),
                        ft.Container(content=slider_battery, width=150),
                        lbl_battery_val,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            ],
            spacing=10,
        )
    )

    # ── Refresh Functions ────────────────────────────────────────────
    def refresh_hotspot() -> None:
        try:
            cons = hub.active_cons()
            active = cons.get(hub.HOTSPOT_CON) == hub.AP_IF
            now = time.monotonic()
            in_fault = (
                last_error_time > 0 and (now - last_error_time) < FAULT_WINDOW_S
            )

            if in_fault:
                state_str = "FAULT"
                state_color = DANGER
                tray_state = "error"
            elif active:
                state_str = "ACTIVE"
                state_color = SUCCESS
                tray_state = "on"
            else:
                state_str = "STANDBY"
                state_color = MUTED_GRAY
                tray_state = "off"

            # Header pill
            header_pill_text.value = state_str
            header_pill_text.color = state_color
            header_lamp.bgcolor = state_color
            header_pill.bgcolor = ft.Colors.with_opacity(0.12, state_color)

            # Hero card
            hero_state_text.value = state_str
            hero_state_text.color = state_color
            hero_lamp.bgcolor = state_color

            # Master switch
            target_toggle = pending_toggle if pending_toggle is not None else active
            master_switch.value = target_toggle
            master_label.value = "On" if active else "Off"
            master_label.color = state_color if active else text_color(page)

            # Instrument readout
            sec = hub.get_hotspot_security()
            ap = hub.iface_info(hub.AP_IF)
            ssid = ap.get("ssid") or hub.HOTSPOT_CON
            ch = ap.get("channel") or "?"
            _, _, n = hub.hotspot_state()
            leases = str(hub.lease_count()).split()[0]
            hero_readout.value = (
                f"{ssid} · ch{ch} · {n} client{'s' if n != 1 else ''} · "
                f"{leases} lease{'s' if leases != '1' else ''} · {sec}"
            )

            # Band selector
            c = hub.load_app_config()
            band_dropdown.value = c.get("band", "Auto")

            update_tray_icon(tray_state)
        except Exception as e:
            hero_readout.value = f"Status error: {e}"

    def refresh_bw() -> None:
        nonlocal bw_last_stats, bw_last_time
        try:
            now = time.monotonic()
            stats = hub.read_net_dev_bytes()
            dt = now - bw_last_time if bw_last_time > 0 else 5.0

            if dt > 15.0:
                bw_last_stats = stats
                bw_last_time = now
                bw_readout.value = (
                    f"{hub.STA_IF} sampling…\n{hub.AP_IF} sampling…"
                )
                return

            if dt <= 0:
                dt = 5.0

            def _rates(iface: str) -> str:
                if iface not in stats or iface not in bw_last_stats:
                    return f"↓{hub.format_rate(0)} ↑{hub.format_rate(0)}"
                rx, tx = stats[iface]
                prev_rx, prev_tx = bw_last_stats[iface]
                rx_rate = (
                    max(0.0, (rx - prev_rx) / dt) if rx >= prev_rx else 0.0
                )
                tx_rate = (
                    max(0.0, (tx - prev_tx) / dt) if tx >= prev_tx else 0.0
                )
                return f"↓{hub.format_rate(rx_rate)} ↑{hub.format_rate(tx_rate)}"

            bw_readout.value = (
                f"{hub.STA_IF} {_rates(hub.STA_IF)}\n{hub.AP_IF} {_rates(hub.AP_IF)}"
            )
            bw_last_stats = stats
            bw_last_time = now

            is_live = hub.detect_state() == "on"
            bw_chip.content.controls[1].value = "LIVE" if is_live else "IDLE"
            c_val = SUCCESS if is_live else MUTED_GRAY
            bw_chip.content.controls[0].bgcolor = c_val
            bw_chip.content.controls[1].color = c_val
            bw_chip.bgcolor = ft.Colors.with_opacity(0.12, c_val)
        except Exception as e:
            bw_readout.value = f"Bandwidth error: {e}"

    def refresh_warp() -> None:
        try:
            st = hub.warp_status()
            warp_info.value = f"Status: {st}"
            low = st.lower()
            if "connected" in low:
                chip_txt, chip_col = "LIVE", SUCCESS
            elif "not installed" in low:
                chip_txt, chip_col = "MISSING", MUTED_GRAY
            else:
                chip_txt, chip_col = "OFF", MUTED_GRAY
            warp_chip.content.controls[1].value = chip_txt
            warp_chip.content.controls[0].bgcolor = chip_col
            warp_chip.content.controls[1].color = chip_col
            warp_chip.bgcolor = ft.Colors.with_opacity(0.12, chip_col)
        except Exception as e:
            warp_info.value = f"WARP error: {e}"

    def refresh_sys() -> None:
        try:
            _, out = hub.run_rc(
                ["systemctl", "is-active", "hotspot-channelsync.service"],
                timeout=10,
                max_retries=1,
            )
            sync_active = (out or "").strip() == "active"
            chip_txt = "SYNC" if sync_active else "SYNC OFF"
            chip_col = SUCCESS if sync_active else DANGER
            sys_chip.content.controls[1].value = chip_txt
            sys_chip.content.controls[0].bgcolor = chip_col
            sys_chip.content.controls[1].color = chip_col
            sys_chip.bgcolor = ft.Colors.with_opacity(0.12, chip_col)

            c = hub.load_app_config()
            allowed = hub.load_mac_file(hub.ALLOWED_MACS_FILE)
            blocked = hub.load_mac_file(hub.BLOCKED_MACS_FILE)
            mf_enabled = c.get("mac_filter_enabled", False)
            mf_mode = c.get("mac_filter_mode", "allow")
            mf_status = (
                f"Active {mf_mode}" if mf_enabled else "OFF"
            ) + f" · {len(allowed)} allowed · {len(blocked)} blocked"
            bypass = "on" if hub.vpn_bypass_on() else "OFF"

            sta = hub.iface_info(hub.STA_IF)
            ap = hub.iface_info(hub.AP_IF)

            lines = [f"VPN bypass: {bypass} · MAC filter: {mf_status}"]
            if hub.DISABLED_FLAG.exists():
                lines.append("Hotspot: manually stopped")
            sta_link = hub.sta_link_info(hub.STA_IF)
            if sta_link.get("connected") and sta_link.get("ssid"):
                sta_desc = f"{hub.STA_IF} ({sta_link['ssid']} · {sta_link['bitrate']}) ch{sta.get('channel', '?')}"
            else:
                sta_desc = f"{hub.STA_IF} ch{sta.get('channel', '?')}"
            lines.append(f"{sta_desc} | {hub.AP_IF} ch{ap.get('channel', '?')}")
            sys_info.value = "\n".join(lines)

            ok, reason = hub.check_connectivity()
            if ok:
                sys_conn_info.value = "Connectivity: OK"
                sys_conn_info.color = SUCCESS
            else:
                sys_conn_info.value = f"Connectivity: Limited ({reason})"
                sys_conn_info.color = WARNING

            chk_reconnect.value = bool(c.get("reconnect_enabled", True))
        except Exception as e:
            sys_info.value = f"System error: {e}"

    def refresh_health() -> None:
        try:
            sta = hub.sta_link_info(hub.STA_IF)
            sta_connected = sta.get("connected") and bool(sta.get("ssid"))

            if sta_connected:
                wifi_ssid_text.value = sta["ssid"]
                speed = sta.get("bitrate") or "Unknown"
                sig = sta.get("signal") or ""
                badge_speed_text.value = speed
                badge_sig_text.value = sig or "Sig ok"
                badge_ch_text.value = f"ch{hub.iface_info(hub.STA_IF).get('channel', '?')}"

                wifi_chip.content.controls[1].value = "CONNECTED"
                wifi_chip.content.controls[0].bgcolor = SUCCESS
                wifi_chip.content.controls[1].color = SUCCESS
                wifi_chip.bgcolor = ft.Colors.with_opacity(0.14, SUCCESS)
            else:
                wifi_ssid_text.value = "Disconnected (STA down)"
                badge_speed_text.value = "0 MBit/s"
                badge_sig_text.value = "No signal"
                badge_ch_text.value = "ch--"

                wifi_chip.content.controls[1].value = "OFFLINE"
                wifi_chip.content.controls[0].bgcolor = MUTED_GRAY
                wifi_chip.content.controls[1].color = MUTED_GRAY
                wifi_chip.bgcolor = ft.Colors.with_opacity(0.14, MUTED_GRAY)

            hc = hub.health_check()
            ok = bool(hc.get("healthy", False))
            gw = hc.get("gateway_ip", "?")
            gw_ok = hc.get("gateway_ok", False)
            gw_probe = hc.get("gateway_probe", "")
            probe_str = f" ({gw_probe})" if gw_probe == "icmp-blocked" else ""
            ap_up = hc.get("ap_up", False)
            leases_ok = hc.get("leases_ok", False)
            leases = hc.get("leases_count", "?")

            wifi_health_text.value = (
                f"Gateway {gw} {'reachable' if gw_ok else 'unreachable'}{probe_str} · "
                f"Internet {'OK' if gw_ok else 'LIMITED'}"
            )
            health_info.value = (
                f"AP {'UP' if ap_up else 'DOWN'} · "
                f"DHCP {'ok (' + str(leases) + ' leases)' if leases_ok else 'error'}"
            )
            health_chip.content.controls[1].value = "HEALTHY" if ok else "ISSUES"
            col = SUCCESS if ok else DANGER
            health_chip.content.controls[0].bgcolor = col
            health_chip.content.controls[1].color = col
            health_chip.bgcolor = ft.Colors.with_opacity(0.12, col)

            chk_autostart.value = hub.is_autostart_enabled()
        except Exception as e:
            wifi_health_text.value = f"Health error: {e}"

    def refresh_all() -> None:
        refresh_hotspot()
        refresh_bw()
        refresh_warp()
        refresh_sys()
        refresh_health()
        try:
            page.update()
        except Exception:
            pass

    # ── Theme Toggle ─────────────────────────────────────────────────
    def toggle_theme() -> None:
        page.theme_mode = (
            ft.ThemeMode.LIGHT if is_dark(page) else ft.ThemeMode.DARK
        )
        page.bgcolor = page_bg(page)

        c_bg = card_bg(page)
        t_col = text_color(page)
        m_col = muted_color(page)

        for card in themed_cards:
            card.bgcolor = c_bg
        for t in themed_texts:
            t.color = t_col
        for m in themed_muted:
            m.color = m_col

        theme_btn.icon = (
            ft.Icons.DARK_MODE if is_dark(page) else ft.Icons.LIGHT_MODE
        )
        theme_btn.icon_color = m_col
        refresh_all()

    # ── Dialogs ──────────────────────────────────────────────────────
    def show_configure_dialog() -> None:
        """SSID and WPA2 passphrase editor with live validation."""
        current_ssid = hub.run([
            "nmcli",
            "-s",
            "-g",
            "802-11-wireless.ssid",
            "connection",
            "show",
            hub.HOTSPOT_CON,
        ]).strip()
        if not current_ssid:
            current_ssid = hub.iface_info(hub.AP_IF).get("ssid", "")

        txt_ssid = ft.TextField(
            value=current_ssid,
            label="Hotspot name (SSID)",
            max_length=32,
            autofocus=True,
        )
        txt_psk = ft.TextField(
            value="",
            label="Passphrase (WPA2)",
            password=True,
            can_reveal_password=True,
            hint_text="8–63 characters (blank = keep current)",
            max_length=64,
        )
        feedback_lbl = ft.Text(
            "Passphrase will be kept as-is.", size=12, color=MUTED_GRAY
        )
        btn_save = ft.TextButton("Save", disabled=False)

        def validate_inputs(e: Any = None) -> None:
            ok, msg = hub.validate_hotspot_credentials(
                txt_ssid.value, txt_psk.value
            )
            btn_save.disabled = not ok
            if not ok:
                feedback_lbl.value = msg
                feedback_lbl.color = DANGER
            elif not txt_psk.value:
                feedback_lbl.value = "Passphrase will be kept as-is."
                feedback_lbl.color = MUTED_GRAY
            else:
                feedback_lbl.value = "Valid — passphrase will be updated."
                feedback_lbl.color = SUCCESS
            dlg.update()

        txt_ssid.on_change = validate_inputs
        txt_psk.on_change = validate_inputs

        def on_save(e: Any) -> None:
            ssid = txt_ssid.value.strip()
            psk = txt_psk.value
            page.pop_dialog()

            def do_save() -> None:
                ok, msg = hub.modify_hotspot_credentials(ssid, psk)
                if not ok:
                    raise RuntimeError(msg)
                if hub.detect_state() == "on":
                    hub.hotspot_restart_service()

            run_async_action(
                do_save, feedback=f"Hotspot name updated to “{ssid}”"
            )

        btn_save.on_click = on_save

        dlg = ft.AlertDialog(
            title=ft.Text(
                "Hotspot — Name & Passphrase", weight=ft.FontWeight.W_600
            ),
            content=ft.Column(
                [
                    ft.Text(
                        "Rename the hotspot or change its passphrase. Applied to NetworkManager; "
                        "the hotspot restarts automatically if active.",
                        size=13,
                        color=muted_color(page),
                    ),
                    txt_ssid,
                    txt_psk,
                    feedback_lbl,
                ],
                spacing=12,
                width=420,
                tight=True,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                btn_save,
            ],
        )
        page.show_dialog(dlg)

    def show_clients_dialog() -> None:
        """Connected clients dialog with naming, allowing, and kicking."""
        stations = hub.station_dump()
        leases = hub.read_leases()
        oui = hub._load_oui_vendors()
        allowed_macs = hub.load_mac_file(hub.ALLOWED_MACS_FILE)

        named_count = sum(
            1 for m, _ in stations if hub.client_device_name(m, leases, oui)
        )
        summary_text = f"Stations: {len(stations)} ({named_count} named) | DHCP leases: {hub.lease_count()}"

        client_rows: list[ft.Control] = []
        if not stations:
            empty_path = resolve_asset("clients-empty.svg", "assets")
            empty_controls: list[ft.Control] = []
            if empty_path:
                empty_controls.append(
                    ft.Image(src=empty_path, width=180, height=100)
                )
            empty_controls.append(
                ft.Text(
                    f"No clients connected (or {hub.AP_IF} is down).",
                    size=13,
                    color=muted_color(page),
                    italic=True,
                )
            )
            client_rows.append(
                ft.Container(
                    content=ft.Column(
                        empty_controls,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=8,
                    ),
                    alignment=ft.Alignment(0, 0),
                    padding=ft.Padding(0, 20, 0, 20),
                )
            )
        else:
            for mac, sig in stations:
                name = hub.client_device_name(mac, leases, oui)
                is_allowed = mac.lower() in allowed_macs

                def make_name_handler(m: str) -> Any:
                    return lambda e: prompt_name_client(m)

                def make_allow_handler(m: str) -> Any:
                    return lambda e: allow_client(m)

                def make_kick_handler(m: str) -> Any:
                    return lambda e: kick_client_action(m)

                btn_allow = ios_button(
                    "Allowed" if is_allowed else "+ Allow",
                    None if is_allowed else make_allow_handler(mac),
                    variant="quiet" if is_allowed else "primary",
                    compact=True,
                )
                if is_allowed:
                    btn_allow.disabled = True

                client_rows.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Column(
                                    [
                                        ft.Text(
                                            name or "Unnamed device",
                                            size=13,
                                            weight=ft.FontWeight.W_600,
                                            color=text_color(page),
                                        ),
                                        ft.Text(
                                            f"{mac}  ·  {sig or 'signal ?'}",
                                            size=11,
                                            font_family="monospace",
                                            color=muted_color(page),
                                        ),
                                    ],
                                    spacing=2,
                                    expand=True,
                                ),
                                ios_button(
                                    "Name…",
                                    make_name_handler(mac),
                                    variant="quiet",
                                    compact=True,
                                ),
                                btn_allow,
                                ios_button(
                                    "Kick",
                                    make_kick_handler(mac),
                                    variant="danger",
                                    compact=True,
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        padding=ft.Padding(0, 4, 0, 4),
                    )
                )

        def prompt_name_client(mac: str) -> None:
            names = hub.load_device_names()
            curr = names.get(mac.lower(), "")
            txt_name = ft.TextField(
                value=curr,
                label=f"Friendly name for {mac}",
                autofocus=True,
                dense=True,
            )

            def save_name(e: Any) -> None:
                val = txt_name.value.strip()
                if val:
                    names[mac.lower()] = val
                elif mac.lower() in names:
                    del names[mac.lower()]
                hub.save_device_names(names)
                page.pop_dialog()
                page.pop_dialog()
                show_clients_dialog()

            name_dlg = ft.AlertDialog(
                title=ft.Text("Name Device", weight=ft.FontWeight.W_600),
                content=txt_name,
                actions=[
                    ft.TextButton(
                        "Cancel", on_click=lambda e: page.pop_dialog()
                    ),
                    ft.TextButton("Save", on_click=save_name),
                ],
            )
            page.show_dialog(name_dlg)

        def allow_client(mac: str) -> None:
            cur_allowed = hub.load_mac_file(hub.ALLOWED_MACS_FILE)
            cur_allowed.add(mac.lower())
            hub.save_mac_file(hub.ALLOWED_MACS_FILE, cur_allowed)
            show_snack(f"Added {mac} to allowed list", SUCCESS)
            page.pop_dialog()
            show_clients_dialog()

        def kick_client_action(mac: str) -> None:
            hub.kick_client(mac)
            show_snack(f"Disconnected {mac}", PRIMARY)
            page.pop_dialog()
            show_clients_dialog()

        dlg = ft.AlertDialog(
            title=ft.Text(
                f"Connected Clients ({hub.AP_IF})", weight=ft.FontWeight.W_600
            ),
            content=ft.Column(
                [
                    ft.Text(
                        summary_text,
                        size=12,
                        font_family="monospace",
                        color=muted_color(page),
                    ),
                    ft.Divider(height=1),
                    ft.Column(
                        client_rows,
                        spacing=6,
                        scroll=ft.ScrollMode.AUTO,
                        height=260,
                    ),
                ],
                spacing=8,
                width=460,
                tight=True,
            ),
            actions=[
                ft.TextButton(
                    "Refresh",
                    on_click=lambda e: (page.pop_dialog(), show_clients_dialog()),
                ),
                ft.TextButton("Close", on_click=lambda e: page.pop_dialog()),
            ],
        )
        page.show_dialog(dlg)

    def show_qr_dialog() -> None:
        """Display QR code and passphrase with one-click copy."""
        psk = hub.run([
            "nmcli",
            "-s",
            "-g",
            "802-11-wireless-security.psk",
            "connection",
            "show",
            hub.HOTSPOT_CON,
        ]).strip()
        ssid = hub.run([
            "nmcli",
            "-s",
            "-g",
            "802-11-wireless.ssid",
            "connection",
            "show",
            hub.HOTSPOT_CON,
        ]).strip()
        if not ssid:
            ssid = hub.iface_info(hub.AP_IF).get("ssid", hub.HOTSPOT_CON)

        if not psk:
            show_snack("Could not read hotspot passphrase", DANGER)
            return

        qr_img_control: ft.Control
        try:
            png_data = hub.qr_code_png(ssid, psk)
            if png_data:
                b64 = base64.b64encode(png_data).decode("ascii")
                qr_img_control = ft.Image(src=b64, width=200, height=200)
            else:
                qr_img_control = ft.Text(
                    "Install 'qrencode' to view QR image.",
                    color=WARNING,
                    italic=True,
                )
        except Exception:
            qr_img_control = ft.Text(
                "QR code generation unavailable.", color=WARNING, italic=True
            )

        copy_btn = ft.FilledButton("Copy Passphrase")

        def on_copy(e: Any) -> None:
            copy_to_clipboard(psk)
            copy_btn.text = "Copied ✓"
            copy_btn.update()

        copy_btn.on_click = on_copy

        dlg = ft.AlertDialog(
            title=ft.Text("Hotspot QR Code", weight=ft.FontWeight.W_600),
            content=ft.Column(
                [
                    ft.Text(f"SSID: {ssid}", size=15, weight=ft.FontWeight.W_700),
                    ft.Container(
                        content=qr_img_control,
                        alignment=ft.Alignment(0, 0),
                        padding=ft.Padding(0, 8, 0, 8),
                    ),
                    ft.Text(
                        f"Password: {psk}",
                        size=13,
                        font_family="monospace",
                        selectable=True,
                    ),
                    copy_btn,
                ],
                spacing=10,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                width=340,
                tight=True,
            ),
            actions=[
                ft.TextButton("Close", on_click=lambda e: page.pop_dialog())
            ],
        )
        page.show_dialog(dlg)

    def show_mac_filter_dialog() -> None:
        """Full-featured MAC Filter management dialog."""
        c = hub.load_app_config()
        allowed = sorted(hub.load_mac_file(hub.ALLOWED_MACS_FILE))
        blocked = sorted(hub.load_mac_file(hub.BLOCKED_MACS_FILE))

        enable_switch = ft.Switch(
            value=bool(c.get("mac_filter_enabled", False)),
            label="Enable MAC filtering",
        )
        mode_radio = ft.RadioGroup(
            content=ft.Row([
                ft.Radio(value="allow", label="Allowlist (Only allowed MACs)"),
                ft.Radio(value="deny", label="Denylist (Block listed MACs)"),
            ]),
            value=c.get("mac_filter_mode", "allow"),
        )

        txt_new_allow = ft.TextField(
            label="MAC Address",
            hint_text="00:11:22:33:44:55",
            dense=True,
            text_size=12,
            expand=True,
        )
        txt_new_block = ft.TextField(
            label="MAC Address",
            hint_text="00:11:22:33:44:55",
            dense=True,
            text_size=12,
            expand=True,
        )

        allowed_col = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, height=130)
        blocked_col = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, height=130)

        def normalize_mac(t: str) -> str:
            cleaned = t.strip().lower().replace("-", ":")
            if re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", cleaned):
                return cleaned
            return ""

        def render_mac_lists() -> None:
            allowed_col.controls = [
                ft.Row(
                    [
                        ft.Text(m, font_family="monospace", size=12, expand=True),
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            icon_size=16,
                            on_click=lambda e, mac=m: del_allow(mac),
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )
                for m in allowed
            ] or [
                ft.Text(
                    "No allowed MACs",
                    size=12,
                    color=muted_color(page),
                    italic=True,
                )
            ]

            blocked_col.controls = [
                ft.Row(
                    [
                        ft.Text(m, font_family="monospace", size=12, expand=True),
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            icon_size=16,
                            on_click=lambda e, mac=m: del_block(mac),
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )
                for m in blocked
            ] or [
                ft.Text(
                    "No blocked MACs",
                    size=12,
                    color=muted_color(page),
                    italic=True,
                )
            ]
            dlg.update()

        def del_allow(m: str) -> None:
            if m in allowed:
                allowed.remove(m)
                render_mac_lists()

        def del_block(m: str) -> None:
            if m in blocked:
                blocked.remove(m)
                render_mac_lists()

        def add_allow(e: Any) -> None:
            m = normalize_mac(txt_new_allow.value)
            if not m:
                show_snack("Invalid MAC address", DANGER)
                return
            if m not in allowed:
                allowed.append(m)
                allowed.sort()
            txt_new_allow.value = ""
            render_mac_lists()

        def add_block(e: Any) -> None:
            m = normalize_mac(txt_new_block.value)
            if not m:
                show_snack("Invalid MAC address", DANGER)
                return
            if m not in blocked:
                blocked.append(m)
                blocked.sort()
            txt_new_block.value = ""
            render_mac_lists()

        def add_connected_to_allow(e: Any) -> None:
            stations = hub.station_dump()
            if not stations:
                show_snack("No connected clients to add", WARNING)
                return
            for s_mac, _ in stations:
                clean = normalize_mac(s_mac)
                if clean and clean not in allowed:
                    allowed.append(clean)
            allowed.sort()
            render_mac_lists()
            show_snack("Added connected clients to allowed list", SUCCESS)

        def save_and_apply(e: Any) -> None:
            c["mac_filter_enabled"] = enable_switch.value
            c["mac_filter_mode"] = mode_radio.value
            hub.save_app_config(c)
            hub.save_mac_file(hub.ALLOWED_MACS_FILE, set(allowed))
            hub.save_mac_file(hub.BLOCKED_MACS_FILE, set(blocked))
            page.pop_dialog()
            show_snack("MAC filter settings saved and applied", SUCCESS)
            refresh_sys()

        dlg = ft.AlertDialog(
            title=ft.Text("MAC Address Filter", weight=ft.FontWeight.W_600),
            content=ft.Column(
                [
                    enable_switch,
                    mode_radio,
                    ft.Divider(height=1),
                    ft.Row(
                        [
                            ft.Text(
                                "Allowed MACs",
                                size=13,
                                weight=ft.FontWeight.W_600,
                            ),
                            ft.TextButton(
                                "+ Add Connected",
                                on_click=add_connected_to_allow,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    allowed_col,
                    ft.Row([
                        txt_new_allow,
                        ios_button(
                            "Add", add_allow, variant="primary", compact=True
                        ),
                    ]),
                    ft.Divider(height=1),
                    ft.Text(
                        "Blocked MACs", size=13, weight=ft.FontWeight.W_600
                    ),
                    blocked_col,
                    ft.Row([
                        txt_new_block,
                        ios_button(
                            "Add", add_block, variant="primary", compact=True
                        ),
                    ]),
                ],
                spacing=8,
                width=460,
                scroll=ft.ScrollMode.AUTO,
                height=480,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                ft.FilledButton("Save & Apply", on_click=save_and_apply),
            ],
        )
        page.show_dialog(dlg)
        render_mac_lists()

    # ── Window close handler ─────────────────────────────────────────
    if not page.web:
        def on_window_event(e: ft.ControlEvent) -> None:
            if e.data == "close":
                page.window.visible = False
                page.update()

        page.window.on_event = on_window_event

    # ── Main Layout ──────────────────────────────────────────────────
    content = ft.Column(
        [
            header,
            ft.Container(height=4),
            hotspot_card,
            ft.Container(height=8),
            wifi_card,
            ft.Container(height=8),
            bw_card,
            ft.Container(height=8),
            warp_card,
            ft.Container(height=8),
            sys_card,
            ft.Container(height=8),
            health_card,
            ft.Container(height=16),
        ],
        scroll=ft.ScrollMode.AUTO,
        expand=True,
        spacing=0,
    )

    page.add(content)

    # Initial state refresh
    refresh_all()

    # ── Periodic background thread ───────────────────────────────────
    def periodic_loop() -> None:
        nonlocal schedule_last_action, battery_action_taken
        while not _quit_app.is_set():
            time.sleep(5)
            if _quit_app.is_set():
                break

            # 1. Schedule checks
            try:
                c = hub.load_app_config()
                if c.get("schedule_enabled", False):
                    now_dt = datetime.now()
                    sched_on = str(c.get("schedule_on", "08:00")).strip()
                    sched_off = str(c.get("schedule_off", "22:00")).strip()
                    try:
                        on_h, on_m = map(int, sched_on.split(":"))
                        match_on = (
                            now_dt.hour == on_h and now_dt.minute == on_m
                        )
                    except Exception:
                        match_on = False
                    try:
                        off_h, off_m = map(int, sched_off.split(":"))
                        match_off = (
                            now_dt.hour == off_h and now_dt.minute == off_m
                        )
                    except Exception:
                        match_off = False

                    if match_on:
                        if schedule_last_action != "on":
                            schedule_last_action = "on"
                            hub.hotspot_on_service()
                    elif match_off:
                        if schedule_last_action != "off":
                            schedule_last_action = "off"
                            hub.hotspot_off_service()
                    else:
                        schedule_last_action = None
            except Exception:
                pass

            # 2. Battery saver checks
            try:
                c = hub.load_app_config()
                if c.get("battery_saver_enabled", False):
                    pct = hub.read_battery_percent()
                    if pct is not None:
                        threshold = int(c.get("battery_threshold", 20))
                        if pct > threshold + 5:
                            battery_action_taken = False
                        if pct <= threshold and not battery_action_taken:
                            if hub.detect_state() == "on":
                                battery_action_taken = True
                                hub.hotspot_off_service()
                                show_snack(
                                    f"Battery saver: hotspot disabled ({pct}% <= {threshold}%)",
                                    WARNING,
                                )
            except Exception:
                pass

            # 3. Smart reconnect check
            try:
                c = hub.load_app_config()
                if c.get("reconnect_enabled", True):
                    reconnected, reason = hub.smart_reconnect(c)
                    if reconnected:
                        show_snack(f"Reconnected ({reason})", PRIMARY)
            except Exception:
                pass

            # 4. Refresh display
            try:
                refresh_all()
            except Exception:
                pass

    threading.Thread(target=periodic_loop, daemon=True).start()

    # ── Poll for tray events ─────────────────────────────────────────
    if not page.web:
        def poll_tray() -> None:
            while not _quit_app.is_set():
                if _show_window.is_set():
                    _show_window.clear()
                    page.window.visible = True
                    page.window.to_front()
                    try:
                        page.update()
                    except Exception:
                        pass
                if _show_clients.is_set():
                    _show_clients.clear()
                    page.window.visible = True
                    page.window.to_front()
                    try:
                        page.update()
                        show_clients_dialog()
                    except Exception:
                        pass
                if _show_qr.is_set():
                    _show_qr.clear()
                    page.window.visible = True
                    page.window.to_front()
                    try:
                        page.update()
                        show_qr_dialog()
                    except Exception:
                        pass
                if _show_configure.is_set():
                    _show_configure.clear()
                    page.window.visible = True
                    page.window.to_front()
                    try:
                        page.update()
                        show_configure_dialog()
                    except Exception:
                        pass
                if _show_mac_filter.is_set():
                    _show_mac_filter.clear()
                    page.window.visible = True
                    page.window.to_front()
                    try:
                        page.update()
                        show_mac_filter_dialog()
                    except Exception:
                        pass
                time.sleep(0.1)

            # Quit signal received from tray: cleanly destroy window
            try:
                page.window.destroy()
            except Exception:
                pass

        threading.Thread(target=poll_tray, daemon=True).start()


# ── Entry point ──────────────────────────────────────────────────────
def run_app() -> None:
    tray_t = threading.Thread(target=tray_thread, daemon=True)
    tray_t.start()
    ft.run(main, view=ft.AppView.FLET_APP)
    _quit_app.set()


if __name__ == "__main__":
    run_app()
