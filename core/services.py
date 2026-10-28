"""Hotspot Hub core services — pure backend logic (no UI dependencies).

All system-level operations: subprocess wrappers, network control, config,
connectivity, bandwidth, MAC filtering, QR codes, health checks, etc.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .config import (
    CONFIG_DIR,
    DISABLED_FLAG,
)

logger = logging.getLogger("hotspot-hub")

# Cache structures
_RUN_CACHE: dict = {}
_RUN_RC_CACHE: dict = {}

# Constants
BACKOFF_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0)
_MUTATING_KEYWORDS: set[str] = {
    "up", "down", "add", "del", "delete", "set", "restart", "start",
    "stop", "connect", "disconnect",
}
CACHE_TTL: float = float(os.environ.get("HOTSPOT_HUB_CACHE_TTL", "8.0"))


LEASE_FILE = "/var/lib/NetworkManager/dnsmasq-ap0.leases"
LOG_DIR = Path.home() / ".local" / "share" / "hotspot-hub"
LOG_FILE = LOG_DIR / "hotspot-hub.log"
DEVICES_FILE = CONFIG_DIR / "devicenames.json"
AP_IF: str = os.environ.get("HOTSPOT_HUB_AP_IF", "ap0")
STA_IF: str = os.environ.get("HOTSPOT_HUB_STA_IF", "wlan0")
HOTSPOT_CON: str = os.environ.get("HOTSPOT_HUB_CON", "Hotspot-Shared")

# Module-level smart-reconnect state.
_RECONNECT_STATE: dict[str, Any] = {
    "last_reconnect_time": 0.0,
    "failures": 0,
    "last_user_interaction": 0.0,
}

def invalidate_cache() -> None:
    """Invalidate cached outputs for subprocess commands."""
    _RUN_CACHE.clear()
    _RUN_RC_CACHE.clear()
    logger.debug("Subprocess command cache invalidated")

def _is_cacheable(cmd: Sequence[str]) -> bool:
    """Check if command is a read query eligible for caching."""
    if not cmd:
        return False
    if cmd[0] == "xdg-open":
        return False
    cmd_lower = [c.lower() for c in cmd]
    return not any(action in cmd_lower for action in _MUTATING_KEYWORDS)

def run(
    cmd: Sequence[str],
    timeout: int | float = 5,
    max_retries: int = 3,
    use_cache: bool = True,
) -> str:
    """Run cmd, return stdout. Never raises ('' on error).

    Caches output with 5s TTL for cacheable commands.
    Retries up to max_retries times with exponential backoff (1s, 2s, 4s).
    """
    cmd_tuple = tuple(cmd)
    cache_key = (cmd_tuple, timeout)
    now = time.monotonic()

    if use_cache and _is_cacheable(cmd):
        cached = _RUN_CACHE.get(cache_key)
        if cached is not None:
            val, ts = cached
            if now - ts < CACHE_TTL:
                return val

    last_err = ""
    for attempt in range(max(1, max_retries)):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            out = p.stdout.strip()
            if p.returncode == 0:
                if use_cache and _is_cacheable(cmd):
                    _RUN_CACHE[cache_key] = (out, time.monotonic())
                return out
            err_msg = (p.stderr or p.stdout).strip()
            last_err = f"code {p.returncode}: {err_msg}"
            if any(s in err_msg.lower() for s in ("no such device", "device not found", "not found")):
                break
        except Exception as e:
            last_err = str(e)

        if attempt < max_retries - 1:
            delay = BACKOFF_DELAYS[attempt] if attempt < len(BACKOFF_DELAYS) else BACKOFF_DELAYS[-1] * (2 ** (attempt - len(BACKOFF_DELAYS) + 1))
            logger.warning(
                "Retrying command %s in %.1fs (attempt %d/%d): %s",
                cmd, delay, attempt + 1, max_retries, last_err
            )
            time.sleep(delay)

    if last_err and not any(s in last_err.lower() for s in ("no such device", "device not found")):
        logger.error("Command %s failed after %d attempts: %s", cmd, max_retries, last_err)
    return ""

def run_rc(
    cmd: Sequence[str],
    timeout: int | float = 15,
    max_retries: int = 3,
    use_cache: bool = True,
) -> tuple[int, str]:
    """Run cmd, return (rc, output). Never raises.

    Caches output with 5s TTL for cacheable commands.
    Retries up to max_retries times with exponential backoff (1s, 2s, 4s).
    """
    cmd_tuple = tuple(cmd)
    cache_key = (cmd_tuple, timeout)
    now = time.monotonic()

    if use_cache and _is_cacheable(cmd):
        cached = _RUN_RC_CACHE.get(cache_key)
        if cached is not None:
            val, ts = cached
            if now - ts < CACHE_TTL:
                return val

    last_rc = 1
    last_out = ""
    for attempt in range(max(1, max_retries)):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            out = (p.stdout + p.stderr).strip()
            if p.returncode == 0:
                res = (0, out)
                if use_cache and _is_cacheable(cmd):
                    _RUN_RC_CACHE[cache_key] = (res, time.monotonic())
                return res
            last_rc = p.returncode
            last_out = out
            if any(s in out.lower() for s in ("no such device", "device not found", "not found", "password")):
                break
        except Exception as e:
            last_rc = 1
            last_out = str(e)

        if attempt < max_retries - 1:
            delay = BACKOFF_DELAYS[attempt] if attempt < len(BACKOFF_DELAYS) else BACKOFF_DELAYS[-1] * (2 ** (attempt - len(BACKOFF_DELAYS) + 1))
            logger.warning(
                "Retrying command %s in %.1fs (attempt %d/%d): code %d - %s",
                cmd, delay, attempt + 1, max_retries, last_rc, last_out
            )
            time.sleep(delay)

    if last_rc != 0 and not any(s in last_out.lower() for s in ("no such device", "device not found")):
        logger.error("Command %s failed after %d attempts (code %d): %s", cmd, max_retries, last_rc, last_out)
    return last_rc, last_out

def load_mac_file(path: Path) -> set[str]:
    """Load normalized MAC addresses from a file (one per line, ignoring comments)."""
    if not path.is_file():
        return set()
    macs: set[str] = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                cleaned = line.lower().replace("-", ":")
                if re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", cleaned):
                    macs.add(cleaned)
    except OSError as e:
        logger.warning("Failed to read MAC file %s: %s", path, e)
    return macs

def save_mac_file(path: Path, macs: set[str] | Sequence[str]) -> bool:
    """Save normalized MAC addresses to a file (one per line, sorted)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = sorted({m.lower().replace("-", ":") for m in macs if m})
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Hotspot Hub MAC Filter List\n")
            for m in lines:
                f.write(f"{m}\n")
        return True
    except OSError as e:
        logger.error("Failed to write MAC file %s: %s", path, e)
        return False





