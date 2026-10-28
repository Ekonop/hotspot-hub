#!/usr/bin/env python3
"""Hotspot Hub CLI - Command-line interface for Hotspot Hub.

Provides subcommands to control and monitor the concurrent Wi-Fi hotspot:
status, on, off, restart, clients, kick <mac>, qr.
Reuses helper functions from app/hotspot-hub.py.
"""
from __future__ import annotations

import argparse

# ---------------------------------------------------------------------------
# Dynamic import of app/hotspot-hub.py
# ---------------------------------------------------------------------------
import importlib.machinery
import importlib.util
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

_repo_hub = Path(__file__).resolve().parent.parent / "app" / "hotspot-hub.py"
_share_hub = Path("/usr/share/hotspot-hub/app/hotspot-hub.py")
_bin_hub = Path("/usr/bin/hotspot-hub")

hub_path = None
for p in (_repo_hub, _share_hub, _bin_hub):
    if p.is_file():
        hub_path = p
        break

if not hub_path:
    print("Error: Could not locate hotspot-hub.py in repo or system.", file=sys.stderr)
    sys.exit(1)

loader = importlib.machinery.SourceFileLoader("hotspot_hub", str(hub_path))
spec = importlib.util.spec_from_loader("hotspot_hub", loader)
hub = importlib.util.module_from_spec(spec)
loader.exec_module(hub)


