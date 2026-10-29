#!/usr/bin/env python3
"""Hotspot Hub — Flet + pystray system tray application.

Senior Desktop UI/UX Refactor:
- 3-Tab Segmented Navigation (Overview, Access & Clients, System & Schedule)
- Eliminates vertical scrolling on default window size
- Standardized single subtle 1px border and flat obsidian palette
- De-noised status indicators and unified switch styling
- Monospace fixed tabular-nums bandwidth display with directional accents
- Single-switch Cloudflare WARP control
- Preserves all underlying backend services, IPC, CLI, and polling logic
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

BG_DARK = "#0D1117"
BG_LIGHT = "#F8FAFC"
CARD_DARK = "#151B23"
CARD_LIGHT = "#FFFFFF"
BORDER_DARK = "#272D38"
BORDER_LIGHT = "#E2E8F0"
TEXT_DARK = "#F8FAFC"
TEXT_LIGHT = "#0F172A"
MUTED_DARK = "#94A3B8"
MUTED_LIGHT = "#64748B"

RADIUS_CARD = 12
RADIUS_BTN = 8
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


def card_border(page: ft.Page, accent: bool = False) -> ft.Border:
    if is_dark(page):
        if accent:
            return ft.Border.all(1.2, ft.Colors.with_opacity(0.45, PRIMARY))
        return ft.Border.all(1, ft.Colors.with_opacity(0.08, ft.Colors.WHITE))
    if accent:
        return ft.Border.all(1.2, PRIMARY)
    return ft.Border.all(1, BORDER_LIGHT)


def card_shadow(page: ft.Page, accent: bool = False) -> ft.BoxShadow:
    return ft.BoxShadow(
        spread_radius=0,
        blur_radius=4,
        color=ft.Colors.with_opacity(0.04, ft.Colors.BLACK),
        offset=ft.Offset(0, 1),
    )


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
    colors = {
        "on": "#22C55E",
        "off": "#94A3B8",
        "error": "#EF4444",
    }
    color = colors.get(state, "#94A3B8")
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill="#1E293B")
    draw.ellipse([8, 8, 56, 56], fill="#0F172A")
    draw.ellipse([20, 20, 44, 44], fill=color)
    draw.arc([14, 14, 50, 50], start=200, end=340, fill=color, width=3)
    draw.arc([8, 8, 56, 56], start=210, end=330, fill=color, width=3)
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
        os._exit(0)

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
        pystray.MenuItem("Traffic Inspector (RustNet)…", lambda i, it: subprocess.Popen(["xdg-open", "http://localhost:9090"])),
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
    page.padding = 12

    if not page.web:
        def on_window_event(e: ft.ControlEvent) -> None:
            if e.data == "close":
                page.window.visible = False
                try:
                    page.window.update()
                except Exception:
                    pass

        page.window.width = 440
        page.window.height = 660
        page.window.resizable = True
        page.window.min_width = 420
        page.window.min_height = 580
        page.window.prevent_close = True
        page.window.on_event = on_window_event
        hide_on_start = os.environ.get("FLET_HIDE_WINDOW_ON_START", "true").lower() not in ("false", "0", "no")
        if "--show" in sys.argv:
            hide_on_start = False
        page.window.visible = not hide_on_start

        try:
            icon_p = resolve_asset("hotspot-hub.svg", "icons")
            if icon_p:
                page.window.icon = icon_p
        except Exception:
            pass

        try:
            page.window.update()
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

    def app_card(
        content: ft.Control, padding: int = 14, accent: bool = False
    ) -> ft.Container:
        c = ft.Container(
            content=content,
            bgcolor=card_bg(page),
            border_radius=RADIUS_CARD,
            padding=ft.Padding(
                left=padding, top=padding, right=padding, bottom=padding
            ),
            border=card_border(page, accent),
            shadow=card_shadow(page, accent),
            data={"accent": accent},
        )
        return register_card(c)

    # Backwards compatibility alias
    ios_card = app_card

    def unified_switch(
        value: bool, on_change: Any = None, disabled: bool = False
    ) -> ft.Switch:
        """Single standardized Switch component for all app toggles."""
        return ft.Switch(
            value=value,
            on_change=on_change,
            disabled=disabled,
            active_color=ft.Colors.WHITE,
            active_track_color=PRIMARY,
            inactive_thumb_color="#CBD5E1",
            inactive_track_color="#334155",
            thumb_color={
                ft.ControlState.SELECTED: ft.Colors.WHITE,
                ft.ControlState.DEFAULT: "#CBD5E1",
            },
            track_color={
                ft.ControlState.SELECTED: PRIMARY,
                ft.ControlState.DEFAULT: "#334155",
            },
            track_outline_color=ft.Colors.TRANSPARENT,
        )

    def action_btn(
        label: str,
        on_click: Any,
        variant: str = "quiet",
        icon: Any = None,
        compact: bool = True,
        disabled: bool = False,
    ) -> ft.Container:
        colors = {
            "primary": (PRIMARY, ft.Colors.WHITE, PRIMARY),
            "success": (SUCCESS, ft.Colors.WHITE, SUCCESS),
            "danger": (DANGER, ft.Colors.WHITE, DANGER),
            "quiet": (
                ft.Colors.with_opacity(0.10, PRIMARY),
                PRIMARY,
                ft.Colors.with_opacity(0.18, PRIMARY),
            ),
            "outlined": (
                ft.Colors.TRANSPARENT,
                PRIMARY,
                ft.Colors.with_opacity(0.30, PRIMARY),
            ),
        }
        bg, fg, border_c = colors.get(
            variant, (ft.Colors.with_opacity(0.10, PRIMARY), PRIMARY, PRIMARY)
        )
        h = 30 if compact else 36
        items: list[ft.Control] = []
        if icon:
            items.append(ft.Icon(icon, size=14 if compact else 16, color=fg))
        items.append(
            ft.Text(label, size=12 if compact else 13, weight=ft.FontWeight.W_600, color=fg)
        )

        return ft.Container(
            content=ft.Row(items, spacing=5, tight=True, alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=bg if not disabled else ft.Colors.with_opacity(0.05, MUTED_GRAY),
            border=ft.Border.all(1, border_c if not disabled else ft.Colors.TRANSPARENT),
            border_radius=RADIUS_BTN,
            padding=ft.Padding(left=10, top=4, right=10, bottom=4),
            height=h,
            on_click=on_click if not disabled else None,
            opacity=1.0 if not disabled else 0.5,
        )

    # Backwards compatibility alias
    ios_button = action_btn

    def status_dot(color: str = MUTED_GRAY, size: int = 8) -> ft.Container:
        return ft.Container(
            width=size,
            height=size,
            border_radius=size // 2,
            bgcolor=color,
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

    # ── 1. Compact Header ────────────────────────────────────────────
    logo_path = resolve_asset("beacon.svg", "assets") or resolve_asset("hotspot-hub.svg", "icons") or resolve_asset("flame.svg", "assets")
    if logo_path:
        logo_widget = ft.Image(src=logo_path, width=24, height=24)
    else:
        logo_widget = ft.Icon(ft.Icons.WIFI_TETHERING, size=22, color=SUCCESS)

    title_text = register_text(
        ft.Text("Hotspot Hub", size=16, weight=ft.FontWeight.W_700)
    )
    subtitle_text = register_muted(
        ft.Text("Concurrent STA + AP", size=11, color=muted_color(page))
    )

    btn_docs = ft.IconButton(
        icon=ft.Icons.HELP_OUTLINE_ROUNDED,
        icon_size=18,
        icon_color=MUTED_DARK,
        tooltip="Documentation",
        on_click=lambda e: subprocess.Popen(["xdg-open", DOC_PATH]),
    )

    theme_btn = ft.IconButton(
        icon=ft.Icons.LIGHT_MODE,
        icon_size=18,
        icon_color=MUTED_DARK,
        tooltip="Toggle Dark/Light Mode",
        on_click=lambda e: toggle_theme(),
    )

    header = ft.Row(
        [
            ft.Row(
                [
                    logo_widget,
                    ft.Column([title_text, subtitle_text], spacing=0),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Row(
                [btn_docs, theme_btn],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        height=36,
    )

    # ── 2. Segmented Navigation Bar ──────────────────────────────────
    current_tab = "overview"

    def make_nav_tab(name: str, label: str, icon: Any) -> ft.Container:
        is_sel = (name == current_tab)
        bg = PRIMARY if is_sel else ft.Colors.TRANSPARENT
        fg = ft.Colors.WHITE if is_sel else muted_color(page)
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(icon, size=14, color=fg),
                    ft.Text(label, size=12, weight=ft.FontWeight.W_700 if is_sel else ft.FontWeight.W_500, color=fg),
                ],
                spacing=5,
                alignment=ft.MainAxisAlignment.CENTER,
                tight=True,
            ),
            bgcolor=bg,
            border_radius=RADIUS_BTN,
            padding=ft.Padding(left=10, top=6, right=10, bottom=6),
            expand=True,
            on_click=lambda e, n=name: select_tab(n),
        )

    tab_overview = make_nav_tab("overview", "Overview", ft.Icons.DASHBOARD_ROUNDED)
    tab_clients = make_nav_tab("clients", "Access & Clients", ft.Icons.DEVICES_ROUNDED)
    tab_system = make_nav_tab("system", "System & Schedule", ft.Icons.TUNE_ROUNDED)

    nav_bar = ft.Container(
        content=ft.Row(
            [tab_overview, tab_clients, tab_system],
            spacing=4,
            expand=True,
        ),
        bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.WHITE) if is_dark(page) else "#E2E8F0",
        border_radius=RADIUS_BTN + 2,
        padding=ft.Padding(3, 3, 3, 3),
    )

    def select_tab(name: str) -> None:
        nonlocal current_tab
        current_tab = name
        overview_view.visible = (current_tab == "overview")
        clients_view.visible = (current_tab == "clients")
        system_view.visible = (current_tab == "system")
        update_nav_bar()
        if current_tab == "clients":
            render_clients_tab()
        page.update()

    def update_nav_bar() -> None:
        for name, tab_ctrl in [
            ("overview", tab_overview),
            ("clients", tab_clients),
            ("system", tab_system),
        ]:
            is_sel = (name == current_tab)
            tab_ctrl.bgcolor = PRIMARY if is_sel else ft.Colors.TRANSPARENT
            fg = ft.Colors.WHITE if is_sel else muted_color(page)
            tab_ctrl.content.controls[0].color = fg
            tab_ctrl.content.controls[1].color = fg
            tab_ctrl.content.controls[1].weight = ft.FontWeight.W_700 if is_sel else ft.FontWeight.W_500

    # ── TAB 1: OVERVIEW COMPONENTS ───────────────────────────────────

    # A. Access Point Card
    ap_status_dot = status_dot(MUTED_GRAY, size=8)
    ap_status_text = ft.Text("STANDBY", size=12, weight=ft.FontWeight.W_700, color=MUTED_GRAY)

    cfg = hub.load_app_config()

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

    master_switch = unified_switch(
        value=False,
        on_change=lambda e: on_master_toggle(e.control.value),
    )

    ap_ssid_text = register_text(
        ft.Text("Hotspot AP", size=17, weight=ft.FontWeight.W_800)
    )

    def on_copy_password(e: Any = None) -> None:
        psk = hub.run([
            "nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", "show", hub.HOTSPOT_CON
        ]).strip()
        if psk:
            copy_to_clipboard(psk)
            show_snack("Hotspot password copied to clipboard ✓", SUCCESS)
        else:
            show_snack("Could not retrieve password", DANGER)

    btn_copy_pwd = ft.IconButton(
        icon=ft.Icons.CONTENT_COPY_ROUNDED,
        icon_size=15,
        icon_color=MUTED_DARK,
        tooltip="Copy hotspot passphrase",
        on_click=on_copy_password,
    )

    # Metadata pills
    badge_ap_ch = ft.Container(
        content=ft.Text("ch--", size=11, font_family="monospace", color=text_color(page)),
        bgcolor=ft.Colors.with_opacity(0.12, MUTED_GRAY),
        border_radius=999,
        padding=ft.Padding(left=8, top=2, right=8, bottom=2),
    )
    badge_ap_sec = ft.Container(
        content=ft.Text("WPA2", size=11, font_family="monospace", color=text_color(page)),
        bgcolor=ft.Colors.with_opacity(0.12, MUTED_GRAY),
        border_radius=999,
        padding=ft.Padding(left=8, top=2, right=8, bottom=2),
    )
    badge_ap_clients = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.PEOPLE_ROUNDED, size=12, color=PRIMARY),
                ft.Text("0 Clients", size=11, weight=ft.FontWeight.W_600, color=PRIMARY),
            ],
            spacing=4,
            tight=True,
        ),
        bgcolor=ft.Colors.with_opacity(0.12, PRIMARY),
        border_radius=999,
        padding=ft.Padding(left=8, top=2, right=8, bottom=2),
        on_click=lambda e: select_tab("clients"),
        tooltip="View connected devices in Access & Clients",
    )

    # Compact Band Segmented Selector
    selected_band = cfg.get("band", "Auto")

    def on_band_select(band: str) -> None:
        nonlocal selected_band
        if band == selected_band:
            return
        selected_band = band
        run_async_action(
            lambda: hub.modify_hotspot_band(band),
            feedback=f"Band preference set to {band}",
        )
        update_band_pills()
        page.update()

    band_pills: dict[str, ft.Container] = {}

    def make_band_pill(label: str) -> ft.Container:
        c = ft.Container(
            content=ft.Text(label, size=11, weight=ft.FontWeight.W_600),
            border_radius=RADIUS_BTN,
            padding=ft.Padding(left=8, top=4, right=8, bottom=4),
            on_click=lambda e, b=label: on_band_select(b),
        )
        band_pills[label] = c
        return c

    def update_band_pills() -> None:
        for b, pill in band_pills.items():
            is_b = (b == selected_band)
            pill.bgcolor = PRIMARY if is_b else ft.Colors.TRANSPARENT
            pill.content.color = ft.Colors.WHITE if is_b else muted_color(page)
            pill.border = None if is_b else ft.Border.all(1, ft.Colors.with_opacity(0.15, MUTED_GRAY))

    band_selector = ft.Container(
        content=ft.Row(
            [make_band_pill("2.4GHz"), make_band_pill("5GHz"), make_band_pill("Auto")],
            spacing=3,
            tight=True,
        ),
        bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.WHITE) if is_dark(page) else "#E2E8F0",
        border_radius=RADIUS_BTN + 2,
        padding=ft.Padding(2, 2, 2, 2),
    )
    update_band_pills()

    btn_configure = action_btn(
        "Configure",
        lambda e: show_configure_dialog(),
        icon=ft.Icons.TUNE_ROUNDED,
        variant="quiet",
    )
    btn_restart = action_btn(
        "Restart",
        lambda e: run_async_action(hub.hotspot_restart_service, feedback="Hotspot restarted"),
        icon=ft.Icons.REFRESH_ROUNDED,
        variant="quiet",
    )
    btn_qr = action_btn(
        "QR Code",
        lambda e: show_qr_dialog(),
        icon=ft.Icons.QR_CODE_ROUNDED,
        variant="quiet",
    )

    hotspot_card = app_card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.ROUTER_ROUNDED, size=16, color=PRIMARY),
                                register_text(ft.Text("Access Point", size=14, weight=ft.FontWeight.W_700)),
                                ap_status_dot,
                                ap_status_text,
                            ],
                            spacing=6,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        master_switch,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Row(
                    [
                        ap_ssid_text,
                        btn_copy_pwd,
                        ft.Container(expand=True),
                        badge_ap_ch,
                        badge_ap_sec,
                        badge_ap_clients,
                    ],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    [
                        band_selector,
                        ft.Container(expand=True),
                        btn_configure,
                        btn_restart,
                        btn_qr,
                    ],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=10,
        )
    )

    # B. Wi-Fi Uplink Card
    wifi_status_dot = status_dot(MUTED_GRAY, size=8)
    wifi_status_text = ft.Text("OFFLINE", size=12, weight=ft.FontWeight.W_700, color=MUTED_GRAY)
    wifi_ssid_text = register_text(
        ft.Text("Scanning…", size=16, weight=ft.FontWeight.W_800)
    )

    badge_speed_text = ft.Text("… MBit/s", size=11, weight=ft.FontWeight.W_700, color=PRIMARY)
    badge_speed = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.SPEED, size=13, color=PRIMARY),
                badge_speed_text,
            ],
            spacing=4,
            tight=True,
        ),
        bgcolor=ft.Colors.with_opacity(0.12, PRIMARY),
        border_radius=999,
        padding=ft.Padding(left=8, top=3, right=8, bottom=3),
    )

    badge_sig_text = ft.Text("… dBm", size=11, weight=ft.FontWeight.W_700, color=SUCCESS)
    badge_signal = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.SIGNAL_WIFI_4_BAR, size=13, color=SUCCESS),
                badge_sig_text,
            ],
            spacing=4,
            tight=True,
        ),
        bgcolor=ft.Colors.with_opacity(0.12, SUCCESS),
        border_radius=999,
        padding=ft.Padding(left=8, top=3, right=8, bottom=3),
    )

    badge_ch_text = ft.Text("ch…", size=11, weight=ft.FontWeight.W_700, color=MUTED_GRAY)
    badge_channel = ft.Container(
        content=badge_ch_text,
        bgcolor=ft.Colors.with_opacity(0.12, MUTED_GRAY),
        border_radius=999,
        padding=ft.Padding(left=8, top=3, right=8, bottom=3),
    )

    wifi_health_text = register_text(
        ft.Text("Health: checking gateway…", size=11, font_family="monospace")
    )

    wifi_card = app_card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.WIFI_ROUNDED, size=16, color=PRIMARY),
                                register_text(ft.Text("Wi-Fi Uplink", size=14, weight=ft.FontWeight.W_700)),
                                wifi_status_dot,
                                wifi_status_text,
                            ],
                            spacing=6,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Row(
                    [
                        wifi_ssid_text,
                        ft.Container(expand=True),
                        badge_speed,
                        badge_signal,
                        badge_channel,
                    ],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                wifi_health_text,
            ],
            spacing=8,
        ),
        accent=True,
    )

    # C. Bandwidth & Cloudflare WARP Section (Compact 2-in-1 Card)
    bw_sta_rx = ft.Text("0 B/s", size=11, font_family="monospace", weight=ft.FontWeight.W_600, color="#38BDF8")
    bw_sta_tx = ft.Text("0 B/s", size=11, font_family="monospace", weight=ft.FontWeight.W_600, color="#34D399")
    bw_ap_rx = ft.Text("0 B/s", size=11, font_family="monospace", weight=ft.FontWeight.W_600, color="#38BDF8")
    bw_ap_tx = ft.Text("0 B/s", size=11, font_family="monospace", weight=ft.FontWeight.W_600, color="#34D399")

    bw_block = ft.Column(
        [
            ft.Row(
                [
                    ft.Icon(ft.Icons.SWAP_VERT_ROUNDED, size=15, color=PRIMARY),
                    register_text(ft.Text("Network Throughput", size=13, weight=ft.FontWeight.W_600)),
                ],
                spacing=5,
            ),
            ft.Row(
                [
                    ft.Text(f"{hub.STA_IF} (Uplink):", size=11, font_family="monospace", color=muted_color(page), width=105),
                    ft.Text("↓", size=11, color="#38BDF8"),
                    bw_sta_rx,
                    ft.Container(width=10),
                    ft.Text("↑", size=11, color="#34D399"),
                    bw_sta_tx,
                ],
                spacing=4,
            ),
            ft.Row(
                [
                    ft.Text(f"{hub.AP_IF}  (Hotspot):", size=11, font_family="monospace", color=muted_color(page), width=105),
                    ft.Text("↓", size=11, color="#38BDF8"),
                    bw_ap_rx,
                    ft.Container(width=10),
                    ft.Text("↑", size=11, color="#34D399"),
                    bw_ap_tx,
                ],
                spacing=4,
            ),
        ],
        spacing=4,
    )

    # Single Switch Cloudflare WARP Control
    warp_status_text = ft.Text("Disconnected", size=11, font_family="monospace", color=MUTED_GRAY)

    def on_warp_toggle(checked: bool) -> None:
        if checked:
            run_async_action(lambda: hub.warp("connect"), feedback="WARP connected")
        else:
            run_async_action(lambda: hub.warp("disconnect"), feedback="WARP disconnected")

    warp_switch = unified_switch(
        value=False,
        on_change=lambda e: on_warp_toggle(e.control.value),
    )

    warp_block = ft.Row(
        [
            ft.Row(
                [
                    ft.Icon(ft.Icons.SHIELD_ROUNDED, size=16, color=PRIMARY),
                    ft.Column(
                        [
                            register_text(ft.Text("Cloudflare WARP", size=13, weight=ft.FontWeight.W_600)),
                            warp_status_text,
                        ],
                        spacing=1,
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            warp_switch,
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    traffic_warp_card = app_card(
        ft.Column(
            [
                bw_block,
                ft.Divider(height=1, color=ft.Colors.with_opacity(0.08, ft.Colors.WHITE) if is_dark(page) else BORDER_LIGHT),
                warp_block,
            ],
            spacing=8,
        )
    )

    overview_view = ft.Column(
        [
            hotspot_card,
            ft.Container(height=4),
            wifi_card,
            ft.Container(height=4),
            traffic_warp_card,
        ],
        spacing=4,
        visible=True,
    )

    # ── TAB 2: ACCESS & CLIENTS COMPONENTS ───────────────────────────
    clients_count_badge = ft.Text("0 Active", size=11, weight=ft.FontWeight.W_700, color=PRIMARY)
    clients_summary_text = ft.Text("DHCP leases: …", size=11, font_family="monospace", color=muted_color(page))
    clients_list_col = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, height=240)

    def render_clients_tab() -> None:
        stations = hub.station_dump()
        leases = hub.read_leases()
        oui = hub._load_oui_vendors()
        allowed_macs = hub.load_mac_file(hub.ALLOWED_MACS_FILE)

        clients_count_badge.value = f"{len(stations)} Active"
        clients_summary_text.value = f"DHCP leases: {hub.lease_count()} on {hub.AP_IF} (10.42.0.1/24)"

        items: list[ft.Control] = []
        if not stations:
            empty_path = resolve_asset("clients-empty.svg", "assets")
            empty_controls: list[ft.Control] = []
            if empty_path:
                empty_controls.append(ft.Image(src=empty_path, width=140, height=80))
            empty_controls.append(
                ft.Text(
                    f"No clients connected (or {hub.AP_IF} is inactive).",
                    size=12,
                    color=muted_color(page),
                    italic=True,
                )
            )
            items.append(
                ft.Container(
                    content=ft.Column(empty_controls, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=6),
                    alignment=ft.Alignment(0, 0),
                    padding=ft.Padding(0, 24, 0, 24),
                )
            )
        else:
            for mac, sig in stations:
                name = hub.client_device_name(mac, leases, oui) or "Unnamed device"
                is_allowed = mac.lower() in allowed_macs

                def make_name_h(m: str) -> Any:
                    return lambda e: prompt_name_client(m)

                def make_allow_h(m: str) -> Any:
                    return lambda e: allow_client(m)

                def make_kick_h(m: str) -> Any:
                    return lambda e: kick_client_action(m)

                btn_allow = action_btn(
                    "Allowed" if is_allowed else "+ Allow",
                    None if is_allowed else make_allow_h(mac),
                    variant="quiet" if is_allowed else "primary",
                    compact=True,
                    disabled=is_allowed,
                )

                items.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Row(
                                    [
                                        ft.Icon(ft.Icons.DEVICES_OTHER_ROUNDED, size=20, color=PRIMARY),
                                        ft.Column(
                                            [
                                                ft.Text(name, size=13, weight=ft.FontWeight.W_600, color=text_color(page)),
                                                ft.Text(f"{mac}  ·  {sig or 'signal ?'}", size=11, font_family="monospace", color=muted_color(page)),
                                            ],
                                            spacing=1,
                                        ),
                                    ],
                                    spacing=8,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    expand=True,
                                ),
                                ft.Row(
                                    [
                                        action_btn("Name…", make_name_h(mac), variant="quiet", compact=True),
                                        btn_allow,
                                        action_btn("Kick", make_kick_h(mac), variant="danger", compact=True),
                                    ],
                                    spacing=4,
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.WHITE) if is_dark(page) else "#F1F5F9",
                        border_radius=RADIUS_BTN,
                        padding=ft.Padding(8, 6, 8, 6),
                    )
                )

        clients_list_col.controls = items
        try:
            clients_list_col.update()
            clients_count_badge.update()
            clients_summary_text.update()
        except Exception:
            pass

    clients_card = app_card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.DEVICES_ROUNDED, size=16, color=PRIMARY),
                                register_text(ft.Text("Connected Devices", size=14, weight=ft.FontWeight.W_700)),
                                ft.Container(
                                    content=clients_count_badge,
                                    bgcolor=ft.Colors.with_opacity(0.12, PRIMARY),
                                    border_radius=999,
                                    padding=ft.Padding(6, 2, 6, 2),
                                ),
                            ],
                            spacing=6,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Row(
                            [
                                ft.IconButton(
                                    icon=ft.Icons.ANALYTICS_ROUNDED,
                                    icon_size=16,
                                    tooltip="Open RustNet Traffic Inspector",
                                    icon_color=PRIMARY,
                                    on_click=lambda e: subprocess.Popen(["xdg-open", "http://localhost:9090"]),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.REFRESH_ROUNDED,
                                    icon_size=16,
                                    tooltip="Refresh client list",
                                    on_click=lambda e: render_clients_tab(),
                                ),
                            ],
                            spacing=0,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                clients_summary_text,
                clients_list_col,
            ],
            spacing=8,
        )
    )

    # MAC Address Filter Status in Tab 2
    chk_mac_filter_tab = unified_switch(
        value=bool(cfg.get("mac_filter_enabled", False)),
        on_change=lambda e: on_toggle_mac_filter(e.control.value),
    )
    mac_filter_status_text = register_text(ft.Text("Filter status: …", size=12, font_family="monospace"))

    def on_toggle_mac_filter(val: bool) -> None:
        c = hub.load_app_config()
        c["mac_filter_enabled"] = val
        hub.save_app_config(c)
        refresh_sys()
        show_snack(f"MAC Filter {'enabled' if val else 'disabled'}", SUCCESS)

    btn_manage_mac = action_btn(
        "Manage MAC Rules…",
        lambda e: show_mac_filter_dialog(),
        icon=ft.Icons.SECURITY_ROUNDED,
        variant="primary",
        compact=True,
    )

    mac_card = app_card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.SHIELD_ROUNDED, size=16, color=PRIMARY),
                                register_text(ft.Text("MAC Address Filtering", size=14, weight=ft.FontWeight.W_700)),
                            ],
                            spacing=6,
                        ),
                        chk_mac_filter_tab,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                mac_filter_status_text,
                ft.Row(
                    [
                        register_muted(ft.Text("Enforces allow/deny list on connecting clients", size=11)),
                        ft.Container(expand=True),
                        btn_manage_mac,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            ],
            spacing=8,
        )
    )

    # AdBlocker & Proxy Shield in Tab 2
    chk_adblock_tab = unified_switch(
        value=bool(cfg.get("adblock_enabled", True)),
        on_change=lambda e: on_toggle_adblock(e.control.value),
    )
    adblock_status_text = register_text(ft.Text("Shield status: Loading…", size=12, font_family="monospace"))

    def on_toggle_adblock(val: bool) -> None:
        def _bg() -> None:
            ok, msg = hub.toggle_adblock(val)
            show_snack(f"AdBlocker Shield {'enabled' if val else 'disabled'}", SUCCESS if ok else DANGER)
            refresh_sys()
        threading.Thread(target=_bg, daemon=True).start()

    def on_update_adblock_rules() -> None:
        show_snack("Updating adblock blocklists from upstream…", PRIMARY)
        def _bg() -> None:
            ok, count, msg = hub.update_adblock_rules()
            if ok:
                show_snack(f"Adblock rules updated: {count:,} active", SUCCESS)
            else:
                show_snack(f"Failed to update rules: {msg}", DANGER)
            refresh_sys()
        threading.Thread(target=_bg, daemon=True).start()

    btn_update_adblock = action_btn(
        "Update Rules",
        lambda e: on_update_adblock_rules(),
        icon=ft.Icons.CLOUD_SYNC_ROUNDED,
        variant="quiet",
        compact=True,
    )

    btn_custom_adblock = action_btn(
        "Add Domain…",
        lambda e: show_add_domain_dialog(),
        icon=ft.Icons.ADD_ROUNDED,
        variant="primary",
        compact=True,
    )

    adblock_card = app_card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.SECURITY_ROUNDED, size=16, color=SUCCESS),
                                register_text(ft.Text("AdBlocker & Proxy Shield", size=14, weight=ft.FontWeight.W_700)),
                                ft.Container(
                                    content=ft.Text("HYBRID", size=9, weight=ft.FontWeight.W_800, color=SUCCESS),
                                    bgcolor=ft.Colors.with_opacity(0.12, SUCCESS),
                                    border_radius=4,
                                    padding=ft.Padding(5, 2, 5, 2),
                                ),
                            ],
                            spacing=6,
                        ),
                        chk_adblock_tab,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                adblock_status_text,
                ft.Row(
                    [
                        register_muted(ft.Text("Zero-config DNS sinkhole + Privoxy HTTP proxy on 10.42.0.1:8118", size=11)),
                        ft.Container(expand=True),
                        btn_update_adblock,
                        btn_custom_adblock,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    spacing=6,
                ),
            ],
            spacing=8,
        )
    )

    clients_view = ft.Column(
        [
            clients_card,
            ft.Container(height=4),
            mac_card,
            ft.Container(height=4),
            adblock_card,
        ],
        spacing=4,
        visible=False,
    )

    # ── TAB 3: SYSTEM & SCHEDULE COMPONENTS ──────────────────────────

    def make_setting_row(
        title: str,
        desc: str,
        control: ft.Control,
        icon: Any = None,
    ) -> ft.Container:
        return ft.Container(
            content=ft.Row(
                [
                    ft.Row(
                        [
                            ft.Icon(icon, size=16, color=PRIMARY) if icon else ft.Container(),
                            ft.Column(
                                [
                                    register_text(ft.Text(title, size=13, weight=ft.FontWeight.W_600)),
                                    register_muted(ft.Text(desc, size=11, color=muted_color(page))),
                                ],
                                spacing=1,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        expand=True,
                    ),
                    control,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            padding=ft.Padding(0, 4, 0, 4),
        )

    # Settings Rows
    chk_reconnect = unified_switch(
        value=bool(cfg.get("reconnect_enabled", True)),
        on_change=lambda e: on_toggle_reconnect(e.control.value),
    )

    def on_toggle_reconnect(val: bool) -> None:
        c = hub.load_app_config()
        c["reconnect_enabled"] = val
        hub.save_app_config(c)
        refresh_sys()
        show_snack(f"Smart reconnect {'enabled' if val else 'disabled'}", SUCCESS)

    chk_autostart = unified_switch(
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
            show_snack(f"Autostart {'enabled' if val else 'disabled'}", SUCCESS)

    chk_ipv6 = unified_switch(
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
            hub.priv(["sysctl", "-w", f"net.ipv6.conf.{hub.AP_IF}.disable_ipv6={flag}"])
        show_snack(f"IPv6 {'enabled' if val else 'disabled'}", SUCCESS)

    vpn_bypass_badge = ft.Container(
        content=ft.Text("Active (pref 50)", size=11, weight=ft.FontWeight.W_700, color=SUCCESS),
        bgcolor=ft.Colors.with_opacity(0.12, SUCCESS),
        border_radius=999,
        padding=ft.Padding(8, 3, 8, 3),
    )

    # Schedule with Progressive Disclosure
    txt_sched_on = ft.TextField(
        value=str(cfg.get("schedule_on", "08:00")),
        label="On Time (HH:MM)",
        width=120,
        dense=True,
        text_size=12,
        on_change=lambda e: on_sched_time_change(),
    )
    txt_sched_off = ft.TextField(
        value=str(cfg.get("schedule_off", "22:00")),
        label="Off Time (HH:MM)",
        width=120,
        dense=True,
        text_size=12,
        on_change=lambda e: on_sched_time_change(),
    )

    sched_inputs_row = ft.Row(
        [
            register_muted(ft.Text("Active Hours:", size=11)),
            txt_sched_on,
            txt_sched_off,
        ],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        visible=bool(cfg.get("schedule_enabled", False)),
    )

    def on_toggle_schedule(val: bool) -> None:
        c = hub.load_app_config()
        c["schedule_enabled"] = val
        hub.save_app_config(c)
        sched_inputs_row.visible = val
        page.update()
        show_snack(f"Schedule {'enabled' if val else 'disabled'}", SUCCESS)

    def on_sched_time_change() -> None:
        c = hub.load_app_config()
        c["schedule_on"] = txt_sched_on.value.strip()
        c["schedule_off"] = txt_sched_off.value.strip()
        hub.save_app_config(c)

    chk_schedule = unified_switch(
        value=bool(cfg.get("schedule_enabled", False)),
        on_change=lambda e: on_toggle_schedule(e.control.value),
    )

    # Battery Saver with Progressive Disclosure
    slider_battery = ft.Slider(
        min=5,
        max=50,
        divisions=9,
        value=float(cfg.get("battery_threshold", 20)),
        label="{value}%",
        on_change=lambda e: on_battery_threshold_change(int(e.control.value)),
    )
    lbl_battery_val = register_muted(ft.Text(f"{cfg.get('battery_threshold', 20)}%", size=12))

    battery_inputs_row = ft.Row(
        [
            register_muted(ft.Text("Threshold:", size=11)),
            ft.Container(content=slider_battery, width=160),
            lbl_battery_val,
        ],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        visible=bool(cfg.get("battery_saver_enabled", False)),
    )

    def on_toggle_battery(val: bool) -> None:
        c = hub.load_app_config()
        c["battery_saver_enabled"] = val
        hub.save_app_config(c)
        battery_inputs_row.visible = val
        page.update()
        show_snack(f"Battery saver {'enabled' if val else 'disabled'}", SUCCESS)

    def on_battery_threshold_change(val: int) -> None:
        lbl_battery_val.value = f"{val}%"
        page.update()
        c = hub.load_app_config()
        c["battery_threshold"] = val
        hub.save_app_config(c)

    chk_battery = unified_switch(
        value=bool(cfg.get("battery_saver_enabled", False)),
        on_change=lambda e: on_toggle_battery(e.control.value),
    )

    system_settings_card = app_card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.SETTINGS_ROUNDED, size=16, color=PRIMARY),
                        register_text(ft.Text("System & Network Settings", size=14, weight=ft.FontWeight.W_700)),
                    ],
                    spacing=6,
                ),
                make_setting_row("Smart Reconnect", "Auto-reconnect AP and sync on uplink dropouts", chk_reconnect, ft.Icons.AUTORENEW_ROUNDED),
                make_setting_row("Start on Boot", "Launch Hotspot Hub in background on login", chk_autostart, ft.Icons.POWER_SETTINGS_NEW_ROUNDED),
                make_setting_row("IPv6 Routing", f"Enable IPv6 forwarding on {hub.AP_IF}", chk_ipv6, ft.Icons.ALT_ROUTE_ROUNDED),
                make_setting_row("VPN Bypass", "Priority 50 policy routing for local traffic", vpn_bypass_badge, ft.Icons.SECURITY_ROUNDED),
                make_setting_row("Scheduled Hotspot", "Auto on/off by configured time of day", chk_schedule, ft.Icons.SCHEDULE_ROUNDED),
                sched_inputs_row,
                make_setting_row("Battery Saver", "Auto-disable hotspot when battery drops low", chk_battery, ft.Icons.BATTERY_CHARGING_FULL_ROUNDED),
                battery_inputs_row,
            ],
            spacing=6,
        )
    )

    # Maintenance & Troubleshooting Card
    sys_diag_text = register_text(ft.Text("Channel sync: active · Gateway: checking…", size=11, font_family="monospace"))

    btn_sync_restart = action_btn(
        "Restart Channel Sync",
        lambda e: run_async_action(hub.sync_restart_service, feedback="Channel sync restarted"),
        icon=ft.Icons.SYNC_ROUNDED,
        variant="outlined",
    )
    btn_fix_routing = action_btn(
        "Fix Routing Table",
        lambda e: run_async_action(hub.fix_routing_service, feedback="Routing table restored"),
        icon=ft.Icons.BUILD_ROUNDED,
        variant="outlined",
    )

    maintenance_card = app_card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.BUILD_CIRCLE_ROUNDED, size=16, color=PRIMARY),
                        register_text(ft.Text("Troubleshooting & Recovery", size=14, weight=ft.FontWeight.W_700)),
                    ],
                    spacing=6,
                ),
                sys_diag_text,
                ft.Row(
                    [btn_sync_restart, btn_fix_routing],
                    spacing=8,
                ),
            ],
            spacing=8,
        )
    )

    system_view = ft.Column(
        [
            system_settings_card,
            ft.Container(height=4),
            maintenance_card,
        ],
        spacing=4,
        visible=False,
    )

    # ── Refresh Functions ────────────────────────────────────────────
    def refresh_hotspot() -> None:
        try:
            cons = hub.active_cons()
            active = cons.get(hub.HOTSPOT_CON) == hub.AP_IF
            now = time.monotonic()
            in_fault = last_error_time > 0 and (now - last_error_time) < FAULT_WINDOW_S

            if in_fault:
                state_str = "FAULT"
                state_color = DANGER
                tray_state = "error"
            elif active:
                state_str = "ACTIVE"
                state_color = SUCCESS
                tray_state = "on"
            else:
                state_str = "INACTIVE"
                state_color = MUTED_GRAY
                tray_state = "off"

            ap_status_text.value = state_str
            ap_status_text.color = state_color
            ap_status_dot.bgcolor = state_color

            target_toggle = pending_toggle if pending_toggle is not None else active
            master_switch.value = target_toggle

            sec = hub.get_hotspot_security()
            ap = hub.iface_info(hub.AP_IF)
            ssid = ap.get("ssid") or hub.HOTSPOT_CON
            ch = ap.get("channel") or "?"
            _, _, n = hub.hotspot_state()

            ap_ssid_text.value = ssid
            badge_ap_ch.content.value = f"ch{ch}"
            badge_ap_sec.content.value = sec
            badge_ap_clients.content.controls[1].value = f"{n} Client{'s' if n != 1 else ''}"

            c = hub.load_app_config()
            nonlocal selected_band
            selected_band = c.get("band", "Auto")
            update_band_pills()

            update_tray_icon(tray_state)
        except Exception as e:
            ap_ssid_text.value = f"Status error: {e}"

    def refresh_bw() -> None:
        nonlocal bw_last_stats, bw_last_time
        try:
            now = time.monotonic()
            stats = hub.read_net_dev_bytes()
            dt = now - bw_last_time if bw_last_time > 0 else 5.0

            if dt > 15.0 or dt <= 0:
                bw_last_stats = stats
                bw_last_time = now
                return

            def _rates(iface: str) -> tuple[str, str]:
                if iface not in stats or iface not in bw_last_stats:
                    return hub.format_rate(0), hub.format_rate(0)
                rx, tx = stats[iface]
                prev_rx, prev_tx = bw_last_stats[iface]
                rx_rate = max(0.0, (rx - prev_rx) / dt) if rx >= prev_rx else 0.0
                tx_rate = max(0.0, (tx - prev_tx) / dt) if tx >= prev_tx else 0.0
                return hub.format_rate(rx_rate), hub.format_rate(tx_rate)

            sta_rx, sta_tx = _rates(hub.STA_IF)
            ap_rx, ap_tx = _rates(hub.AP_IF)

            bw_sta_rx.value = f"{sta_rx:>9}"
            bw_sta_tx.value = f"{sta_tx:>9}"
            bw_ap_rx.value = f"{ap_rx:>9}"
            bw_ap_tx.value = f"{ap_tx:>9}"

            bw_last_stats = stats
            bw_last_time = now
        except Exception:
            pass

    def refresh_warp() -> None:
        try:
            st = hub.warp_status()
            low = st.strip().lower()
            if "disconnected" in low:
                warp_status_text.value = "Disconnected"
                warp_status_text.color = MUTED_GRAY
                warp_switch.value = False
            elif "connecting" in low:
                warp_status_text.value = "● Connecting…"
                warp_status_text.color = WARNING
                warp_switch.value = True
            elif "connected" in low:
                warp_status_text.value = "● Connected (Protected)"
                warp_status_text.color = SUCCESS
                warp_switch.value = True
            elif "not installed" in low:
                warp_status_text.value = "WARP not installed"
                warp_status_text.color = MUTED_GRAY
                warp_switch.value = False
                warp_switch.disabled = True
            else:
                warp_status_text.value = st if st else "Disconnected"
                warp_status_text.color = MUTED_GRAY
                warp_switch.value = False
        except Exception as e:
            warp_status_text.value = f"WARP error: {e}"
            warp_status_text.color = DANGER
            warp_switch.value = False

    def refresh_sys() -> None:
        try:
            _, out = hub.run_rc(
                ["systemctl", "is-active", "hotspot-channelsync.service"],
                timeout=10,
                max_retries=1,
            )
            sync_active = (out or "").strip() == "active"
            sync_str = "active" if sync_active else "inactive"

            c = hub.load_app_config()
            allowed = hub.load_mac_file(hub.ALLOWED_MACS_FILE)
            blocked = hub.load_mac_file(hub.BLOCKED_MACS_FILE)
            mf_enabled = c.get("mac_filter_enabled", False)
            mf_mode = c.get("mac_filter_mode", "allow")
            chk_mac_filter_tab.value = mf_enabled

            mac_filter_status_text.value = (
                f"Mode: {'Allowlist' if mf_mode == 'allow' else 'Denylist'} · {len(allowed)} allowed · {len(blocked)} blocked"
            )

            ab = hub.adblock_status()
            ab_enabled = ab.get("enabled", False)
            chk_adblock_tab.value = ab_enabled
            rules_n = ab.get("rules_count", 0)
            proxy_str = "Privoxy ON (:8118)" if ab.get("proxy_running") else "Proxy OFF"
            adblock_status_text.value = (
                f"{rules_n:,} rules active · {proxy_str} · WPAD active" if ab_enabled else "Shield disabled (direct DNS/HTTP)"
            )

            bypass = hub.vpn_bypass_on()
            vpn_bypass_badge.content.value = "Active (pref 50)" if bypass else "Inactive"
            vpn_bypass_badge.content.color = SUCCESS if bypass else MUTED_GRAY
            vpn_bypass_badge.bgcolor = ft.Colors.with_opacity(0.12, SUCCESS if bypass else MUTED_GRAY)

            sys_diag_text.value = f"Channel Sync: {sync_str} · VPN bypass: {'on' if bypass else 'off'}"
            chk_reconnect.value = bool(c.get("reconnect_enabled", True))
        except Exception:
            pass

    def refresh_health() -> None:
        try:
            sta = hub.sta_link_info(hub.STA_IF)
            sta_connected = sta.get("connected") and bool(sta.get("ssid"))

            if sta_connected:
                wifi_ssid_text.value = sta["ssid"]
                badge_speed_text.value = sta.get("bitrate") or "Unknown"
                badge_sig_text.value = sta.get("signal") or "Sig ok"
                badge_ch_text.value = f"ch{hub.iface_info(hub.STA_IF).get('channel', '?')}"

                wifi_status_text.value = "CONNECTED"
                wifi_status_text.color = SUCCESS
                wifi_status_dot.bgcolor = SUCCESS
            else:
                wifi_ssid_text.value = "Disconnected (STA down)"
                badge_speed_text.value = "0 MBit/s"
                badge_sig_text.value = "No signal"
                badge_ch_text.value = "ch--"

                wifi_status_text.value = "OFFLINE"
                wifi_status_text.color = MUTED_GRAY
                wifi_status_dot.bgcolor = MUTED_GRAY

            hc = hub.health_check()
            gw = hc.get("gateway_ip", "?")
            gw_ok = hc.get("gateway_ok", False)
            gw_probe = hc.get("gateway_probe", "")
            probe_str = f" ({gw_probe})" if gw_probe == "icmp-blocked" else ""

            wifi_health_text.value = (
                f"Gateway {gw} {'reachable' if gw_ok else 'unreachable'}{probe_str} · "
                f"Internet {'OK' if gw_ok else 'LIMITED'}"
            )
            chk_autostart.value = hub.is_autostart_enabled()
        except Exception as e:
            wifi_health_text.value = f"Health error: {e}"

    def refresh_all() -> None:
        refresh_hotspot()
        refresh_bw()
        refresh_warp()
        refresh_sys()
        refresh_health()
        if current_tab == "clients":
            render_clients_tab()
        try:
            page.update()
        except Exception:
            pass

    # ── Theme Toggle ─────────────────────────────────────────────────
    def toggle_theme() -> None:
        page.theme_mode = ft.ThemeMode.LIGHT if is_dark(page) else ft.ThemeMode.DARK
        page.bgcolor = page_bg(page)

        c_bg = card_bg(page)
        t_col = text_color(page)
        m_col = muted_color(page)

        for card in themed_cards:
            card.bgcolor = c_bg
            accent = bool(card.data and card.data.get("accent"))
            card.border = card_border(page, accent)
            card.shadow = card_shadow(page, accent)
        for t in themed_texts:
            t.color = t_col
        for m in themed_muted:
            m.color = m_col

        theme_btn.icon = ft.Icons.DARK_MODE if is_dark(page) else ft.Icons.LIGHT_MODE
        theme_btn.icon_color = m_col
        btn_docs.icon_color = m_col
        update_nav_bar()
        update_band_pills()
        refresh_all()

    # ── Dialogs ──────────────────────────────────────────────────────
    def show_configure_dialog() -> None:
        """SSID and WPA2 passphrase editor with live validation."""
        current_ssid = hub.run([
            "nmcli", "-s", "-g", "802-11-wireless.ssid", "connection", "show", hub.HOTSPOT_CON
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
            ok, msg = hub.validate_hotspot_credentials(txt_ssid.value, txt_psk.value)
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
            psk = txt_psk.value.strip()
            page.pop_dialog()

            def do_save() -> None:
                ok, msg = hub.set_hotspot_credentials(ssid, psk if psk else None)
                if not ok:
                    raise RuntimeError(msg)
                if hub.detect_state() == "on":
                    hub.hotspot_restart_service()

            run_async_action(do_save, feedback=f"Hotspot name updated to “{ssid}”")

        btn_save.on_click = on_save

        dlg = ft.AlertDialog(
            title=ft.Text("Hotspot — Name & Passphrase", weight=ft.FontWeight.W_600),
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
        """Jump to Access & Clients tab."""
        select_tab("clients")

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
            render_clients_tab()

        name_dlg = ft.AlertDialog(
            title=ft.Text("Name Device", weight=ft.FontWeight.W_600),
            content=txt_name,
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                ft.TextButton("Save", on_click=save_name),
            ],
        )
        page.show_dialog(name_dlg)

    def allow_client(mac: str) -> None:
        cur_allowed = hub.load_mac_file(hub.ALLOWED_MACS_FILE)
        cur_allowed.add(mac.lower())
        hub.save_mac_file(hub.ALLOWED_MACS_FILE, cur_allowed)
        show_snack(f"Added {mac} to allowed list", SUCCESS)
        render_clients_tab()

    def kick_client_action(mac: str) -> None:
        hub.kick_client(mac)
        show_snack(f"Disconnected {mac}", PRIMARY)
        render_clients_tab()

    def show_qr_dialog() -> None:
        """Display QR code and passphrase with one-click copy."""
        psk = hub.run([
            "nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", "show", hub.HOTSPOT_CON
        ]).strip()
        ssid = hub.run([
            "nmcli", "-s", "-g", "802-11-wireless.ssid", "connection", "show", hub.HOTSPOT_CON
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
                qr_img_control = ft.Image(src=b64, width=180, height=180)
            else:
                qr_img_control = ft.Text("Install 'qrencode' to view QR image.", color=WARNING, italic=True)
        except Exception:
            qr_img_control = ft.Text("QR code generation unavailable.", color=WARNING, italic=True)

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
                    ft.Container(content=qr_img_control, alignment=ft.Alignment(0, 0), padding=ft.Padding(0, 4, 0, 4)),
                    ft.Text(f"Password: {psk}", size=13, font_family="monospace", selectable=True),
                    copy_btn,
                ],
                spacing=8,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                width=320,
                tight=True,
            ),
            actions=[ft.TextButton("Close", on_click=lambda e: page.pop_dialog())],
        )
        page.show_dialog(dlg)

    def show_add_domain_dialog() -> None:
        """Prompt dialog to add a custom domain to adblock sinkhole."""
        txt_domain = ft.TextField(
            label="Domain to block",
            hint_text="e.g. tracking.example.com",
            dense=True,
            text_size=12,
            autofocus=True,
        )

        def on_add(e: Any) -> None:
            dom = (txt_domain.value or "").strip()
            if dom:
                ok = hub.add_custom_adblock_domain(dom)
                if ok:
                    show_snack(f"Blocked {dom}", SUCCESS)
                    refresh_sys()
                else:
                    show_snack(f"Failed to block {dom}", DANGER)
            page.pop_dialog()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Add Custom Blocked Domain", size=15, weight=ft.FontWeight.W_700),
            content=ft.Container(
                content=ft.Column(
                    [
                        register_muted(ft.Text("Instantly sinkholes this domain for all connected devices via local DNS.", size=12)),
                        txt_domain,
                    ],
                    spacing=12,
                    tight=True,
                ),
                width=360,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                ft.FilledButton("Block Domain", on_click=on_add),
            ],
        )
        page.show_dialog(dlg)

    def show_mac_filter_dialog() -> None:
        """Full-featured MAC Filter management dialog."""
        c = hub.load_app_config()
        allowed = sorted(hub.load_mac_file(hub.ALLOWED_MACS_FILE))
        blocked = sorted(hub.load_mac_file(hub.BLOCKED_MACS_FILE))

        enable_switch = unified_switch(value=bool(c.get("mac_filter_enabled", False)))
        mode_radio = ft.RadioGroup(
            content=ft.Row([
                ft.Radio(value="allow", label="Allowlist (Only allowed)"),
                ft.Radio(value="deny", label="Denylist (Block listed)"),
            ]),
            value=c.get("mac_filter_mode", "allow"),
        )

        txt_new_allow = ft.TextField(label="MAC Address", hint_text="00:11:22:33:44:55", dense=True, text_size=12, expand=True)
        txt_new_block = ft.TextField(label="MAC Address", hint_text="00:11:22:33:44:55", dense=True, text_size=12, expand=True)

        allowed_col = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, height=110)
        blocked_col = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, height=110)

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
                        ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_size=16, on_click=lambda e, mac=m: del_allow(mac)),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )
                for m in allowed
            ] or [ft.Text("No allowed MACs", size=12, color=muted_color(page), italic=True)]

            blocked_col.controls = [
                ft.Row(
                    [
                        ft.Text(m, font_family="monospace", size=12, expand=True),
                        ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_size=16, on_click=lambda e, mac=m: del_block(mac)),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )
                for m in blocked
            ] or [ft.Text("No blocked MACs", size=12, color=muted_color(page), italic=True)]
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
                    ft.Row([enable_switch, ft.Text("Enable MAC filtering", size=13, weight=ft.FontWeight.W_600)], spacing=8),
                    mode_radio,
                    ft.Divider(height=1),
                    ft.Row(
                        [
                            ft.Text("Allowed MACs", size=13, weight=ft.FontWeight.W_600),
                            ft.TextButton("+ Add Connected", on_click=add_connected_to_allow),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    allowed_col,
                    ft.Row([txt_new_allow, action_btn("Add", add_allow, variant="primary", compact=True)]),
                    ft.Divider(height=1),
                    ft.Text("Blocked MACs", size=13, weight=ft.FontWeight.W_600),
                    blocked_col,
                    ft.Row([txt_new_block, action_btn("Block", add_block, variant="danger", compact=True)]),
                ],
                spacing=8,
                width=420,
                tight=True,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                ft.FilledButton("Save & Apply", on_click=save_and_apply),
            ],
        )
        page.show_dialog(dlg)
        render_mac_lists()

    def show_app_window() -> None:
        if page.web:
            return
        page.window.visible = True
        page.window.minimized = False
        page.window.focused = True
        try:
            page.window.update()
        except Exception:
            pass
        try:
            page.run_task(page.window.to_front)
        except Exception:
            pass
        try:
            page.update()
        except Exception:
            pass

    # ── Main Layout ──────────────────────────────────────────────────
    content = ft.Column(
        [
            header,
            ft.Container(height=2),
            nav_bar,
            ft.Container(height=4),
            overview_view,
            clients_view,
            system_view,
        ],
        spacing=0,
        expand=True,
    )

    page.add(content)

    # Make window visible once title and layout are ready (unless started in background)
    start_hidden = any(
        arg in sys.argv for arg in ("--autostart", "--tray-only", "--hidden")
    )
    if not start_hidden:
        show_app_window()

    try:
        page.update()
    except Exception:
        pass

    # Initial state refresh in background thread to avoid freezing boot
    threading.Thread(target=refresh_all, daemon=True).start()

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
                        match_on = (now_dt.hour == on_h and now_dt.minute == on_m)
                    except Exception:
                        match_on = False
                    try:
                        off_h, off_m = map(int, sched_off.split(":"))
                        match_off = (now_dt.hour == off_h and now_dt.minute == off_m)
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
                    thresh = int(c.get("battery_threshold", 20))
                    bat = hub.get_battery_level()
                    charging = hub.is_battery_charging()
                    if bat is not None and not charging:
                        if bat <= thresh and not battery_action_taken:
                            battery_action_taken = True
                            if hub.detect_state() == "on":
                                hub.hotspot_off_service()
                                show_snack(f"Hotspot disabled: battery low ({bat}%)", WARNING)
                        elif bat > (thresh + 5):
                            battery_action_taken = False
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
                    show_app_window()
                if _show_clients.is_set():
                    _show_clients.clear()
                    select_tab("clients")
                    show_app_window()
                if _show_qr.is_set():
                    _show_qr.clear()
                    show_app_window()
                    try:
                        show_qr_dialog()
                    except Exception:
                        pass
                if _show_configure.is_set():
                    _show_configure.clear()
                    show_app_window()
                    try:
                        show_configure_dialog()
                    except Exception:
                        pass
                if _show_mac_filter.is_set():
                    _show_mac_filter.clear()
                    select_tab("clients")
                    show_app_window()
                    try:
                        show_mac_filter_dialog()
                    except Exception:
                        pass
                time.sleep(0.05)

            # Quit signal received from tray: cleanly destroy window
            try:
                page.run_task(page.window.destroy)
            except Exception:
                pass
            os._exit(0)

        threading.Thread(target=poll_tray, daemon=True).start()


# ── Entry point ──────────────────────────────────────────────────────
def run_app() -> None:
    os.environ["FLET_APP_ID"] = "hotspot-hub"
    hide_start = os.environ.get("FLET_HIDE_WINDOW_ON_START", "true").lower() not in ("false", "0", "no")
    if "--show" in sys.argv:
        hide_start = False
    if hide_start:
        os.environ["FLET_HIDE_WINDOW_ON_START"] = "true"
        app_view = ft.AppView.FLET_APP_HIDDEN
    else:
        os.environ["FLET_HIDE_WINDOW_ON_START"] = "false"
        app_view = ft.AppView.FLET_APP

    tray_t = threading.Thread(target=tray_thread, daemon=True)
    tray_t.start()
    ft.run(main, view=app_view)
    _quit_app.set()
    os._exit(0)


if __name__ == "__main__":
    run_app()