def read_battery_percent() -> int | None:
    """Read the current battery percentage from sysfs (/sys/class/power_supply/BAT0/capacity).

    Returns:
        int | None: Battery capacity percentage (0-100), or None on any failure.
    """
    bat_path = Path("/sys/class/power_supply/BAT0/capacity")
    try:
        if bat_path.is_file():
            return int(bat_path.read_text(encoding="utf-8").strip())
    except Exception as e:
        logger.debug("Failed reading battery capacity from %s: %s", bat_path, e)
    return None

def detect_ipv6_support() -> bool:
    """Check if IPv6 is supported by the kernel by inspecting /proc/net/if_inet6.

    Returns:
        bool: True if /proc/net/if_inet6 exists and is non-empty, False otherwise.
    """
    proc_file = Path("/proc/net/if_inet6")
    try:
        if proc_file.is_file():
            content = proc_file.read_text(encoding="utf-8").strip()
            return bool(content)
    except Exception as e:
        logger.debug("Failed reading %s: %s", proc_file, e)
    return False

def get_hotspot_security() -> str:
    """Query NetworkManager for Hotspot-Shared security key-mgmt (returns 'WPA3' or 'WPA2')."""
    key_mgmt = run(["nmcli", "-s", "-g", "802-11-wireless-security.key-mgmt",
                    "connection", "show", HOTSPOT_CON], timeout=5)
    if "sae" in key_mgmt.lower():
        return "WPA3"
    return "WPA2"

def get_phy_channels() -> dict[str, list[int]]:
    """Parse 'iw phy' output to discover available (non-disabled) channels for 2.4GHz and 5GHz."""
    out = run(["iw", "phy"], timeout=5)
    chans: dict[str, list[int]] = {"2.4GHz": [], "5GHz": []}
    for line in out.splitlines():
        m = re.search(r"\*\s+(\d+(?:\.\d+)?)\s+MHz\s+\[(\d+)\](.*)", line)
        if m:
            freq = float(m.group(1))
            ch = int(m.group(2))
            rest = m.group(3)
            if "(disabled)" not in rest:
                if 2400.0 <= freq <= 2500.0:
                    if ch not in chans["2.4GHz"]:
                        chans["2.4GHz"].append(ch)
                elif 5150.0 <= freq <= 5895.0:
                    if ch not in chans["5GHz"]:
                        chans["5GHz"].append(ch)
    return chans

def validate_band_availability(band: str) -> tuple[bool, str, list[int]]:
    """Check if the requested band has usable channels on the current hardware.

    Args:
        band: One of '2.4GHz', '5GHz', or 'Auto'.

    Returns:
        tuple[bool, str, list[int]]: (valid, message, available_channels).
    """
    if band.lower() in ("auto", ""):
        return True, "Band set to Auto", []

    channels = get_phy_channels()
    target_key = "2.4GHz" if "2.4" in band else "5GHz"
    avail = channels.get(target_key, [])
    if not avail:
        return False, f"No channels available for {target_key} on this Wi-Fi adapter.", []
    return True, f"Found {len(avail)} channels for {target_key}", avail

def modify_hotspot_band(band: str) -> tuple[bool, str]:
    """Modify the NetworkManager profile band setting ('2.4GHz', '5GHz', or 'Auto').

    Validates channel availability first. If valid, modifies 802-11-wireless.band
    and adjusts channel if necessary.
    """
    valid, msg, avail = validate_band_availability(band)
    if not valid:
        return False, msg

    invalidate_cache()
    if band == "2.4GHz":
        cur_ch = run(["nmcli", "-g", "802-11-wireless.channel", "connection", "show", HOTSPOT_CON], timeout=5)
        args = ["nmcli", "con", "modify", HOTSPOT_CON, "802-11-wireless.band", "bg"]
        try:
            ch_num = int(cur_ch) if cur_ch.isdigit() else 0
            if ch_num not in avail:
                new_ch = str(avail[0] if avail else 11)
                args.extend(["802-11-wireless.channel", new_ch])
        except Exception:
            args.extend(["802-11-wireless.channel", "11"])
    elif band == "5GHz":
        cur_ch = run(["nmcli", "-g", "802-11-wireless.channel", "connection", "show", HOTSPOT_CON], timeout=5)
        args = ["nmcli", "con", "modify", HOTSPOT_CON, "802-11-wireless.band", "a"]
        try:
            ch_num = int(cur_ch) if cur_ch.isdigit() else 0
            if ch_num not in avail:
                new_ch = str(avail[0] if avail else 36)
                args.extend(["802-11-wireless.channel", new_ch])
        except Exception:
            args.extend(["802-11-wireless.channel", "36"])
    else:  # Auto
        args = ["nmcli", "con", "modify", HOTSPOT_CON, "802-11-wireless.band", ""]

    rc, out = run_rc(args, timeout=15)
    invalidate_cache()
    if rc == 0:
        return True, f"Band updated to {band}"
    return False, f"Failed to modify band: {out}"

