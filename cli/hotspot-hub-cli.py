#!/usr/bin/env python3
"""Hotspot Hub CLI - Command-line interface for Hotspot Hub.

Provides subcommands to control and monitor the concurrent Wi-Fi hotspot:
status, on, off, restart, clients, kick <mac>, qr.
Reuses helper functions from app/hotspot-hub.py.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

# ---------------------------------------------------------------------------
# Dynamic import of app/hotspot-hub.py via sys.path
# ---------------------------------------------------------------------------
_repo_app = Path(__file__).resolve().parent.parent / "app"
if _repo_app.is_dir() and str(_repo_app) not in sys.path:
    sys.path.insert(0, str(_repo_app))

_share_app = Path("/usr/share/hotspot-hub/app")
if _share_app.is_dir() and str(_share_app) not in sys.path:
    sys.path.insert(0, str(_share_app))

try:
    import importlib
    hub = importlib.import_module("hotspot-hub")
except ModuleNotFoundError:
    import importlib.machinery
    import importlib.util
    installed_bin = Path("/usr/bin/hotspot-hub")
    if installed_bin.exists():
        loader = importlib.machinery.SourceFileLoader("hotspot_hub", str(installed_bin))
        spec = importlib.util.spec_from_loader("hotspot_hub", loader)
        hub = importlib.util.module_from_spec(spec)
        loader.exec_module(hub)
    else:
        print("Error: Could not import hotspot-hub module from app/ or /usr/share/hotspot-hub/app.",
              file=sys.stderr)
        sys.exit(1)


def cmd_status(args: argparse.Namespace) -> int:
    """Display comprehensive status of hotspot, uplink, clients, and routing."""
    sta_text, ap_text, client_count = hub.hotspot_state()
    sta = hub.iface_info(hub.STA_IF)
    ap = hub.iface_info(hub.AP_IF)
    cons = hub.active_cons()
    hotspot_active = cons.get(hub.HOTSPOT_CON) == hub.AP_IF

    state_str = "ACTIVE" if hotspot_active else ("IDLE" if ap else "DOWN")
    print(f"Hotspot:    {state_str} ({hub.HOTSPOT_CON} on {hub.AP_IF})")
    print(f"  AP info:  SSID='{ap.get('ssid', '')}' Channel={ap.get('channel', '?')}")
    print(f"Uplink:     {sta_text}")
    print(f"Clients:    {client_count} connected (DHCP leases: {hub.lease_count()})")
    print(f"WARP:       {hub.warp_status()}")
    print(f"VPN Bypass: {'ENABLED (pref 50)' if hub.vpn_bypass_on() else 'OFF'}")
    return 0


def cmd_on(args: argparse.Namespace) -> int:
    """Turn on hotspot."""
    print("Activating hotspot...")
    ok, msg = hub.hotspot_start()
    if ok:
        print(f"Success: {msg}")
        return 0
    print(f"Error: {msg}", file=sys.stderr)
    return 1


def cmd_off(args: argparse.Namespace) -> int:
    """Turn off hotspot."""
    print("Deactivating hotspot...")
    ok, msg = hub.hotspot_stop()
    if ok:
        print(f"Success: {msg}")
        return 0
    print(f"Error: {msg}", file=sys.stderr)
    return 1


def cmd_restart(args: argparse.Namespace) -> int:
    """Restart hotspot."""
    print("Restarting hotspot...")
    ok, msg = hub.hotspot_cycle()
    if ok:
        print(f"Success: {msg}")
        return 0
    print(f"Error: {msg}", file=sys.stderr)
    return 1


def cmd_clients(args: argparse.Namespace) -> int:
    """List connected clients and signal strength."""
    try:
        stations = hub.station_dump()
    except Exception as e:
        print(f"Error reading stations: {e}", file=sys.stderr)
        return 1

    print(f"Connected clients on {hub.AP_IF}: {len(stations)} (leases: {hub.lease_count()})")
    if not stations:
        print(f"  No connected clients (or {hub.AP_IF} is down).")
        return 0

    for mac, sig in stations:
        sig_str = sig if sig else "signal ?"
        print(f"  - {mac} ({sig_str})")
    return 0


def cmd_kick(args: argparse.Namespace) -> int:
    """Disconnect a client by MAC address."""
    mac = args.mac.strip()
    if not re.match(r"^([0-9a-fA-F]{2}[:-]){5}([0-9a-fA-F]{2})$", mac):
        print(f"Error: Invalid MAC address format: {mac}", file=sys.stderr)
        return 1

    ok, msg = hub.kick_client(mac)
    if ok:
        print(f"Success: {msg}")
        return 0
    print(f"Error: {msg}", file=sys.stderr)
    return 1


def cmd_qr(args: argparse.Namespace) -> int:
    """Display WiFi QR code and credentials."""
    psk = hub.run(["nmcli", "-s", "-g", "802-11-wireless-security.psk",
                   "connection", "show", hub.HOTSPOT_CON])
    ssid = hub.run(["nmcli", "-s", "-g", "802-11-wireless.ssid",
                    "connection", "show", hub.HOTSPOT_CON])
    if not ssid:
        ssid = hub.iface_info(hub.AP_IF).get("ssid", "")
    if not ssid:
        ssid = "Hotspot-Hub"

    if not psk:
        print(f"Error: Could not retrieve password for '{hub.HOTSPOT_CON}'.", file=sys.stderr)
        return 1

    print(f"Hotspot SSID: {ssid}")
    print(f"Password:     {psk}")
    payload = f"WIFI:T:WPA;S:{ssid};P:{psk};;"

    if shutil.which("qrencode"):
        print("\nScan to connect:")
        try:
            subprocess.run(["qrencode", "-t", "UTF8", payload], check=True)
        except Exception as e:
            print(f"Warning: Failed to render QR code: {e}", file=sys.stderr)
    else:
        print("Note: 'qrencode' not installed. Install qrencode (pacman -S qrencode) for QR output.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build and return command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="hotspot-hub-cli",
        description="Hotspot Hub CLI - Command-line interface for Hotspot Hub.",
        epilog="For more information, see https://github.com/Ekonop/hotspot-hub",
    )
    subparsers = parser.add_subparsers(dest="command", required=True,
                                       help="Subcommands")

    # status
    p_status = subparsers.add_parser("status", help="Show hotspot, uplink, client, and VPN status")
    p_status.set_defaults(func=cmd_status)

    # on
    p_on = subparsers.add_parser("on", help="Activate hotspot connection")
    p_on.set_defaults(func=cmd_on)

    # off
    p_off = subparsers.add_parser("off", help="Deactivate hotspot connection")
    p_off.set_defaults(func=cmd_off)

    # restart
    p_restart = subparsers.add_parser("restart", help="Restart hotspot connection")
    p_restart.set_defaults(func=cmd_restart)

    # clients
    p_clients = subparsers.add_parser("clients", help="List connected WiFi stations and signal strength")
    p_clients.set_defaults(func=cmd_clients)

    # kick <mac>
    p_kick = subparsers.add_parser("kick", help="Disconnect a connected client by MAC address")
    p_kick.add_argument("mac", help="MAC address of the client to kick (e.g. 00:11:22:33:44:55)")
    p_kick.set_defaults(func=cmd_kick)

    # qr
    p_qr = subparsers.add_parser("qr", help="Display SSID, password, and terminal QR code")
    p_qr.set_defaults(func=cmd_qr)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and dispatch subcommand."""
    parser = build_parser()
    if argv is None and len(sys.argv) == 1:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    if hasattr(args, "func"):
        return args.func(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
