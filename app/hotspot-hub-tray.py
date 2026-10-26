#!/usr/bin/env python3
"""Hotspot Hub — Flet + pystray system tray application.

Architecture (per knowledge-base guidance):
  - pystray: system tray icon + context menu
  - Flet: graphical UI window (hidden by default, shown on tray click)
  - services: shared backend logic from app/hotspot-hub.py
  - Thread-safe communication via events/queue

Run: python3 app/hotspot-hub-tray.py
"""

from __future__ import annotations

import os
import sys
import time
import threading
import logging

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "app"))
sys.path.insert(0, os.path.join(REPO, "cli"))

import importlib.util
_spec = importlib.util.spec_from_file_location("hotspot_hub", os.path.join(REPO, "app", "hotspot-hub.py"))
hub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hub)

import flet as ft
from PIL import Image, ImageDraw

# ── Shared state & events ────────────────────────────────────────────
_show_window = threading.Event()
_quit_app = threading.Event()
_window_visible = threading.Event()

# ── Design tokens ────────────────────────────────────────────────────
PRIMARY = "#007AFF"
SUCCESS = "#34C759"
WARNING = "#FF9500"
DANGER = "#FF3B30"
BG_DARK = "#1C1C1E"
BG_LIGHT = "#F2F2F7"
CARD_DARK = "#2C2C2E"
CARD_LIGHT = "#FFFFFF"
TEXT_DARK = "#FFFFFF"
TEXT_LIGHT = "#000000"
MUTED_DARK = "#98989D"
MUTED_LIGHT = "#8E8E93"
RADIUS_CARD = 16
RADIUS_BTN = 12


def is_dark(page):
    return page.theme_mode == ft.ThemeMode.DARK


def card_bg(page):
    return CARD_DARK if is_dark(page) else CARD_LIGHT


def text_color(page):
    return TEXT_DARK if is_dark(page) else TEXT_LIGHT


def muted_color(page):
    return MUTED_DARK if is_dark(page) else MUTED_LIGHT


def page_bg(page):
    return BG_DARK if is_dark(page) else BG_LIGHT


def ios_card(content, page, padding=16):
    return ft.Container(
        content=content,
        bgcolor=card_bg(page),
        border_radius=RADIUS_CARD,
        padding=ft.Padding(left=padding, top=padding, right=padding, bottom=padding),
        shadow=ft.BoxShadow(
            spread_radius=0, blur_radius=16,
            color=ft.Colors.with_opacity(0.08, ft.Colors.BLACK),
            offset=ft.Offset(0, 2),
        ),
    )


def ios_button(label, on_click, page, variant="primary", icon=None, compact=False):
    colors = {
        "primary": (PRIMARY, ft.Colors.WHITE),
        "success": (SUCCESS, ft.Colors.WHITE),
        "danger": (DANGER, ft.Colors.WHITE),
        "quiet": (ft.Colors.TRANSPARENT, PRIMARY),
        "warning": (WARNING, ft.Colors.WHITE),
    }
    bg, fg = colors.get(variant, (ft.Colors.with_opacity(0.1, PRIMARY), PRIMARY))
    h = 36 if compact else 44
    pv = 8 if compact else 12
    ph = 14 if compact else 20
    items = []
    if icon:
        items.append(ft.Icon(icon, size=16 if compact else 18, color=fg))
    items.append(ft.Text(label, size=13 if compact else 15,
                         weight=ft.FontWeight.W_600, color=fg))
    return ft.FilledButton(
        content=ft.Row(items, spacing=6, alignment=ft.MainAxisAlignment.CENTER),
        style=ft.ButtonStyle(
            color=fg, bgcolor=bg,
            shape=ft.RoundedRectangleBorder(radius=RADIUS_BTN),
            padding=ft.Padding(left=ph, top=pv, right=ph, bottom=pv),
            elevation=0, overlay_color=ft.Colors.with_opacity(0.12, fg),
        ),
        on_click=on_click, height=h,
    )


def status_pill(text, color):
    return ft.Container(
        content=ft.Row([
            ft.Container(width=8, height=8, border_radius=4, bgcolor=color),
            ft.Text(text, size=12, weight=ft.FontWeight.W_600, color=color),
        ], spacing=6),
        bgcolor=ft.Colors.with_opacity(0.12, color),
        border_radius=999,
        padding=ft.Padding(left=12, top=6, right=12, bottom=6),
    )