def modify_hotspot_security(sec: str) -> tuple[bool, str]:
    """Modify NetworkManager profile security: 'wpa2' or 'wpa3-sae'."""
    invalidate_cache()
    if sec.lower() in ("wpa3", "wpa3-sae", "sae"):
        args = [
            "nmcli", "con", "modify", HOTSPOT_CON,
            "wifi-sec.key-mgmt", "sae",
            "wifi-sec.pmf", "required",
            "wifi-sec.proto", "rsn",
        ]
    else:
        args = [
            "nmcli", "con", "modify", HOTSPOT_CON,
            "wifi-sec.key-mgmt", "wpa-psk",
            "wifi-sec.pmf", "optional",
            "wifi-sec.proto", "rsn",
            "wifi-sec.pairwise", "ccmp",
            "wifi-sec.group", "ccmp",
        ]
    rc, out = run_rc(args, timeout=15)
    invalidate_cache()
    if rc == 0:
        return True, f"Security updated to {sec}"
    return False, f"Failed to modify security: {out}"

def validate_hotspot_credentials(ssid: str, psk: str) -> tuple[bool, str]:
    """Validate SSID (1-32 printable chars) and WPA2 passphrase.

    Passphrase rules: 8-63 chars, or exactly 64 hex digits. An empty ``psk``
    means "keep the current passphrase" and is valid.
    """
    ssid = (ssid or "").strip()
    if not (1 <= len(ssid) <= 32):
        return False, "SSID must be 1–32 characters."
    if any(ord(c) < 32 or ord(c) == 127 for c in ssid):
        return False, "SSID contains invalid (control) characters."
    if psk:
        hex64 = len(psk) == 64 and all(c in "0123456789abcdefABCDEF" for c in psk)
        if not (8 <= len(psk) <= 63 or hex64):
            return False, "Passphrase must be 8–63 characters (or 64 hex digits)."
    return True, ""

def modify_hotspot_credentials(ssid: str, psk: str) -> tuple[bool, str]:
    """Set the SSID and (optionally) passphrase on the hotspot NM profile.

    Uses the same user-level ``nmcli con modify`` path as band/security
    changes (NetworkManager polkit lets the active session user modify the
    system hotspot profile). An empty ``psk`` keeps the current passphrase.
    """
    ssid = (ssid or "").strip()
    invalidate_cache()
    args = ["nmcli", "con", "modify", HOTSPOT_CON, "802-11-wireless.ssid", ssid]
    if psk:
        args += ["802-11-wireless-security.psk", psk]
    rc, out = run_rc(args, timeout=15)
    invalidate_cache()
    if rc == 0:
        return True, f"SSID and passphrase updated on {HOTSPOT_CON}."
    return False, f"Failed to update credentials: {out}"

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
    """Return (sta_text, ap_text, n_clients); safe with missing interfaces.

    If the hotspot has been manually disabled by the user, ap_text reflects
    a 'disabled by user' state.
    """
    cons = active_cons()
    sta, ap = iface_info(STA_IF), iface_info(AP_IF)
    on = cons.get(HOTSPOT_CON) == AP_IF
    name = sta_name()
    sta_t = f"STA {sta.get('ssid') or name or '?'} ch{sta.get('channel') or '?'}" if (sta or name) \
        else "STA down"
    if DISABLED_FLAG.exists():
        ap_t = "AP disabled by user"
    elif ap and on:
        ap_t = f"AP {ap.get('ssid') or AP_IF} ch{ap.get('channel') or '?'} ON"
    elif ap:
        ap_t = f"AP {AP_IF} idle"
    else:
        ap_t = "AP off (no ap0)"
    try:
        n = len(station_dump())
    except Exception:
        n = 0
    return sta_t, ap_t, n

def priv(cmd: list[str], timeout: int | float = 30) -> tuple[int, str]:
    """Run privileged cmd without prompt: sudo -n first (sudoers), pkexec fallback."""
    rc, out = run_rc(["sudo", "-n"] + cmd, timeout=timeout, max_retries=1)
    if rc == 0:
        return rc, out
    if "password" in out.lower() or "sudo" in out.lower()[:200]:
        if shutil.which("pkexec"):
            return run_rc(["pkexec"] + cmd, timeout=timeout, max_retries=1)
    return rc, out

def set_disabled_flag(disabled: bool = True) -> bool:
    """Create or remove the manually disabled flag file (/run/hotspot-hub-disabled).

    Args:
        disabled: True to create the flag file (touch), False to remove it.

    Returns:
        bool: True if the flag file was successfully created or removed.
    """
    flag = Path(DISABLED_FLAG)
    try:
        if disabled:
            flag.touch(exist_ok=True)
            logger.info("Hotspot disabled flag touched at %s", flag)
            return True
        else:
            if flag.exists():
                flag.unlink(missing_ok=True)
                logger.info("Hotspot disabled flag removed from %s", flag)
            return True
    except (PermissionError, OSError) as e:
        logger.debug("Direct flag file access failed (%s), using priv fallback", e)
        if disabled:
            rc, out = priv(["touch", str(flag)])
            if rc == 0:
                logger.info("Hotspot disabled flag touched via priv at %s", flag)
                return True
            logger.error("Failed to touch %s via priv: %s", flag, out)
            return False
        else:
            rc, out = priv(["rm", "-f", str(flag)])
            if rc == 0:
                logger.info("Hotspot disabled flag removed via priv from %s", flag)
                return True
            logger.error("Failed to remove %s via priv: %s", flag, out)
            return False

