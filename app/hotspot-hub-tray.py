#!/usr/bin/env python3
"""Hotspot Hub — Flet + pystray system tray application.

Full-featured modern UI with system tray integration.
All backend logic shared with app/hotspot-hub.py.
"""

from __future__ import annotations

import os
import sys
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from core import config as _cfg
from core import services as hub

# Merge config exports into hub for backwards compatibility with hub.* calls
for _name in ("ALLOWED_MACS_FILE", "BLOCKED_MACS_FILE", "load_app_config", "save_app_config"):
    setattr(hub, _name, getattr(_cfg, _name))

import flet as ft
from PIL import Image, ImageDraw

# ── Shared state & events ────────────────────────────────────────────
_show_window = threading.Event()
_quit_app = threading.Event()

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


# ── Tray icon helpers ────────────────────────────────────────────────
def create_tray_icon(state="off"):
    """Create a colored tray icon based on hotspot state."""
    colors = {"on": "#34C759", "off": "#8E8E93", "error": "#FF3B30"}
    color = colors.get(state, colors["off"])
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Hub-node broadcast mark
    draw.ellipse([12, 12, 52, 52], fill=color)
    draw.ellipse([22, 22, 42, 42], fill="#FFFFFF")
    draw.ellipse([28, 28, 36, 36], fill=color)
    # Status dot (bottom-right)
    draw.ellipse([44, 44, 60, 60], fill=color, outline="#FFFFFF", width=2)
    return img


_tray_icon_ref = {"icon": None, "state": "off"}