def cmd_status(args: argparse.Namespace) -> int:
    """Display comprehensive status of hotspot, uplink, clients, security, and routing."""
    sta_text, ap_text, client_count = hub.hotspot_state()
    ap = hub.iface_info(hub.AP_IF)
    cons = hub.active_cons()
    hotspot_active = cons.get(hub.HOTSPOT_CON) == hub.AP_IF

    cfg = hub.load_app_config()
    sec = hub.get_hotspot_security()
    band_pref = cfg.get("band", "Auto")
    mf_enabled = cfg.get("mac_filter_enabled", False)
    mf_mode = cfg.get("mac_filter_mode", "allow")
    allowed_count = len(hub.load_mac_file(hub.ALLOWED_MACS_FILE))
    blocked_count = len(hub.load_mac_file(hub.BLOCKED_MACS_FILE))
    mf_str = (f"ENABLED ({mf_mode} mode)" if mf_enabled else "OFF") + f" [allowed: {allowed_count}, blocked: {blocked_count}]"

    is_disabled = bool(getattr(hub, "DISABLED_FLAG", None) and hub.DISABLED_FLAG.exists())
    state_str = "ACTIVE" if hotspot_active else ("DISABLED (manually stopped)" if is_disabled else ("IDLE" if ap else "DOWN"))
    print(f"Hotspot:    {state_str} ({hub.HOTSPOT_CON} on {hub.AP_IF})")
    print(f"  Security: {sec} (WPA3-SAE supported: {'yes' if cfg.get('wpa3_supported') else 'no'})")
    # Derive the actual band from the live channel number — the configured
    # band_pref is only a *preference* and often disagrees with the channel
    # the AP actually landed on (e.g. pref Auto, channel 36 = 5GHz).
    ap_channel = ap.get("channel", "")
    try:
        ch_num = int(ap_channel)
    except (TypeError, ValueError):
        ch_num = 0
    actual_band = "2.4GHz" if 0 < ch_num <= 14 else ("5GHz" if ch_num >= 36 else "?")
    pref_note = "" if band_pref in ("", "Auto") else f" · pref {band_pref}"
    print(f"  AP info:  SSID='{ap.get('ssid', '')}' Channel={ap_channel} (Band: {actual_band}{pref_note})")
    print(f"Uplink:     {sta_text}")
    hc = hub.health_check()
    print(f"Health:     {hc.get('summary', 'unknown')}")
    print(f"Clients:    {client_count} connected (DHCP leases: {hub.lease_count()})")
    print(f"MAC Filter: {mf_str}")
    print(f"WARP:       {hub.warp_status()}")
    print(f"VPN Bypass: {'ENABLED (pref 50)' if hub.vpn_bypass_on() else 'OFF'}")
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    """Emit Prometheus/node_exporter textfile-collector metrics for the hotspot.

    Writes a set of gauge metrics to stdout (or to --output file) in the
    OpenMetrics/Prometheus text exposition format. Suitable for piping into
    node_exporter's textfile collector.
    """
    sta_text, ap_text, client_count = hub.hotspot_state()
    ap = hub.iface_info(hub.AP_IF)
    cons = hub.active_cons()
    hotspot_active = cons.get(hub.HOTSPOT_CON) == hub.AP_IF
    cfg = hub.load_app_config()
    hc = hub.health_check()
    is_disabled = bool(getattr(hub, "DISABLED_FLAG", None) and hub.DISABLED_FLAG.exists())

    allowed_count = len(hub.load_mac_file(hub.ALLOWED_MACS_FILE))
    blocked_count = len(hub.load_mac_file(hub.BLOCKED_MACS_FILE))

    # Parse lease count into an int when possible.
    lease_raw = hub.lease_count()
    try:
        lease_num = int(lease_raw)
    except (TypeError, ValueError):
        lease_num = 0

    # Bandwidth rates (bytes/sec) for each interface.
    def _rates(iface: str) -> tuple[float, float]:
        """Return (rx_bps, tx_bps) current rates for iface using cached counters."""
        return 0.0, 0.0  # placeholder; real rates come from BandwidthSection state

    warp = hub.warp_status()
    warp_up = 1 if warp and warp.lower() not in ("disconnected", "down", "inactive", "error", "") else 0

    lines: list[str] = []
    lines.append("# HELP hotspot_up Whether the hotspot connection is active (1) or not (0).")
    lines.append("# TYPE hotspot_up gauge")
    lines.append(f"hotspot_up {1 if hotspot_active else 0}")
    lines.append("# HELP hotspot_disabled Manually stopped flag present (1) or not (0).")
    lines.append("# TYPE hotspot_disabled gauge")
    lines.append(f"hotspot_disabled {1 if is_disabled else 0}")
    lines.append("# HELP hotspot_clients Number of connected Wi-Fi stations.")
    lines.append("# TYPE hotspot_clients gauge")
    lines.append(f"hotspot_clients {client_count}")
    lines.append("# HELP hotspot_dhcp_leases Number of active DHCP leases.")
    lines.append("# TYPE hotspot_dhcp_leases gauge")
    lines.append(f"hotspot_dhcp_leases {lease_num}")
    lines.append("# HELP hotspot_healthy All health checks passing (1) or not (0).")
    lines.append("# TYPE hotspot_healthy gauge")
    lines.append(f"hotspot_healthy {1 if hc.get('healthy') else 0}")
    lines.append("# HELP hotspot_gateway_responsive Default gateway is reachable (ICMP or TCP/DNS) (1) or not (0).")
    lines.append("# TYPE hotspot_gateway_responsive gauge")
    lines.append(f"hotspot_gateway_responsive {1 if hc.get('gateway_ok') else 0}")
    lines.append("# HELP hotspot_ap_up AP interface is up (1) or not (0).")
    lines.append("# TYPE hotspot_ap_up gauge")
    lines.append(f"hotspot_ap_up {1 if hc.get('ap_up') else 0}")
    lines.append("# HELP hotspot_mac_filter_enabled MAC filtering enabled (1) or not (0).")
    lines.append("# TYPE hotspot_mac_filter_enabled gauge")
    lines.append(f"hotspot_mac_filter_enabled {1 if cfg.get('mac_filter_enabled') else 0}")
    lines.append("# HELP hotspot_mac_allowed Number of MACs in the allow list.")
    lines.append("# TYPE hotspot_mac_allowed gauge")
    lines.append(f"hotspot_mac_allowed {allowed_count}")
    lines.append("# HELP hotspot_mac_blocked Number of MACs in the block list.")
    lines.append("# TYPE hotspot_mac_blocked gauge")
    lines.append(f"hotspot_mac_blocked {blocked_count}")
    lines.append("# HELP hotspot_warp_up Cloudflare WARP connected (1) or not (0).")
    lines.append("# TYPE hotspot_warp_up gauge")
    lines.append(f"hotspot_warp_up {warp_up}")
    lines.append("# HELP hotspot_ap_channel Current AP channel number.")
    lines.append("# TYPE hotspot_ap_channel gauge")
    try:
        lines.append(f"hotspot_ap_channel {int(ap.get('channel', 0))}")
    except (TypeError, ValueError):
        lines.append("hotspot_ap_channel 0")

    output = "\n".join(lines) + "\n"

    if getattr(args, "output", None):
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"Wrote metrics to {args.output}", file=sys.stderr)
        except OSError as e:
            print(f"Failed to write metrics: {e}", file=sys.stderr)
            return 1
    else:
        sys.stdout.write(output)
    return 0


def cmd_throttle(args: argparse.Namespace) -> int:
    """Apply or clear a bandwidth limit (Mbps) on an interface via tc."""
    iface = args.iface
    rate = args.rate
    if rate is None or rate <= 0:
        ok, msg = hub.clear_bandwidth_limit(iface)
    else:
        ok, msg = hub.set_bandwidth_limit(iface, rate)
    print(msg)
    return 0 if ok else 1