def is_hotspot_disabled() -> bool:
    """Check if the hotspot has been manually disabled by the user.

    Returns:
        bool: True if the disabled flag file exists, False otherwise.
    """
    return Path(DISABLED_FLAG).exists()

def ensure_ap0() -> tuple[bool, str]:
    """Ensure ap0 exists and is an AP-mode interface; create or rebuild it.

    The interface is created passwordless via the scoped sudoers rule
    (``iw phy ... add ap0 type __ap``). If an ap0 already exists but with the
    wrong nl80211 type (e.g. a stale ``managed`` interface), it is deleted and
    recreated \u2014 NetworkManager cannot activate the AP profile on a non-AP
    device and fails with "The device could not be readied for configuration".
    Returns (ok, msg).
    """
    try:
        info = iface_info(AP_IF)
        wrong_type = False
        if info:
            _, out = run_rc(["iw", "dev", AP_IF, "info"], timeout=3, max_retries=1)
            m = re.search(r"\btype\s+([a-z0-9_]+)", out)
            wrong_type = bool(m) and m.group(1) not in ("__ap", "ap")
        if info and not wrong_type:
            return True, "ap0 exists"
        if wrong_type:
            logger.warning("ap0 exists but is type %r; deleting and recreating as __ap",
                           m.group(1) if m else "unknown")
            priv(["iw", "dev", AP_IF, "del"])
            invalidate_cache()
        phy_dir = "/sys/class/ieee80211"
        phy = (os.listdir(phy_dir)[0]
               if os.path.isdir(phy_dir) and os.listdir(phy_dir) else "phy0")
        rc, out = priv(["iw", "phy", phy, "interface",
                        "add", AP_IF, "type", "__ap"])
        if rc != 0 and not iface_info(AP_IF):
            err = f"create ap0 failed: {out[-200:]}"
            logger.error("ensure_ap0 failed: %s", err)
            return False, err
        priv(["ip", "link", "set", AP_IF, "up"])
        invalidate_cache()
        logger.info("ap0 interface created and brought up on %s", phy)
        return True, "ap0 created"
    except Exception as e:
        err = f"ensure_ap0 error: {e}"
        logger.error("ensure_ap0 exception: %s", err, exc_info=True)
        return False, err

def lease_count() -> str:
    """DHCP lease count; sudo -n cat (sudoers). Returns str."""
    try:
        with open(LEASE_FILE) as f:
            return str(sum(1 for ln in f if ln.strip()))
    except (PermissionError, OSError):
        pass
    rc, out = run_rc(["sudo", "-n", "cat", LEASE_FILE], timeout=15, max_retries=1)
    if rc == 0 and out:
        return str(sum(1 for ln in out.splitlines() if ln.strip()))
    return "0 (no leases yet)"

def read_leases() -> dict[str, dict[str, str]]:
    """Parse the dnsmasq lease file -> {mac.lower(): {'ip': str, 'hostname': str}}.

    Lease line format: <expires> <mac> <ip> <hostname> [<clientid>]. A \"*\"
    hostname means the client sent no name.
    """
    text = ""
    try:
        with open(LEASE_FILE) as f:
            text = f.read()
    except (PermissionError, OSError):
        rc, out = run_rc(["sudo", "-n", "cat", LEASE_FILE], timeout=15, max_retries=1)
        if rc == 0:
            text = out
    leases: dict[str, dict[str, str]] = {}
    for ln in text.splitlines():
        parts = ln.split()
        if len(parts) >= 4 and ":" in parts[1]:
            host = parts[3]
            if host in ("*", ""):
                host = ""
            leases[parts[1].lower()] = {"ip": parts[2], "hostname": host}
    return leases


_HWADATA_OUI: dict[str, str] | None = None
_device_names_cache: dict[str, str] | None = None
_device_names_mtime: float = 0.0
_mdns_cache: dict[str, tuple[float, str]] = {}

def load_device_names() -> dict[str, str]:
    """Load user-assigned device names {mac.lower(): name}, cached by mtime."""
    global _device_names_cache, _device_names_mtime
    try:
        mtime = DEVICES_FILE.stat().st_mtime if DEVICES_FILE.is_file() else 0.0
    except OSError:
        mtime = 0.0
    if _device_names_cache is not None and mtime == _device_names_mtime:
        return _device_names_cache
    data: dict[str, str] = {}
    if DEVICES_FILE.is_file():
        try:
            with open(DEVICES_FILE, encoding="utf-8") as f:
                raw = json.load(f)
                if isinstance(raw, dict):
                    data = {str(k).lower(): str(v) for k, v in raw.items()}
        except Exception:
            data = {}
    _device_names_cache, _device_names_mtime = data, mtime
    return data

def save_device_names(names: dict[str, str]) -> None:
    """Persist the user-assigned device map and refresh the cache."""
    global _device_names_cache, _device_names_mtime
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in names.items()}, f, indent=2, sort_keys=True)
    except OSError:
        return
    _device_names_cache = {str(k).lower(): str(v) for k, v in names.items()}
    try:
        _device_names_mtime = DEVICES_FILE.stat().st_mtime
    except OSError:
        _device_names_mtime = 0.0