def update_tray_icon(state):
    """Update the tray icon color based on hotspot state."""
    if _tray_icon_ref["icon"] is not None and _tray_icon_ref["state"] != state:
        _tray_icon_ref["icon"].icon = create_tray_icon(state)
        _tray_icon_ref["state"] = state


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
        pystray.MenuItem("Hotspot On", lambda i, it: hub.hotspot_on_service()),
        pystray.MenuItem("Hotspot Off", lambda i, it: hub.hotspot_off_service()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon("hotspot-hub", create_tray_icon("off"), "Hotspot Hub", menu)
    _tray_icon_ref["icon"] = icon
    icon.run()


# ── Flet UI ──────────────────────────────────────────────────────────
def main(page: ft.Page):
    page.title = "Hotspot Hub"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = page_bg(page)
    page.padding = 16
    page.window.width = 520
    page.window.height = 760
    page.window.resizable = True
    page.window.min_width = 420
    page.window.min_height = 480
    page.window.prevent_close = True
    # Set window icon
    try:
        page.window.icon = os.path.join(REPO, "icons", "hotspot-hub.svg")
    except Exception:
        pass

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

    band_dropdown = ft.Dropdown(
        label="Band",
        options=[
            ft.dropdown.Option("Auto"),
            ft.dropdown.Option("2.4GHz"),
            ft.dropdown.Option("5GHz"),
        ],
        value="Auto",
        width=120,
        text_size=13,
        on_select=lambda e: (hub.modify_hotspot_band(e.control.value), refresh_hotspot()),
    )

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
            # Update tray icon
            update_tray_icon("on" if active else "off")
            # Sync band dropdown
            cfg = hub.load_app_config()
            band_dropdown.value = cfg.get("band", "Auto")
        except Exception as e:
            hotspot_info.value = f"Error: {e}"
        page.update()

    def do_on(e):
        hub.hotspot_on_service()
        refresh_hotspot()
        show_snack(page, "Hotspot turned ON", SUCCESS)

    def do_off(e):
        hub.hotspot_off_service()
        refresh_hotspot()
        show_snack(page, "Hotspot turned OFF", MUTED_DARK)

    def do_restart(e):
        hub.hotspot_restart_service()
        refresh_hotspot()
        show_snack(page, "Hotspot restarted", PRIMARY)

    hotspot_card = ios_card(ft.Column([
        ft.Row([
            ft.Text("Hotspot", size=16, weight=ft.FontWeight.W_600,
                    color=text_color(page)),
            ft.Container(expand=True),
            band_dropdown,
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        hotspot_info,
        ft.Row([
            ios_button("On", do_on, page, "success", compact=True),
            ios_button("Off", do_off, page, "quiet", compact=True),
            ios_button("Restart", do_restart, page, "quiet", compact=True),
            ios_button("Clients", lambda e: show_clients_dialog(page), page, "quiet", compact=True),
            ios_button("QR", lambda e: show_qr_dialog(page), page, "quiet", compact=True),
        ], spacing=6, wrap=True),
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
                    parts.append(f"{iface}: ↓{rx // 1024}KB ↑{tx // 1024}KB")
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
        show_snack(page, "WARP connected", PRIMARY)

    def warp_disconnect(e):
        hub.warp("disconnect")
        refresh_warp()
        show_snack(page, "WARP disconnected", MUTED_DARK)

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
    health_info = ft.Text("Health: checking…", size=12, color=muted_color(page))

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
            # Health
            hc = hub.health_check()
            health_info.value = f"Health: {'OK' if hc.get('healthy') else 'ISSUES'} — {hc.get('summary', '')}"
            health_info.color = SUCCESS if hc.get('healthy') else DANGER
        except Exception as e:
            sys_info.value = f"Error: {e}"
        page.update()

    def sync_restart(e):
        hub.sync_restart_service()
        refresh_sys()
        show_snack(page, "Channel-sync restarted", PRIMARY)

    def fix_routing(e):
        hub.fix_routing_service()
        refresh_sys()
        show_snack(page, "Routing fixed", PRIMARY)

    # Settings toggles
    cfg = hub.load_app_config()

    def make_toggle(label, key, callback=None):
        sw = ft.Switch(
            value=bool(cfg.get(key, False)),
            on_change=lambda e: toggle_setting(key, e.control.value, callback),
        )
        return ft.Row([
            ft.Text(label, size=13, color=text_color(page), expand=True),
            sw,
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

    def toggle_setting(key, val, callback=None):
        cfg[key] = val
        hub.save_app_config(cfg)
        if callback:
            callback(val)
        page.update()

    def toggle_autostart(val):
        hub.set_autostart(val)

    def toggle_ipv6(val):
        if hub.detect_state() == "on":
            hub.priv(["sysctl", "-w", f"net.ipv6.conf.{hub.AP_IF}.disable_ipv6={'0' if val else '1'}"])

    autostart_toggle = make_toggle("Autostart on boot", "autostart", None)
    # Fix autostart toggle to use the right config key
    # (config uses is_autostart_enabled, not config key)
    ipv6_toggle = make_toggle("IPv6", "ipv6_enabled", toggle_ipv6)
    reconnect_toggle = make_toggle("Smart reconnect", "reconnect_enabled", None)

    sys_card = ios_card(ft.Column([
        ft.Text("System", size=16, weight=ft.FontWeight.W_600,
                color=text_color(page)),
        sys_info,
        conn_info,
        health_info,
        ft.Row([
            ios_button("Restart sync", sync_restart, page, "quiet", compact=True),
            ios_button("Fix routing", fix_routing, page, "quiet", compact=True),
            ios_button("MAC Filter", lambda e: show_mac_filter_dialog(page), page, "quiet", compact=True),
        ], spacing=6, wrap=True),
        ft.Divider(height=1, color=ft.Colors.with_opacity(0.1, muted_color(page))),
        autostart_toggle,
        ipv6_toggle,
        reconnect_toggle,
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

    # ── Dialogs ──
    def show_snack(page, msg, color):
        page.snack_bar = ft.SnackBar(ft.Text(msg), bgcolor=color)
        page.snack_bar.open = True
        page.update()

    def show_clients_dialog(page):
        """Show connected clients with kick capability."""
        try:
            stations = hub.station_dump()
        except Exception:
            stations = []
        lease_map = {}
        try:
            # Parse leases
            leases_raw = hub.run(["sudo", "-n", "cat", "/var/lib/NetworkManager/dnsmasq-ap0.leases"]) if False else ""
        except Exception:
            pass

        items = []
        if not stations:
            items.append(ft.Text("No clients connected.", size=13,
                                 color=muted_color(page), italic=True))
        else:
            for mac, sig in stations:
                name = hub.client_device_name(mac) if hasattr(hub, 'client_device_name') else mac
                items.append(ft.Row([
                    ft.Column([
                        ft.Text(name, size=13, weight=ft.FontWeight.W_500,
                                color=text_color(page)),
                        ft.Text(f"{mac}  ·  {sig}", size=11,
                                color=muted_color(page), font_family="monospace"),
                    ], spacing=2, expand=True),
                    ios_button("Kick", lambda e, m=mac: (hub.kick_client(m), close_dialog(dlg), show_clients_dialog(page)),
                               page, "danger", compact=True),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN))

        dlg = ft.AlertDialog(
            title=ft.Text("Connected Clients", weight=ft.FontWeight.W_600),
            content=ft.Column(items, spacing=8, scroll=ft.ScrollMode.AUTO,
                              width=400, height=300),
            actions=[ft.TextButton("Close", on_click=lambda e: close_dialog(dlg))],
        )
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

    def show_qr_dialog(page):
        """Show QR code for hotspot SSID/PSK."""
        psk = hub.run(["nmcli", "-s", "-g", "802-11-wireless-security.psk",
                       "connection", "show", hub.HOTSPOT_CON])
        ssid = hub.run(["nmcli", "-s", "-g", "802-11-wireless.ssid",
                        "connection", "show", hub.HOTSPOT_CON])
        if not ssid:
            ssid = hub.iface_info(hub.AP_IF).get("ssid", "Hotspot-Hub")
        if not psk:
            show_snack(page, "Could not read password", DANGER)
            return

        # Try to generate QR code image
        qr_widget = ft.Text(f"SSID: {ssid}\nPassword: {psk}", size=13,
                            color=text_color(page), selectable=True)
        try:
            png_data = hub.qr_code_png(ssid, psk)
            if png_data:
                import base64
                b64 = base64.b64encode(png_data).decode()
                qr_widget = ft.Image(src_base64=b64, width=200, height=200)
        except Exception:
            pass

        dlg = ft.AlertDialog(
            title=ft.Text("Hotspot QR Code", weight=ft.FontWeight.W_600),
            content=ft.Column([
                ft.Text(f"SSID: {ssid}", size=14, weight=ft.FontWeight.W_600,
                        color=text_color(page)),
                qr_widget,
                ft.Text(f"Password: {psk}", size=12, color=muted_color(page),
                        selectable=True, font_family="monospace"),
            ], spacing=12, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            actions=[ft.TextButton("Close", on_click=lambda e: close_dialog(dlg))],
        )
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

    def show_mac_filter_dialog(page):
        """Show MAC filter management dialog."""
        cfg = hub.load_app_config()
        allowed = hub.load_mac_file(hub.ALLOWED_MACS_FILE)
        blocked = hub.load_mac_file(hub.BLOCKED_MACS_FILE)

        allowed_list = ft.Column(
            [ft.Text(m, size=12, font_family="monospace", color=text_color(page))
             for m in sorted(allowed)] or
            [ft.Text("No allowed MACs", size=12, color=muted_color(page), italic=True)],
            spacing=4, scroll=ft.ScrollMode.AUTO, height=120,
        )
        blocked_list = ft.Column(
            [ft.Text(m, size=12, font_family="monospace", color=text_color(page))
             for m in sorted(blocked)] or
            [ft.Text("No blocked MACs", size=12, color=muted_color(page), italic=True)],
            spacing=4, scroll=ft.ScrollMode.AUTO, height=120,
        )

        mode_radio = ft.RadioGroup(
            content=ft.Row([
                ft.Radio(value="allow", label="Allow list"),
                ft.Radio(value="deny", label="Deny list"),
            ]),
            value=cfg.get("mac_filter_mode", "allow"),
        )
        enable_switch = ft.Switch(
            value=bool(cfg.get("mac_filter_enabled", False)),
            label="Enable MAC filtering",
        )

        def save_mac_filter(e):
            cfg["mac_filter_enabled"] = enable_switch.value
            cfg["mac_filter_mode"] = mode_radio.value
            hub.save_app_config(cfg)
            show_snack(page, "MAC filter settings saved", SUCCESS)
            close_dialog(dlg)

        dlg = ft.AlertDialog(
            title=ft.Text("MAC Filter", weight=ft.FontWeight.W_600),
            content=ft.Column([
                enable_switch,
                mode_radio,
                ft.Text("Allowed MACs:", size=12, weight=ft.FontWeight.W_600,
                        color=text_color(page)),
                allowed_list,
                ft.Text("Blocked MACs:", size=12, weight=ft.FontWeight.W_600,
                        color=text_color(page)),
                blocked_list,
            ], spacing=8, width=400),
            actions=[
                ft.TextButton("Save", on_click=save_mac_filter),
                ft.TextButton("Close", on_click=lambda e: close_dialog(dlg)),
            ],
        )
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

    def close_dialog(dlg):
        dlg.open = False
        if dlg in page.overlay:
            page.overlay.remove(dlg)
        page.update()

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

    # ── Window close handler ──
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
                reconnected, reason = hub.smart_reconnect(hub.load_app_config())
                if reconnected:
                    page.snack_bar = ft.SnackBar(
                        ft.Text(f"Reconnected ({reason})"), bgcolor=PRIMARY)
                    page.snack_bar.open = True
                    page.update()
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
    tray_t = threading.Thread(target=tray_thread, daemon=True)
    tray_t.start()
    ft.run(main, view=ft.AppView.FLET_APP)
    _quit_app.set()


if __name__ == "__main__":
    run_app()