def cmd_profile(args: argparse.Namespace) -> int:
    """Manage hotspot profiles (list/add/switch/remove)."""
    action = args.action
    cfg = hub.load_app_config()
    profiles = cfg.get("profiles", ["Hotspot-Shared"])
    active = cfg.get("active_profile", hub.HOTSPOT_CON)

    if action == "list":
        print("Hotspot profiles:")
        for p in profiles:
            marker = " (active)" if p == active else ""
            print(f"  {p}{marker}")
        return 0

    if action == "add":
        name = args.name
        if not name:
            print("Profile name required.", file=sys.stderr)
            return 2
        if name in profiles:
            print(f"Profile '{name}' already exists.")
            return 0
        profiles.append(name)
        cfg["profiles"] = profiles
        hub.save_app_config(cfg)
        print(f"Added profile '{name}'.")
        return 0

    if action == "switch":
        name = args.name
        if name not in profiles:
            print(f"Profile '{name}' not found. Use 'profile add {name}' first.", file=sys.stderr)
            return 2
        cfg["active_profile"] = name
        hub.save_app_config(cfg)
        print(f"Switched active profile to '{name}'. Restart hotspot-hub to apply.")
        return 0

    if action == "remove":
        name = args.name
        if name == "Hotspot-Shared":
            print("Cannot remove the default profile.", file=sys.stderr)
            return 2
        if name not in profiles:
            print(f"Profile '{name}' not found.", file=sys.stderr)
            return 2
        profiles = [p for p in profiles if p != name]
        cfg["profiles"] = profiles
        if cfg.get("active_profile") == name:
            cfg["active_profile"] = "Hotspot-Shared"
        hub.save_app_config(cfg)
        print(f"Removed profile '{name}'.")
        return 0

    print(f"Unknown action: {action}", file=sys.stderr)
    return 2


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


def cmd_band(args: argparse.Namespace) -> int:
    """View or set hotspot Wi-Fi band (2.4GHz / 5GHz / Auto)."""
    if args.band:
        band_val = args.band.strip()
        if band_val.lower() in ("2.4", "2.4ghz", "bg"):
            target_band = "2.4GHz"
        elif band_val.lower() in ("5", "5ghz", "a"):
            target_band = "5GHz"
        elif band_val.lower() in ("auto", ""):
            target_band = "Auto"
        else:
            print(f"Error: Unknown band '{args.band}'. Choose from: 2.4GHz, 5GHz, Auto", file=sys.stderr)
            return 1

        ok, msg = hub.modify_hotspot_band(target_band)
        if ok:
            cfg = hub.load_app_config()
            cfg["band"] = target_band
            hub.save_app_config(cfg)
            print(f"Success: {msg}")
            return 0
        else:
            print(f"Error: {msg}", file=sys.stderr)
            return 1
    else:
        cfg = hub.load_app_config()
        print(f"Current band preference: {cfg.get('band', 'Auto')}")
        channels = hub.get_phy_channels()
        print(f"Available 2.4GHz channels: {channels.get('2.4GHz', [])}")
        print(f"Available 5GHz channels:   {channels.get('5GHz', [])}")
        return 0


def cmd_creds(args: argparse.Namespace) -> int:
    """Set hotspot SSID and/or passphrase; restart the hotspot if active."""
    ssid = args.ssid or ""
    psk = args.password or ""
    if not ssid and not psk:
        print("Error: provide --ssid and/or --password", file=sys.stderr)
        return 1
    ok, msg = hub.validate_hotspot_credentials(ssid, psk)
    if not ok:
        print(f"Error: {msg}", file=sys.stderr)
        return 1
    ok, msg = hub.modify_hotspot_credentials(ssid, psk)
    if not ok:
        print(f"Error: {msg}", file=sys.stderr)
        return 1
    if hub.active_cons().get(hub.HOTSPOT_CON) == hub.AP_IF:
        hub.run_rc(["nmcli", "con", "down", hub.HOTSPOT_CON], timeout=30)
        hub.run_rc(["nmcli", "con", "up", hub.HOTSPOT_CON], timeout=30)
        print(f"Success: {msg} Hotspot restarted to apply.")
    else:
        print(f"Success: {msg} Applies when the hotspot starts.")
    return 0