def resolve_mdns_name(ip: str, timeout: float = 3.0) -> str:
    """Reverse-resolve an AP client IP via mDNS (.local) to its device name.

    iOS/Android publish their device name over Bonjour even with a private
    (randomized) MAC — exactly the case that defeats the DHCP-lease and
    OUI lookups. Cached per-IP for 30s. Returns "" when unresolvable.
    """
    if not ip:
        return ""
    now = time.monotonic()
    hit = _mdns_cache.get(ip)
    if hit and now - hit[0] < 30:
        return hit[1]
    name = ""
    for cmd in (["getent", "hosts", ip], ["avahi-resolve-address", ip]):
        if not shutil.which(cmd[0]):
            continue
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
        except (subprocess.SubprocessError, OSError):
            continue
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].lower().endswith(".local"):
                name = parts[1][:-6]
                break
        if name:
            break
    name = name.strip(" .")
    if len(name) > 63:
        name = name[:63]
    _mdns_cache[ip] = (now, name)
    return name

def _load_oui_vendors() -> dict[str, str]:
    """Load MAC OUI prefix -> vendor from /usr/share/hwdata/oui.txt (hwdata)."""
    global _HWADATA_OUI
    if _HWADATA_OUI is not None:
        return _HWADATA_OUI
    out: dict[str, str] = {}
    try:
        with open("/usr/share/hwdata/oui.txt", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"^([0-9A-F]{2}[-:][0-9A-F]{2}[-:][0-9A-F]{2})\s+\(hex\)\s+(.+)$",
                             line.strip())
                if m:
                    out[m.group(1).replace("-", ":").replace(":", "").lower()] = m.group(2).strip()
    except OSError:
        pass
    _HWADATA_OUI = out
    return out

def client_device_name(mac: str, leases: dict[str, dict[str, str]] | None = None,
                       oui: dict[str, str] | None = None) -> str:
    """Best-effort human name for a client MAC.

    Priority: user-assigned name (persisted) -> DHCP hostname from the dnsmasq
    lease (iOS/Android report their device name, e.g. \"Vansh's iPhone\") ->
    mDNS reverse lookup (works with private MACs) -> MAC OUI vendor -> "".
    """
    mac = mac.lower()
    manual = load_device_names().get(mac)
    if manual:
        return manual
    if leases is None:
        leases = read_leases()
    host = leases.get(mac, {}).get("hostname", "")
    host = host.strip(" \t\"'")
    if host and host != "*":
        return host
    ip = leases.get(mac, {}).get("ip", "")
    mdns = resolve_mdns_name(ip) if ip else ""
    if mdns:
        return mdns
    if oui is None:
        oui = _load_oui_vendors()
    vendor = oui.get(mac.replace(":", "")[:6], "")
    return vendor.split(",")[0].strip() if vendor else ""

def smart_reconnect(config: dict[str, Any]) -> tuple[bool, str]:
    """Perform a smart reconnect if connectivity is limited.

    Uses module-level state for back-off/cooldown tracking. Returns
    (reconnected, reason) -- (False, "ok") if connectivity is fine or
    reconnect was skipped.

    Args:
        config: Application configuration dict.

    Returns:
        (reconnected, reason): True if a reconnect was performed.
    """
    if not config.get("reconnect_enabled", True):
        return False, "disabled"

    ok, reason = check_connectivity()
    if ok:
        _RECONNECT_STATE["failures"] = 0
        return False, "ok"

    now = time.monotonic()
    cooldown = float(config.get("reconnect_cooldown_sec", 300))
    if now - _RECONNECT_STATE["last_reconnect_time"] < cooldown:
        return False, "cooldown"

    base = float(config.get("reconnect_backoff_sec", 30))
    wait = min(base * (2 ** _RECONNECT_STATE["failures"]), 300.0)
    if _RECONNECT_STATE["last_reconnect_time"] > 0 and (now - _RECONNECT_STATE["last_reconnect_time"]) < wait:
        return False, "backoff"

    if DISABLED_FLAG.exists():
        return False, "manually-disabled"

    if now - _RECONNECT_STATE["last_user_interaction"] < 300:
        return False, "user-active"

    # Perform reconnect.
    attempt = _RECONNECT_STATE["failures"] + 1
    logger.info("Smart reconnect (attempt %d): %s", attempt, reason)
    _RECONNECT_STATE["last_reconnect_time"] = now
    _RECONNECT_STATE["failures"] += 1

    priv(["ip", "link", "set", STA_IF, "down"])
    time.sleep(2)
    priv(["ip", "link", "set", STA_IF, "up"])

    invalidate_cache()
    return True, reason


def warp(action: str) -> tuple[bool, str]:
    """Connect or disconnect Cloudflare WARP.

    Args:
        action: "connect" or "disconnect".

    Returns:
        (ok, message) tuple.
    """
    if action == "connect":
        if shutil.which("warp-cli"):
            rc, out = run_rc(["warp-cli", "connect"], timeout=30, max_retries=1)
            return rc == 0, out or "connected"
        return False, "warp-cli not found"
    elif action == "disconnect":
        if shutil.which("warp-cli"):
            rc, out = run_rc(["warp-cli", "disconnect"], timeout=30, max_retries=1)
            return rc == 0, out or "disconnected"
        return False, "warp-cli not found"
    return False, f"unknown action: {action}"


def sync_restart_service() -> tuple[bool, str]:
    """Restart channel-sync service and enforce VPN bypass routing."""
    invalidate_cache()
    rc, out = priv(["/usr/local/bin/hotspot-channelsync.sh"], timeout=30)
    if rc == 0:
        logger.info("channel-sync restarted successfully")
        return True, "channel-sync restarted"
    logger.error("channel-sync restart failed: %s", out)
    return False, out or "failed"