# ── Tray icon (pystray) ──────────────────────────────────────────────
def create_tray_icon():
    """Create a simple tray icon image."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Draw a simple hotspot-like icon (circle with waves)
    draw.ellipse([16, 16, 48, 48], fill="#007AFF")
    draw.ellipse([24, 24, 40, 40], fill="#FFFFFF")
    draw.ellipse([30, 30, 34, 34], fill="#007AFF")
    return img


def tray_thread():
    """Run pystray icon in a background thread."""
    import pystray

    def on_open(icon, item):
        _show_window.set()

    def on_quit(icon, item):
        _quit_app.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open Hotspot Hub", on_open, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon("hotspot-hub", create_tray_icon(), "Hotspot Hub", menu)
    icon.run()


# ── Flet UI ──────────────────────────────────────────────────────────
def main(page: ft.Page):
    page.title = "Hotspot Hub"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = page_bg(page)
    page.padding = 16
    page.window.width = 500
    page.window.height = 720
    page.window.resizable = True
    page.window.min_width = 420
    page.window.min_height = 480
    # Hide window on close instead of quitting
    page.window.prevent_close = True

    state = {"hotspot_on": False, "clients": 0}

    # ── Header ──
    header_pill = status_pill("STANDBY", MUTED_DARK)

    header = ft.Row([
        ft.Column([
            ft.Text("Hotspot Hub", size=22, weight=ft.FontWeight.W_700,
                    color=text_color(page)),
            ft.Text("Concurrent STA + AP control", size=13,
                    color=muted_color(page)),
        ], spacing=2, expand=True),
        header_pill,
    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

    # ── Hotspot card ──
    hotspot_info = ft.Text("Loading…", size=13, color=muted_color(page))

    def refresh_hotspot():
        try:
            sta, ap, n = hub.hotspot_state()
            hotspot_info.value = f"{sta}\n{ap}  ·  {n} clients"
            cons = hub.active_cons()
            active = cons.get(hub.HOTSPOT_CON) == hub.AP_IF
            state["hotspot_on"] = active
            state["clients"] = n
            pill_text = "ACTIVE" if active else "STANDBY"
            pill_color = SUCCESS if active else MUTED_DARK
            header_pill.content.controls[1].value = pill_text
            header_pill.content.controls[0].bgcolor = pill_color
            header_pill.bgcolor = ft.Colors.with_opacity(0.12, pill_color)
            header_pill.content.controls[1].color = pill_color
        except Exception as e:
            hotspot_info.value = f"Error: {e}"
        page.update()

    def do_on(e):
        hub.hotspot_on()
        refresh_hotspot()
        page.snack_bar = ft.SnackBar(ft.Text("Hotspot turned ON"), bgcolor=SUCCESS)
        page.snack_bar.open = True
        page.update()

    def do_off(e):
        hub.hotspot_off()
        refresh_hotspot()
        page.snack_bar = ft.SnackBar(ft.Text("Hotspot turned OFF"), bgcolor=MUTED_DARK)
        page.snack_bar.open = True
        page.update()

    def do_restart(e):
        hub.hotspot_restart()
        refresh_hotspot()
        page.snack_bar = ft.SnackBar(ft.Text("Hotspot restarted"), bgcolor=PRIMARY)
        page.snack_bar.open = True
        page.update()

    hotspot_card = ios_card(ft.Column([
        ft.Row([
            ft.Text("Hotspot", size=16, weight=ft.FontWeight.W_600,
                    color=text_color(page)),
            ft.Container(expand=True),
            ft.Row([
                ios_button("On", do_on, page, "success", compact=True),
                ios_button("Off", do_off, page, "quiet", compact=True),
                ios_button("Restart", do_restart, page, "quiet", compact=True),
            ], spacing=6),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        hotspot_info,
    ], spacing=12), page)

    # ── Bandwidth card ──
    bw_info = ft.Text("Loading…", size=13, color=muted_color(page),
                      font_family="monospace")

    def refresh_bw():
        try:
            stats = hub.read_net_dev_bytes()
            parts = []
            for iface in (hub.STA_IF, hub.AP_IF):
                if iface in stats:
                    rx, tx = stats[iface]
                    parts.append(f"{iface}: RX {rx // 1024}KB TX {tx // 1024}KB")
                else:
                    parts.append(f"{iface}: down")
            bw_info.value = "  |  ".join(parts)
        except Exception as e:
            bw_info.value = f"Error: {e}"
        page.update()

    bw_card = ios_card(ft.Column([
        ft.Text("Bandwidth", size=16, weight=ft.FontWeight.W_600,
                color=text_color(page)),
        bw_info,
    ], spacing=12), page)

    # ── WARP card ──
    warp_info = ft.Text("Status: …", size=13, color=muted_color(page))

    def refresh_warp():
        try:
            warp_info.value = f"Status: {hub.warp_status()}"
        except Exception as e:
            warp_info.value = f"Error: {e}"
        page.update()

    def warp_connect(e):
        hub.warp("connect")
        refresh_warp()
        page.snack_bar = ft.SnackBar(ft.Text("WARP connected"), bgcolor=PRIMARY)
        page.snack_bar.open = True
        page.update()

    def warp_disconnect(e):
        hub.warp("disconnect")
        refresh_warp()
        page.snack_bar = ft.SnackBar(ft.Text("WARP disconnected"), bgcolor=MUTED_DARK)
        page.snack_bar.open = True
        page.update()

    warp_card = ios_card(ft.Column([
        ft.Row([
            ft.Text("WARP", size=16, weight=ft.FontWeight.W_600,
                    color=text_color(page)),
            ft.Container(expand=True),
            ft.Row([
                ios_button("Connect", warp_connect, page, "primary", compact=True),
                ios_button("Disconnect", warp_disconnect, page, "quiet", compact=True),
            ], spacing=6),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        warp_info,
    ], spacing=12), page)

    # ── System card ──
    sys_info = ft.Text("Loading…", size=13, color=muted_color(page),
                       font_family="monospace")
    conn_info = ft.Text("Connectivity: checking…", size=12, color=muted_color(page))

    def refresh_sys():
        try:
            _, out = hub.run_rc(["systemctl", "is-active", "hotspot-channelsync.service"],
                                timeout=10, max_retries=1)
            state_str = out.strip().splitlines()[0] if out else "unknown"
            sys_info.value = (f"channel-sync: {state_str}\n"
                              f"VPN bypass: {'on' if hub.vpn_bypass_on() else 'OFF'}")
            ok, reason = hub.check_connectivity()
            conn_info.value = "Connectivity: OK" if ok else f"Connectivity: Limited ({reason})"
            conn_info.color = SUCCESS if ok else WARNING
        except Exception as e:
            sys_info.value = f"Error: {e}"
        page.update()

    def sync_restart(e):
        hub.sync_restart()
        refresh_sys()
        page.snack_bar = ft.SnackBar(ft.Text("Channel-sync restarted"), bgcolor=PRIMARY)
        page.snack_bar.open = True
        page.update()

    def fix_routing(e):
        hub.fix_routing()
        refresh_sys()
        page.snack_bar = ft.SnackBar(ft.Text("Routing fixed"), bgcolor=PRIMARY)
        page.snack_bar.open = True
        page.update()

    sys_card = ios_card(ft.Column([
        ft.Text("System", size=16, weight=ft.FontWeight.W_600,
                color=text_color(page)),
        sys_info,
        conn_info,
        ft.Row([
            ios_button("Restart sync", sync_restart, page, "quiet", compact=True),
            ios_button("Fix routing", fix_routing, page, "quiet", compact=True),
        ], spacing=6),
    ], spacing=12), page)

    # ── Theme toggle ──
    def toggle_theme(e):
        page.theme_mode = (ft.ThemeMode.LIGHT if is_dark(page)
                           else ft.ThemeMode.DARK)
        page.bgcolor = page_bg(page)
        page.update()

    theme_btn = ft.IconButton(
        icon=ft.Icons.DARK_MODE,
        on_click=toggle_theme,
        icon_color=muted_color(page),
    )

    # ── Window close handler (hide instead of quit) ──
    def on_window_event(e):
        if e.data == "close":
            page.window.visible = False
            page.update()

    page.window.on_event = on_window_event

    # ── Layout ──
    content = ft.Column([
        ft.Row([ft.Column([
            ft.Text("Hotspot Hub", size=22, weight=ft.FontWeight.W_700,
                    color=text_color(page)),
            ft.Text("Concurrent STA + AP control", size=13,
                    color=muted_color(page)),
        ], spacing=2, expand=True), theme_btn],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Container(height=4),
        hotspot_card,
        ft.Container(height=8),
        bw_card,
        ft.Container(height=8),
        warp_card,
        ft.Container(height=8),
        sys_card,
    ], scroll=ft.ScrollMode.AUTO, expand=True, spacing=0)

    page.add(content)

    # Initial refresh
    refresh_hotspot()
    refresh_warp()
    refresh_sys()
    refresh_bw()

    # Periodic refresh (30s)
    def periodic():
        while not _quit_app.is_set():
            time.sleep(30)
            try:
                refresh_hotspot()
                refresh_warp()
                refresh_sys()
                refresh_bw()
            except Exception:
                pass

    threading.Thread(target=periodic, daemon=True).start()

    # ── Poll for tray "show window" events ──
    def poll_tray():
        while not _quit_app.is_set():
            if _show_window.is_set():
                _show_window.clear()
                page.window.visible = True
                page.window.to_front()
                page.update()
            time.sleep(0.1)

    threading.Thread(target=poll_tray, daemon=True).start()


# ── Entry point ──────────────────────────────────────────────────────
def run_app():
    # Start tray in background thread
    tray_t = threading.Thread(target=tray_thread, daemon=True)
    tray_t.start()

    # Run Flet in main thread
    ft.run(main, view=ft.AppView.FLET_APP)

    # When Flet exits, signal quit
    _quit_app.set()


if __name__ == "__main__":
    run_app()