def cmd_mac_filter(args: argparse.Namespace) -> int:
    """Manage MAC filtering policy and address lists."""
    action = args.action
    cfg = hub.load_app_config()
    allowed = hub.load_mac_file(hub.ALLOWED_MACS_FILE)
    blocked = hub.load_mac_file(hub.BLOCKED_MACS_FILE)

    if action == "list":
        status_str = "ENABLED" if cfg.get("mac_filter_enabled", False) else "DISABLED"
        print(f"MAC Filter Status: {status_str} (Mode: {cfg.get('mac_filter_mode', 'allow')})")
        print(f"\nAllowed MACs ({hub.ALLOWED_MACS_FILE}):")
        if allowed:
            for m in sorted(allowed):
                print(f"  - {m}")
        else:
            print("  (empty)")
        print(f"\nBlocked MACs ({hub.BLOCKED_MACS_FILE}):")
        if blocked:
            for m in sorted(blocked):
                print(f"  - {m}")
        else:
            print("  (empty)")
        return 0

    elif action == "enable":
        cfg["mac_filter_enabled"] = True
        hub.save_app_config(cfg)
        print("MAC filtering enabled.")
        return 0

    elif action == "disable":
        cfg["mac_filter_enabled"] = False
        hub.save_app_config(cfg)
        print("MAC filtering disabled.")
        return 0

    elif action == "allow":
        if not args.mac:
            print("Error: 'allow' requires a MAC address argument.", file=sys.stderr)
            return 1
        mac = args.mac.strip().lower().replace("-", ":")
        if not re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", mac):
            print(f"Error: Invalid MAC address: {args.mac}", file=sys.stderr)
            return 1
        allowed.add(mac)
        hub.save_mac_file(hub.ALLOWED_MACS_FILE, allowed)
        print(f"Added {mac} to allowed list.")
        return 0

    elif action in ("deny", "block"):
        if not args.mac:
            print("Error: 'deny' requires a MAC address argument.", file=sys.stderr)
            return 1
        mac = args.mac.strip().lower().replace("-", ":")
        if not re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", mac):
            print(f"Error: Invalid MAC address: {args.mac}", file=sys.stderr)
            return 1
        blocked.add(mac)
        hub.save_mac_file(hub.BLOCKED_MACS_FILE, blocked)
        print(f"Added {mac} to blocked list.")
        return 0

    elif action == "remove":
        if not args.mac:
            print("Error: 'remove' requires a MAC address argument.", file=sys.stderr)
            return 1
        mac = args.mac.strip().lower().replace("-", ":")
        changed = False
        if mac in allowed:
            allowed.remove(mac)
            hub.save_mac_file(hub.ALLOWED_MACS_FILE, allowed)
            changed = True
        if mac in blocked:
            blocked.remove(mac)
            hub.save_mac_file(hub.BLOCKED_MACS_FILE, blocked)
            changed = True
        if changed:
            print(f"Removed {mac} from MAC filter lists.")
            return 0
        else:
            print(f"MAC {mac} not found in allowed or blocked lists.", file=sys.stderr)
            return 1

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

    # band
    p_band = subparsers.add_parser("band", help="View or set Wi-Fi band (2.4GHz / 5GHz / Auto)")
    p_band.add_argument("band", nargs="?", choices=["2.4GHz", "5GHz", "Auto", "2.4", "5", "auto"],
                        help="Optional band to set (2.4GHz, 5GHz, Auto)")
    p_band.set_defaults(func=cmd_band)

    # set-credentials
    p_creds = subparsers.add_parser(
        "set-credentials", help="Set hotspot SSID and/or passphrase")
    p_creds.add_argument("--ssid", help="New SSID (1-32 characters)")
    p_creds.add_argument("--password", help="New WPA2 passphrase (8-63 chars; omit to keep current)")
    p_creds.set_defaults(func=cmd_creds)

    # mac-filter
    p_mf = subparsers.add_parser("mac-filter", help="Manage MAC address filtering")
    p_mf.add_argument("action", choices=["list", "allow", "deny", "block", "remove", "enable", "disable"],
                      help="MAC filter action")
    p_mf.add_argument("mac", nargs="?", help="MAC address for allow/deny/remove actions")
    p_mf.set_defaults(func=cmd_mac_filter)

    # metrics
    p_metrics = subparsers.add_parser(
        "metrics",
        help="Emit Prometheus textfile-collector metrics (hotspot_up, clients, health, ...)")
    p_metrics.add_argument("-o", "--output", metavar="FILE",
                           help="Write metrics to FILE instead of stdout")
    p_metrics.set_defaults(func=cmd_metrics)

    # throttle
    p_throttle = subparsers.add_parser(
        "throttle",
        help="Apply or clear a bandwidth cap (Mbps) on an interface via tc")
    p_throttle.add_argument("iface", help="Interface (e.g. ap0, wlan0)")
    p_throttle.add_argument("rate", nargs="?", type=float, default=0.0,
                            help="Rate limit in Mbps (0 or omit to clear)")
    p_throttle.set_defaults(func=cmd_throttle)

    # profile
    p_profile = subparsers.add_parser(
        "profile", help="Manage hotspot profiles (list/add/switch/remove)")
    p_profile.add_argument("action", choices=["list", "add", "switch", "remove"],
                           help="Profile action")
    p_profile.add_argument("name", nargs="?", help="Profile name for add/switch/remove")
    p_profile.set_defaults(func=cmd_profile)

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