def fix_routing_service() -> tuple[bool, str]:
    """Run channel-sync script to enforce VPN bypass routing."""
    return sync_restart_service()


def detect_state() -> str:
    """Detect hotspot state: 'on', 'off', or 'error'."""
    try:
        cons = active_cons()
        if cons.get(HOTSPOT_CON) == AP_IF:
            return "on"
        return "off"
    except Exception:
        return "error"


def hotspot_on_service() -> tuple[bool, str]:
    """Activate hotspot (backend only)."""
    invalidate_cache()
    ok, msg = hotspot_start()
    if ok:
        logger.info("Hotspot On succeeded: %s", msg)
    else:
        logger.error("Hotspot On failed: %s", msg)
    return ok, msg


def hotspot_off_service() -> tuple[bool, str]:
    """Deactivate hotspot (backend only)."""
    invalidate_cache()
    ok, msg = hotspot_stop()
    if ok:
        logger.info("Hotspot Off succeeded: %s", msg)
    else:
        logger.error("Hotspot Off failed: %s", msg)
    return ok, msg


def hotspot_restart_service() -> tuple[bool, str]:
    """Restart hotspot (backend only)."""
    invalidate_cache()
    ok, msg = hotspot_cycle()
    if ok:
        logger.info("Hotspot Restart succeeded: %s", msg)
    else:
        logger.error("Hotspot Restart failed: %s", msg)
    return ok, msg


# ---------------------------------------------------------------------------
# Connectivity probes — ICMP-agnostic.  Some networks (campus / enterprise
# 802.1X like KIIT-WIFI) drop ICMP entirely while TCP/DNS traffic flows
# normally, so a bare gateway ping would misreport a working uplink as
# "limited" and trigger needless smart-reconnect cycles.  We therefore treat
# a failed ping as inconclusive and fall back to real-traffic probes.
# ---------------------------------------------------------------------------

_TCP_PROBE_TARGETS: tuple[tuple[str, int], ...] = (
    ("1.1.1.1", 443),
    ("8.8.8.8", 53),
    ("208.67.222.222", 443),  # OpenDNS — reachable where 1.1.1.1/8.8.8.8 may not be
)
_TCP_PROBE_TIMEOUT: float = 2.0


def _gateway_ip() -> str:
    """Return the IPv4 default gateway ("default via X" in `ip route show default`)."""
    try:
        route_out = run(["ip", "route", "show", "default"], timeout=3, max_retries=1)
        m = re.search(r"default via (\S+)", route_out)
        return m.group(1) if m else ""
    except Exception as exc:
        logger.debug("_gateway_ip: ip route error: %s", exc)
        return ""


def _ping_ok(target: str) -> bool:
    """One-shot ICMP ping with a short wait; True on exit code 0."""
    try:
        rc, _ = run_rc(
            ["ping", "-c", "1", "-W", "2", target],
            timeout=4,
            max_retries=1,
            use_cache=False,
        )
        return rc == 0
    except Exception as exc:
        logger.debug("_ping_ok(%s): %s", target, exc)
        return False


def _dns_resolves() -> bool:
    """True if example.com resolves via getent (fallback: socket.gethostbyname)."""
    try:
        if shutil.which("getent"):
            rc, _ = run_rc(
                ["getent", "hosts", "example.com"],
                timeout=5,
                max_retries=1,
                use_cache=False,
            )
            return rc == 0
        socket.gethostbyname("example.com")
        return True
    except socket.gaierror:
        return False
    except Exception as exc:
        logger.debug("_dns_resolves: %s", exc)
        return False


def _tcp_connect_ok(timeout: float = _TCP_PROBE_TIMEOUT) -> bool:
    """True if any well-known endpoint accepts a TCP connection (real routed path)."""
    for host, port in _TCP_PROBE_TARGETS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            logger.debug("_tcp_connect_ok: %s:%d timed out/refused", host, port)
            continue
    return False


def health_check() -> dict[str, Any]:
    """Perform health checks: gateway reachability, DHCP leases, and AP interface state.

    Returns:
        dict[str, Any] with keys:
            - 'healthy': bool, True if all checks pass
            - 'gateway_ok': bool, True if the gateway is reachable via ICMP or,
              on networks that drop ICMP, via a TCP/DNS fallback probe
            - 'gateway_ip': str, IP address of default gateway or 'none'
            - 'gateway_probe': str, how reachability was established
              ('ok'=ICMP, 'icmp-blocked'=TCP/DNS, or a failure reason)
            - 'leases_ok': bool, True if DHCP lease status is readable
            - 'leases_count': str, formatted count or status of DHCP leases
            - 'ap_up': bool, True if AP interface is UP
            - 'summary': str, concise human-readable summary
    """
    # 1. Gateway reachability — ICMP with a real-traffic fallback, because
    #    some networks (e.g. KIIT-WIFI) drop ICMP while still carrying data.
    gw_ip = _gateway_ip()
    if not gw_ip:
        gw_ip = "none"
    gateway_ok, _reason = check_connectivity(gw_ip if gw_ip != "none" else "")

    # 2. Check DHCP leases
    leases_str = lease_count()
    leases_ok = not leases_str.lower().startswith("error")

    # 3. Verify AP interface UP
    ap_up = False
    try:
        ap_sys = Path(f"/sys/class/net/{AP_IF}")
        if ap_sys.exists():
            state_file = ap_sys / "operstate"
            operstate = state_file.read_text(encoding="utf-8").strip().lower() if state_file.exists() else ""
            flags_file = ap_sys / "flags"
            flags = int(flags_file.read_text().strip(), 16) if flags_file.exists() else 0
            is_iff_up = bool(flags & 0x1)  # IFF_UP flag
            ap_up = is_iff_up or operstate in ("up", "dormant", "unknown")
        if not ap_up:
            link_out = run(["ip", "link", "show", "dev", AP_IF], timeout=3, max_retries=1)
            ap_up = "<UP" in link_out or ",UP" in link_out or "state UP" in link_out
    except Exception as e:
        logger.warning("Health check AP interface error: %s", e)
        ap_up = False

    healthy = gateway_ok and ap_up and leases_ok
    if gateway_ok and _reason == "icmp-blocked":
        gw_status = f"GW: ok-ICMP-blocked ({gw_ip})"
    else:
        gw_status = f"GW: {'ok' if gateway_ok else 'unreachable'} ({gw_ip})"
    ap_status = f"AP: {'UP' if ap_up else 'DOWN'}"
    dhcp_status = f"DHCP: {'ok' if leases_ok else 'fail'} ({leases_str})"
    summary = f"{'OK' if healthy else 'Degraded'} [{gw_status} | {ap_status} | {dhcp_status}]"

    res = {
        "healthy": healthy,
        "gateway_ok": gateway_ok,
        "gateway_ip": gw_ip,
        "gateway_probe": _reason,
        "leases_ok": leases_ok,
        "leases_count": leases_str,
        "ap_up": ap_up,
        "summary": summary,
    }
    return res

def check_connectivity(gw_ip: str = "") -> tuple[bool, str]:
    """Check whether the STA uplink really has connectivity.

    Some networks (e.g. campus / enterprise 802.1X like KIIT-WIFI) drop ICMP
    while TCP/DNS traffic flows normally, so a bare gateway ping misreports a
    working link as "limited" (and can trigger needless smart reconnects).
    Probe order: gateway ping (fast path) -> DNS resolution + TCP connect
    (real-traffic fallback).  First success wins.

    Args:
        gw_ip: Optional already-discovered gateway IP to reuse; discovered if empty.

    Returns:
        tuple[bool, str]:
            ``(True, "ok")``            gateway answered ICMP
            ``(True, "icmp-blocked")``  ICMP failed but DNS + TCP probes succeeded
            ``(True, "tcp-only")``      DNS failed but a TCP probe succeeded
            ``(False, reason)``         no probe succeeded; *reason* states the symptom
    """
    if not gw_ip:
        gw_ip = _gateway_ip()
    if not gw_ip:
        return False, "no default gateway"

    # Fast path: ICMP to the gateway. Most networks answer; a failure is only
    # inconclusive (ICMP may be filtered), never fatal by itself.
    if _ping_ok(gw_ip):
        return True, "ok"

    # ICMP blocked/unsuccessful: prove real traffic flows.
    dns_ok = _dns_resolves()
    tcp_ok = _tcp_connect_ok()
    if dns_ok and tcp_ok:
        return True, "icmp-blocked"
    if tcp_ok:
        return True, "tcp-only"

    return False, f"gateway unreachable ({gw_ip})"

def hotspot_start() -> tuple[bool, str]:
    """Ensure ap0 exists, clear disabled flag, and activate hotspot connection. Return (ok, message)."""
    invalidate_cache()
    logger.info("hotspot_start requested: activating %s on %s", HOTSPOT_CON, AP_IF)
    set_disabled_flag(False)
    ok, msg = ensure_ap0()
    if not ok:
        logger.error("hotspot_start failed during ensure_ap0: %s", msg)
        return False, msg
    rc, out = run_rc(["nmcli", "con", "up", HOTSPOT_CON], timeout=30)
    invalidate_cache()
    if rc == 0:
        logger.info("hotspot_start succeeded: %s", out)
        return True, f"{HOTSPOT_CON} activated on {AP_IF}"
    err = out[-300:] or "nmcli failed"
    logger.error("hotspot_start nmcli failed: %s", err)
    return False, err

def hotspot_stop() -> tuple[bool, str]:
    """Deactivate hotspot connection and create disabled flag. Return (ok, message).

    The disabled flag gates hotspot-channelsync.service (a 10s ticker that
    resurrects the AP whenever wlan0 STA is associated): it must be set BEFORE
    the down and unconditionally, or the hotspot comes straight back on after
    a toggle-off ("the toggle doesn't work").
    """
    invalidate_cache()
    logger.info("hotspot_stop requested: deactivating %s", HOTSPOT_CON)
    set_disabled_flag(True)
    rc, out = run_rc(["nmcli", "con", "down", HOTSPOT_CON], timeout=30)
    invalidate_cache()
    if rc == 0:
        logger.info("hotspot_stop succeeded")
        return True, f"{HOTSPOT_CON} deactivated"
    err = out[-300:] or "nmcli down failed"
    logger.error("hotspot_stop failed: %s", err)
    return False, err

def hotspot_cycle() -> tuple[bool, str]:
    """Cycle hotspot connection off and on, clearing disabled flag. Return (ok, message)."""
    invalidate_cache()
    logger.info("hotspot_cycle requested for %s", HOTSPOT_CON)
    set_disabled_flag(False)
    ok, msg = ensure_ap0()
    if not ok:
        logger.error("hotspot_cycle failed during ensure_ap0: %s", msg)
        return False, msg
    run_rc(["nmcli", "con", "down", HOTSPOT_CON], timeout=30)
    rc, out = run_rc(["nmcli", "con", "up", HOTSPOT_CON], timeout=30)
    invalidate_cache()
    if rc == 0:
        logger.info("hotspot_cycle succeeded: %s", out)
        return True, f"{HOTSPOT_CON} restarted on {AP_IF}"
    err = out[-300:] or "nmcli failed"
    logger.error("hotspot_cycle failed: %s", err)
    return False, err

def kick_client(mac: str) -> tuple[bool, str]:
    """Disconnect a station via `iw dev <AP_IF> station del <mac>`."""
    rc, out = priv(["iw", "dev", AP_IF, "station", "del", mac])
    if rc == 0:
        return True, f"Station {mac} disconnected"
    return False, f"Failed to disconnect {mac}: {(out[-200:] or 'iw failed')}"

def read_net_dev_bytes() -> dict[str, tuple[int, int]]:
    """Read /proc/net/dev and return {interface: (rx_bytes, tx_bytes)}."""
    stats: dict[str, tuple[int, int]] = {}
    try:
        with open("/proc/net/dev", encoding="utf-8") as f:
            for line in f:
                if ":" not in line:
                    continue
                iface, _, data = line.partition(":")
                parts = data.split()
                if len(parts) >= 9:
                    try:
                        rx = int(parts[0])
                        tx = int(parts[8])
                        stats[iface.strip()] = (rx, tx)
                    except ValueError:
                        continue
    except OSError:
        pass
    return stats

def format_rate(bytes_per_sec: float) -> str:
    """Format bytes/s with adaptive units (B/s, kB/s, MB/s, GB/s).

    MB/s with one decimal rounds sub-50-kB/s traffic to "0.0MB/s", which
    made the Bandwidth card look dead for typical phone/laptop traffic.
    """
    if bytes_per_sec <= 0:
        return "0 B/s"
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f} B/s"
    kb_s = bytes_per_sec / 1024
    if kb_s < 1024:
        return f"{kb_s:.1f}kB/s"
    mb_s = kb_s / 1024
    if mb_s < 1024:
        return f"{mb_s:.1f}MB/s"
    return f"{mb_s / 1024:.1f}GB/s"

def set_bandwidth_limit(iface: str, rate_mbps: float) -> tuple[bool, str]:
    """Apply a total bandwidth cap (Mbps) on an interface using tc (token bucket filter).

    Replaces any existing root qdisc on the interface. Use rate_mbps <= 0 to clear.

    Args:
        iface: Interface name (e.g. 'ap0', 'wlan0').
        rate_mbps: Rate limit in Mbps; 0 or negative clears the limit.

    Returns:
        (ok, message) tuple.
    """
    if rate_mbps <= 0:
        return clear_bandwidth_limit(iface)

    # tc uses kbit/s; 1 Mbps = 1000 kbit/s
    rate_kbit = int(rate_mbps * 1000)
    burst_kb = max(16, rate_kbit // 8)
    # Replace existing root qdisc with tbf
    rc, out = priv(["tc", "qdisc", "replace", "dev", iface, "root", "tbf",
                    "rate", f"{rate_kbit}kbit", "burst", f"{burst_kb}kb", "latency", "50ms"],
                   timeout=15)
    if rc == 0:
        logger.info("Bandwidth limit set on %s: %s Mbps", iface, rate_mbps)
        return True, f"Throttled {iface} to {rate_mbps} Mbps"
    logger.error("Failed to set bandwidth limit on %s: %s", iface, out)
    return False, out or "tc failed"

def clear_bandwidth_limit(iface: str) -> tuple[bool, str]:
    """Remove any bandwidth limit (root qdisc) from an interface.

    Args:
        iface: Interface name.

    Returns:
        (ok, message) tuple.
    """
    rc, out = priv(["tc", "qdisc", "del", "dev", iface, "root"], timeout=15)
    # tc returns non-zero if there was no qdisc; treat as success.
    if rc == 0 or "Cannot delete" in out or "No such file" in out:
        logger.info("Bandwidth limit cleared on %s", iface)
        return True, f"Cleared bandwidth limit on {iface}"
    logger.error("Failed to clear bandwidth limit on %s: %s", iface, out)
    return False, out or "tc failed"

def autostart_desktop_path() -> Path:
    """Return path to autostart desktop file (~/.config/autostart/hotspot-hub.desktop)."""
    return Path.home() / ".config" / "autostart" / "hotspot-hub.desktop"

def is_autostart_enabled() -> bool:
    """Check if autostart desktop entry exists and is enabled."""
    return autostart_desktop_path().is_file()

def set_autostart(enabled: bool) -> bool:
    """Enable or disable autostart by creating or deleting desktop entry."""
    target = autostart_desktop_path()
    try:
        if enabled:
            target.parent.mkdir(parents=True, exist_ok=True)
            for src in (
                Path(__file__).resolve().parent.parent / "hotspot-hub.desktop",
                Path("/usr/share/applications/hotspot-hub.desktop"),
            ):
                if src.is_file():
                    shutil.copyfile(src, target)
                    target.chmod(0o755)
                    return True
            content = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Hotspot Hub\n"
                "Comment=Tray control for concurrent Wi-Fi hotspot, WARP and channel-sync\n"
                "Exec=hotspot-hub\n"
                "Icon=hotspot-hub\n"
                "Terminal=false\n"
                "Categories=Network;\n"
                "StartupNotify=false\n"
                "X-GNOME-Autostart-enabled=true\n"
            )
            target.write_text(content, encoding="utf-8")
            target.chmod(0o755)
            return True
        else:
            if target.exists():
                target.unlink()
            return True
    except OSError:
        return False
