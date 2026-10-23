#!/usr/bin/env python3
"""Hotspot Hub - KDE tray control with dropdown canvas (CachyOS).
Dropdown canvas is extensible: add a PanelSection subclass + register in
DropdownPanel._build_sections(). Privileged ops use passwordless sudo
(/etc/sudoers.d/hotspot-hub), pkexec fallback. No plaintext passwords."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QEvent, QRectF, QSettings, Qt, QTime, QTimer
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCursor,
    QFocusEvent,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QSpinBox,
    QSystemTrayIcon,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

# Optional KColorScheme support (PyKF6 / PyKF5)
try:
    from PyKF6.KColorScheme import KColorScheme  # type: ignore
except ImportError:
    try:
        from PyKF5.KColorScheme import KColorScheme  # type: ignore
    except ImportError:
        KColorScheme = None  # Fallback to QPalette

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
LOG_DIR = Path.home() / ".local" / "share" / "hotspot-hub"
LOG_FILE = LOG_DIR / "hotspot-hub.log"
CONFIG_DIR = Path.home() / ".config" / "hotspot-hub"
CONFIG_FILE = CONFIG_DIR / "config.json"
ALLOWED_MACS_FILE = CONFIG_DIR / "allowed_macs.txt"
BLOCKED_MACS_FILE = CONFIG_DIR / "blocked_macs.txt"
DISABLED_FLAG = Path("/run/hotspot-hub-disabled")


def setup_logging() -> logging.Logger:
    """Configure persistent rotating file logging and stderr stream logging."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("hotspot-hub")
    log.setLevel(logging.INFO)
    if not log.handlers:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        # Rotate at 5MB, keep 3 backup copies
        file_handler = RotatingFileHandler(
            str(LOG_FILE),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(logging.INFO)
        log.addHandler(file_handler)
    return log


logger = setup_logging()

# TTL cache structures: (cmd_tuple, timeout) -> (output, timestamp)
_RUN_CACHE: dict[tuple[tuple[str, ...], int | float], tuple[str, float]] = {}
_RUN_RC_CACHE: dict[tuple[tuple[str, ...], int | float], tuple[tuple[int, str], float]] = {}
CACHE_TTL: float = 5.0
BACKOFF_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0)

_MUTATING_KEYWORDS: set[str] = {
    "up", "down", "add", "del", "delete", "set", "restart", "start", "stop", "connect", "disconnect"
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


DEFAULT_CONFIG: dict[str, Any] = {
    "mac_filter_enabled": False,
    "mac_filter_mode": "allow",  # "allow" (whitelist) or "deny" (blacklist)
    "band": "Auto",              # "Auto", "2.4GHz", "5GHz"
    "wpa3_supported": False,
    "security": "wpa2",          # "wpa2" or "wpa3-sae"
    "schedule_enabled": False,
    "schedule_on": "08:00",
    "schedule_off": "22:00",
    "battery_saver_enabled": False,
    "battery_threshold": 20,
    "ipv6_enabled": False,
}


def validate_config(cfg: dict[str, Any]) -> list[str]:
    """Validate application configuration values against allowed ranges and formats.

    Checks:
        - band in ("Auto", "2.4GHz", "5GHz")
        - security in ("wpa2", "wpa3-sae")
        - schedule_on/schedule_off match regex ^\\d{2}:\\d{2}$ and valid hours/minutes
        - battery_threshold between 5 and 50

    Args:
        cfg: Configuration dictionary to validate.

    Returns:
        list[str]: Human-readable warning strings for invalid configuration values (empty if valid).
    """
    warnings: list[str] = []

    band = cfg.get("band")
    if band not in ("Auto", "2.4GHz", "5GHz"):
        warnings.append(f"Invalid band '{band}': expected 'Auto', '2.4GHz', or '5GHz'")

    security = cfg.get("security")
    if security not in ("wpa2", "wpa3-sae"):
        warnings.append(f"Invalid security '{security}': expected 'wpa2' or 'wpa3-sae'")

    time_regex = re.compile(r"^\d{2}:\d{2}$")
    for key in ("schedule_on", "schedule_off"):
        val = cfg.get(key)
        if not isinstance(val, str) or not time_regex.match(val):
            warnings.append(f"Invalid format for '{key}': '{val}', expected HH:MM")
        else:
            try:
                h, m = map(int, val.split(":"))
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    warnings.append(f"Invalid time in '{key}': '{val}', hours must be 0-23 and minutes 0-59")
            except ValueError:
                warnings.append(f"Invalid time in '{key}': '{val}'")

    threshold = cfg.get("battery_threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not (5 <= threshold <= 50):
        warnings.append(f"Invalid battery_threshold '{threshold}': expected integer between 5 and 50")

    return warnings


def load_app_config() -> dict[str, Any]:
    """Load persistent configuration from ~/.config/hotspot-hub/config.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.is_file():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    cfg.update(data)
        except Exception as e:
            logger.warning("Failed to read config from %s: %s", CONFIG_FILE, e)
    else:
        # First run: auto-detect WPA3 support and initialize config
        cfg["wpa3_supported"] = detect_wpa3_support()
        save_app_config(cfg)

    # Ensure wpa3_supported reflects live hardware if not previously recorded
    if "wpa3_supported" not in cfg or not cfg.get("wpa3_supported"):
        if detect_wpa3_support():
            cfg["wpa3_supported"] = True
            save_app_config(cfg)

    # Validate configuration and log any warnings
    warnings = validate_config(cfg)
    for warning in warnings:
        logger.warning("Config validation warning: %s", warning)

    return cfg


def save_app_config(cfg: dict[str, Any]) -> bool:
    """Save persistent configuration to ~/.config/hotspot-hub/config.json."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return True
    except OSError as e:
        logger.error("Failed to write config to %s: %s", CONFIG_FILE, e)
        return False


def detect_wpa3_support() -> bool:
    """Probe 'iw phy' for SAE (Simultaneous Authentication of Equals / WPA3) support."""
    out = run(["iw", "phy"], timeout=5)
    return bool(re.search(r"SAE", out, re.IGNORECASE))


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
    """Create ap0 passwordless (sudoers) if missing. Returns (ok, msg)."""
    try:
        if iface_info(AP_IF):
            return True, "ap0 exists"
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


def health_check() -> dict[str, Any]:
    """Perform health checks: ping gateway, check DHCP leases, and verify AP interface state.

    Returns:
        dict[str, Any] with keys:
            - 'healthy': bool, True if all checks pass
            - 'gateway_ok': bool, True if default gateway responds to ICMP ping
            - 'gateway_ip': str, IP address of default gateway or 'none'
            - 'leases_ok': bool, True if DHCP lease status is readable
            - 'leases_count': str, formatted count or status of DHCP leases
            - 'ap_up': bool, True if AP interface is UP
            - 'summary': str, concise human-readable summary
    """
    # 1. Ping default gateway
    gw_ip = ""
    gateway_ok = False
    try:
        route_out = run(["ip", "route", "show", "default"], timeout=3, max_retries=1)
        m = re.search(r"default via (\S+)", route_out)
        if m:
            gw_ip = m.group(1)
            ping_rc, _ = run_rc(["ping", "-c", "1", "-W", "1", gw_ip], timeout=2, max_retries=1, use_cache=False)
            gateway_ok = (ping_rc == 0)
        else:
            gw_ip = "none"
    except Exception as e:
        logger.warning("Health check gateway ping error: %s", e)
        gw_ip = "error"

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
    gw_status = f"GW: {'ok' if gateway_ok else 'unreachable'} ({gw_ip})"
    ap_status = f"AP: {'UP' if ap_up else 'DOWN'}"
    dhcp_status = f"DHCP: {'ok' if leases_ok else 'fail'} ({leases_str})"
    summary = f"{'OK' if healthy else 'Degraded'} [{gw_status} | {ap_status} | {dhcp_status}]"

    res = {
        "healthy": healthy,
        "gateway_ok": gateway_ok,
        "gateway_ip": gw_ip,
        "leases_ok": leases_ok,
        "leases_count": leases_str,
        "ap_up": ap_up,
        "summary": summary,
    }
    return res


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
    """Deactivate hotspot connection and create disabled flag. Return (ok, message)."""
    invalidate_cache()
    logger.info("hotspot_stop requested: deactivating %s", HOTSPOT_CON)
    rc, out = run_rc(["nmcli", "con", "down", HOTSPOT_CON], timeout=30)
    invalidate_cache()
    if rc == 0:
        logger.info("hotspot_stop succeeded")
        set_disabled_flag(True)
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
    """Format bytes per second into MB/s rate string (e.g. 1.2MB/s)."""
    if bytes_per_sec <= 0:
        return "0.0MB/s"
    mb_s = bytes_per_sec / (1024 * 1024)
    if mb_s >= 1024:
        return f"{mb_s / 1024:.1f}GB/s"
    return f"{mb_s:.1f}MB/s"


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


def get_app_font(pixel_size: int = 13, bold: bool = False) -> QFont:
    """Return primary application font (Manrope) with fallback chain."""
    font = QFont("Manrope")
    font.setFamilies(["Manrope", "Noto Sans", "DejaVu Sans", "sans-serif"])
    font.setPixelSize(pixel_size)
    if bold:
        font.setBold(True)
    return font


def get_monospace_font(pixel_size: int = 12, bold: bool = False) -> QFont:
    """Return monospace font (JetBrains Mono) with fallback chain for IPs, MACs, and metrics."""
    font = QFont("JetBrains Mono")
    font.setFamilies(["JetBrains Mono", "DejaVu Sans Mono", "monospace"])
    font.setPixelSize(pixel_size)
    if bold:
        font.setBold(True)
    return font


def init_application_fonts(app: QApplication | None = None) -> QFont:
    """Configure and apply the primary application font to QApplication.

    Sets application font to Manrope (13px body) with fallback to Noto Sans,
    DejaVu Sans, sans-serif. Monospace labels (IPs, MACs, metrics) use JetBrains Mono (12px).
    """
    font = get_app_font(pixel_size=13)
    target_app = app or QApplication.instance()
    if target_app:
        target_app.setFont(font)
    return font


def is_dark_mode(palette: QPalette | None = None) -> bool:
    """Detect if dark mode is active via QPalette lightness or KColorScheme."""
    pal = palette or QApplication.palette()
    win_col = pal.color(QPalette.ColorRole.Window)
    text_col = pal.color(QPalette.ColorRole.WindowText)
    if KColorScheme is not None:
        try:
            scheme = KColorScheme(QPalette.ColorGroup.Active, KColorScheme.ColorSet.Window)
            win_col = scheme.background().color()
            text_col = scheme.foreground().color()
        except Exception:
            pass
    return win_col.lightness() < 128 or text_col.lightness() > win_col.lightness()


def _color_to_rgba(color: QColor, alpha: float) -> str:
    """Format QColor and float alpha (0.0 - 1.0) into CSS rgba(...) string."""
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha:.2f})"


def get_theme_stylesheet(palette: QPalette | None = None) -> str:
    """Generate dynamic stylesheet matching dark/light mode and Plasma theme colors.

    Uses KColorScheme (when available) or QPalette for Plasma theme colors.
    Applies solid backgrounds with full opacity, subtle borders, 12px border radius,
    and Plasma accent color for highlights.
    """
    pal = palette or QApplication.palette()
    dark = is_dark_mode(pal)

    win_col = pal.color(QPalette.ColorRole.Window)
    win_text_col = pal.color(QPalette.ColorRole.WindowText)
    base_col = pal.color(QPalette.ColorRole.Base)
    btn_col = pal.color(QPalette.ColorRole.Button)
    btn_text_col = pal.color(QPalette.ColorRole.ButtonText)
    accent_col = pal.color(QPalette.ColorRole.Highlight)

    # Allow KColorScheme override if available
    if KColorScheme is not None:
        try:
            scheme_win = KColorScheme(QPalette.ColorGroup.Active, KColorScheme.ColorSet.Window)
            win_col = scheme_win.background().color()
            win_text_col = scheme_win.foreground().color()
            scheme_view = KColorScheme(QPalette.ColorGroup.Active, KColorScheme.ColorSet.View)
            base_col = scheme_view.background().color()
            scheme_btn = KColorScheme(QPalette.ColorGroup.Active, KColorScheme.ColorSet.Button)
            btn_col = scheme_btn.background().color()
            btn_text_col = scheme_btn.foreground().color()
            scheme_sel = KColorScheme(QPalette.ColorGroup.Active, KColorScheme.ColorSet.Selection)
            accent_col = scheme_sel.background().color()
        except Exception:
            pass

    win_text = win_text_col.name()
    btn_text = btn_text_col.name()
    accent = accent_col.name()

    if dark:
        card_base = base_col if base_col.name() != win_col.name() else QColor("#24282f")
        card_hover_base = card_base.lighter(118)
        border_base = QColor("#3a414e")
        text_muted = "#a0a0a0"
        btn_bg_base = btn_col if btn_col.name() != win_col.name() else QColor("#323844")
    else:
        card_base = base_col if base_col.name() != win_col.name() else QColor("#ffffff")
        card_hover_base = QColor("#ffffff") if card_base.lightness() > 240 else card_base.lighter(106)
        border_base = QColor("#cbd5e1")
        text_muted = "#64748b"
        btn_bg_base = btn_col if btn_col.name() != win_col.name() else QColor("#f1f5f9")

    # Solid colors with full opacity for canvas, cards, and buttons
    canvas_bg = win_col.name()
    canvas_border = border_base.name()
    card_bg = card_base.name()
    card_hover_bg = card_hover_base.name()
    card_border = border_base.name()
    card_hover_border = accent  # Plasma accent highlight

    btn_bg = btn_bg_base.name()
    btn_hover_bg = btn_bg_base.lighter(112).name()
    btn_pressed_bg = (btn_bg_base.darker(110) if dark else btn_bg_base.darker(105)).name()

    return f"""
        QWidget#dropdownCanvas {{
            background-color: {canvas_bg};
            color: {win_text};
            border: 1px solid {canvas_border};
            border-radius: 12px;
            font-family: 'Manrope', 'Noto Sans', 'DejaVu Sans', sans-serif;
            font-size: 13px;
        }}
        QFrame[panelCard="true"] {{
            background-color: {card_bg};
            color: {win_text};
            border: 1px solid {card_border};
            border-radius: 12px;
        }}
        QFrame[panelCard="true"]:hover {{
            background-color: {card_hover_bg};
            border: 1px solid {card_hover_border};
        }}
        QLabel {{
            color: {win_text};
            font-family: 'Manrope', 'Noto Sans', 'DejaVu Sans', sans-serif;
            font-size: 13px;
        }}
        QLabel[headingLabel="true"] {{
            font-family: 'Manrope', 'Noto Sans', 'DejaVu Sans', sans-serif;
            font-size: 15px;
            font-weight: bold;
            color: {win_text};
        }}
        QLabel[metricLabel="true"],
        .metricLabel {{
            font-family: 'JetBrains Mono', 'DejaVu Sans Mono', monospace;
            font-size: 12px;
            color: {win_text};
        }}
        QPushButton {{
            background-color: {btn_bg};
            color: {btn_text};
            border: 1px solid {card_border};
            border-radius: 6px;
            padding: 6px 12px;
            min-height: 30px;
            font-family: 'Manrope', 'Noto Sans', 'DejaVu Sans', sans-serif;
            font-size: 13px;
        }}
        QPushButton#closeBtn {{
            padding: 0px;
            font-size: 14px;
            font-weight: bold;
            min-height: 36px;
            max-height: 36px;
            min-width: 36px;
            max-width: 36px;
        }}
        QPushButton:hover {{
            background-color: {btn_hover_bg};
            border-color: {accent};
        }}
        QPushButton:pressed {{
            background-color: {btn_pressed_bg};
        }}
        QPushButton:focus {{
            border-color: {accent};
            outline: none;
        }}
        QPushButton:disabled {{
            color: {text_muted};
            border-color: transparent;
            background-color: transparent;
        }}
        QCheckBox {{
            color: {win_text};
            font-family: 'Manrope', 'Noto Sans', 'DejaVu Sans', sans-serif;
            font-size: 13px;
            spacing: 6px;
        }}
        QCheckBox::indicator {{
            width: 15px;
            height: 15px;
            border-radius: 4px;
            border: 1px solid {card_border};
            background-color: {card_bg};
        }}
        QCheckBox::indicator:hover {{
            border-color: {accent};
        }}
        QCheckBox::indicator:checked {{
            background-color: {accent};
            border-color: {accent};
        }}
        QSizeGrip {{
            width: 16px;
            height: 16px;
        }}
        QComboBox, QLineEdit, QListWidget {{
            background-color: {card_bg};
            color: {win_text};
            border: 1px solid {card_border};
            border-radius: 6px;
            padding: 4px 8px;
            font-family: 'Manrope', 'Noto Sans', 'DejaVu Sans', sans-serif;
            font-size: 13px;
        }}
        QComboBox:focus, QLineEdit:focus, QListWidget:focus {{
            border-color: {accent};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 20px;
        }}
        QTabWidget::pane {{
            border: 1px solid {card_border};
            border-radius: 8px;
            background-color: {card_bg};
        }}
        QTabBar::tab {{
            background-color: {btn_bg};
            color: {win_text};
            padding: 5px 12px;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            margin-right: 2px;
            font-family: 'Manrope', 'Noto Sans', 'DejaVu Sans', sans-serif;
            font-size: 13px;
        }}
        QTabBar::tab:hover {{
            border-color: {accent};
        }}
        QTabBar::tab:selected {{
            background-color: {card_bg};
            border: 1px solid {card_border};
            border-bottom: 2px solid {accent};
        }}
        QGroupBox {{
            color: {win_text};
            font-family: 'Manrope', 'Noto Sans', 'DejaVu Sans', sans-serif;
            font-weight: bold;
            font-size: 13px;
            border: 1px solid {card_border};
            border-radius: 8px;
            margin-top: 8px;
            padding-top: 10px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
        }}
        QRadioButton {{
            color: {win_text};
            font-family: 'Manrope', 'Noto Sans', 'DejaVu Sans', sans-serif;
            font-size: 13px;
            spacing: 6px;
        }}
        QRadioButton::indicator:checked {{
            background-color: {accent};
            border-color: {accent};
        }}
    """


STATUS_COLORS: dict[str, QColor] = {
    "on": QColor("#22c55e"),      # green: hotspot active
    "green": QColor("#22c55e"),
    "off": QColor("#94a3b8"),     # grey: hotspot inactive
    "grey": QColor("#94a3b8"),
    "gray": QColor("#94a3b8"),
    "error": QColor("#ef4444"),   # red: error state
    "red": QColor("#ef4444"),
}

_STATUS_ICON_CACHE: dict[tuple[str, int], QIcon] = {}


def get_base_icon() -> QIcon:
    """Return the base QIcon from hotspot-hub.svg or theme fallback."""
    icon_paths = [
        Path(__file__).resolve().parent.parent / "icons" / "hotspot-hub.svg",
        Path("/usr/share/icons/hicolor/scalable/apps/hotspot-hub.svg"),
    ]
    for p in icon_paths:
        if p.exists():
            icon = QIcon(str(p))
            if not icon.isNull():
                return icon
        return QIcon.fromTheme(
            "hotspot-hub",
            QIcon.fromTheme("network-wireless-hotspot",
                            QIcon.fromTheme("network-wireless",
                                            QIcon.fromTheme("network-modem")))
        )


def make_status_icon(state: str, size: int = 64) -> QIcon:
    """Generate a colored tray icon (green=hotspot on, grey=off, red=error) using QPixmap painting.

    The base icon is hotspot-hub.svg (or fallback network-wireless-hotspot).
    Tint it with a colored overlay circle in the bottom-right corner.
    """
    cache_key = (state.lower(), size)
    if cache_key in _STATUS_ICON_CACHE:
        return _STATUS_ICON_CACHE[cache_key]

    color = STATUS_COLORS.get(state.lower(), STATUS_COLORS["off"])
    base_icon = get_base_icon()

    icon = QIcon()
    sizes = (size,) if size != 64 else (22, 24, 32, 48, 64)
    for s in sizes:
        base_pix = base_icon.pixmap(s, s)
        if base_pix.isNull():
            fallback = QIcon.fromTheme(
                "network-wireless-hotspot",
                QIcon.fromTheme("network-wireless", QIcon.fromTheme("network-modem"))
            )
            base_pix = fallback.pixmap(s, s)

        pix = QPixmap(s, s)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if not base_pix.isNull():
                painter.drawPixmap(0, 0, base_pix)
            d = max(6.0, s * 0.32)
            margin = max(1.0, s * 0.05)
            x = s - d - margin
            y = s - d - margin
            painter.setPen(QPen(QColor(15, 23, 42, 220), max(1.0, s * 0.035)))
            painter.setBrush(QBrush(color))
            painter.drawEllipse(QRectF(x, y, d, d))
        finally:
            painter.end()
        icon.addPixmap(pix)

    _STATUS_ICON_CACHE[cache_key] = icon
    return icon


# ---------------------------------------------------------------------------
# Dropdown canvas: extensible sections.
# To add future mods: subclass PanelSection, implement refresh(), append an
# instance in DropdownPanel._build_sections().
# ---------------------------------------------------------------------------
class PanelSection(QFrame):
    """Base card for the dropdown canvas with glassmorphism styling.

    Subclass + override refresh().
    """
    title = "Section"

    def __init__(self, controller: HotspotPanel, parent: QWidget | None = None) -> None:
        """Initialize the panel section with controller reference and setup layout."""
        super().__init__(parent)
        self.controller = controller
        self.setProperty("panelCard", True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        # Consistent 12px padding
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(12, 12, 12, 12)
        self.layout.setSpacing(8)

        # Header typography: 15px bold Manrope
        self.head = QLabel(f"<b>{self.title}</b>")
        self.head.setProperty("headingLabel", True)
        self.head.setFont(get_app_font(pixel_size=15, bold=True))
        self.layout.addWidget(self.head)

        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        self.layout.addLayout(self.body)
        self.build_body()

    def changeEvent(self, event: QEvent) -> None:
        """Handle widget change events with recursion guard.

        Args:
            event: Qt event passed to widget.
        """
        if getattr(self, "_in_change_event", False):
            super().changeEvent(event)
            return
        self._in_change_event = True
        try:
            super().changeEvent(event)
        finally:
            self._in_change_event = False

    def build_body(self) -> None:
        """Create widgets. Override in subclass."""

    def refresh(self) -> None:
        """Update widgets from live state. Override in subclass."""

    def row(self, *widgets: QWidget) -> QHBoxLayout:
        """Add a horizontal row of widgets to the section layout.

        Ensures buttons in rows have consistent sizing with min-height 28px.
        """
        h = QHBoxLayout()
        h.setSpacing(6)
        for w in widgets:
            if isinstance(w, QPushButton):
                w.setMinimumHeight(28)
            h.addWidget(w)
        self.body.addLayout(h)
        return h


class HotspotSection(PanelSection):
    title = "Hotspot"

    def build_body(self) -> None:
        """Build Hotspot section widgets and connect button handlers."""
        self.info = QLabel("…")
        self.info.setWordWrap(True)
        self.info.setProperty("metricLabel", True)
        self.info.setFont(get_monospace_font(pixel_size=12))
        self.body.addWidget(self.info)

        # Band selector row
        band_row = QHBoxLayout()
        band_row.setSpacing(6)
        band_lbl = QLabel("Band:")
        self.combo_band = QComboBox()
        self.combo_band.addItems(["Auto", "2.4GHz", "5GHz"])
        self.combo_band.currentTextChanged.connect(self._on_band_changed)
        band_row.addWidget(band_lbl)
        band_row.addWidget(self.combo_band, 1)
        self.body.addLayout(band_row)

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

    def _on_band_changed(self, band_name: str) -> None:
        """Handle user selection of Wi-Fi band."""
        if not band_name:
            return
        current_cfg = self.controller.config.get("band", "Auto")
        if band_name == current_cfg:
            return
        ok, msg = self.controller.set_band(band_name)
        if not ok:
            QMessageBox.warning(self, "Band Selection Error", msg)
            self.combo_band.blockSignals(True)
            idx = self.combo_band.findText(current_cfg)
            if idx >= 0:
                self.combo_band.setCurrentIndex(idx)
            self.combo_band.blockSignals(False)

    def refresh(self) -> None:
        """Update hotspot and station status labels from live system state and toggle controls."""
        ap0_ok = self.controller.ap0_available
        self.btn_on.setEnabled(ap0_ok)
        self.btn_off.setEnabled(ap0_ok)
        self.btn_restart.setEnabled(ap0_ok)
        self.btn_clients.setEnabled(ap0_ok)
        self.btn_qr.setEnabled(ap0_ok)
        self.combo_band.setEnabled(ap0_ok)

        if not ap0_ok:
            self.info.setText(
                f"Interface {AP_IF} is unavailable.\n"
                "Hotspot controls disabled."
            )
            return

        try:
            sec = get_hotspot_security()
            sta, ap, n = hotspot_state()
            self.info.setText(f"{sta}\n{ap} [{sec}] | clients {n} | leases {lease_count()}")
        except Exception as e:
            self.info.setText(f"Status error: {e}")

        # Keep band dropdown in sync with controller config
        cfg_band = self.controller.config.get("band", "Auto")
        self.combo_band.blockSignals(True)
        idx = self.combo_band.findText(cfg_band)
        if idx >= 0:
            self.combo_band.setCurrentIndex(idx)
        self.combo_band.blockSignals(False)


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


class BandwidthSection(PanelSection):
    """Bandwidth monitor card displaying RX/TX throughput for STA and AP."""
    title = "Bandwidth"

    def __init__(self, controller: HotspotPanel, parent: QWidget | None = None) -> None:
        """Initialize bandwidth section with cached interface byte counters."""
        self._last_stats: dict[str, tuple[int, int]] = read_net_dev_bytes()
        self._last_time: float = time.monotonic()
        super().__init__(controller, parent)

    def build_body(self) -> None:
        """Create widgets for bandwidth monitor."""
        self.info = QLabel("…")
        self.info.setWordWrap(True)
        self.info.setProperty("metricLabel", True)
        self.info.setFont(get_monospace_font(pixel_size=12))
        self.body.addWidget(self.info)

    def refresh(self) -> None:
        """Update bandwidth throughput calculated from /proc/net/dev deltas."""
        now = time.monotonic()
        stats = read_net_dev_bytes()
        dt = now - self._last_time if self._last_time > 0 else 5.0
        if dt <= 0:
            dt = 5.0

        def _rate_str(iface: str) -> str:
            if iface not in stats:
                return f"{iface}: down"
            rx, tx = stats[iface]
            if iface not in self._last_stats:
                return f"{iface}: ↓{format_rate(0)} ↑{format_rate(0)}"
            prev_rx, prev_tx = self._last_stats[iface]
            rx_rate = max(0.0, (rx - prev_rx) / dt) if rx >= prev_rx else 0.0
            tx_rate = max(0.0, (tx - prev_tx) / dt) if tx >= prev_tx else 0.0
            return f"{iface}: ↓{format_rate(rx_rate)} ↑{format_rate(tx_rate)}"

        wlan_part = _rate_str(STA_IF)
        ap_part = _rate_str(AP_IF)
        self.info.setText(f"{wlan_part} | {ap_part}")
        self._last_stats = stats
        self._last_time = now


class SystemSection(PanelSection):
    title = "System"

    def build_body(self) -> None:
        """Build System section widgets, autostart toggle, IPv6, schedule, battery saver, and connect action buttons."""
        self.info = QLabel("…")
        self.info.setProperty("metricLabel", True)
        self.info.setFont(get_monospace_font(pixel_size=12))
        self.body.addWidget(self.info)
        self.btn_sync = QPushButton("Restart channel-sync")
        self.btn_route = QPushButton("Fix client routing")
        self.btn_mac_filter = QPushButton("MAC Filter…")
        self.btn_docs = QPushButton("Docs")
        self.btn_sync.clicked.connect(self.controller.sync_restart)
        self.btn_route.clicked.connect(self.controller.fix_routing)
        self.btn_mac_filter.clicked.connect(self.controller.show_mac_filter)
        self.btn_docs.clicked.connect(lambda: run(["xdg-open", DOC_PATH], max_retries=1))
        self.row(self.btn_sync, self.btn_route)
        self.row(self.btn_mac_filter, self.btn_docs)

        self.health_info = QLabel("<b>Health:</b> checking…")
        self.health_info.setWordWrap(True)
        self.body.addWidget(self.health_info)

        self.chk_autostart = QCheckBox("Start on boot")
        self.chk_autostart.setChecked(is_autostart_enabled())
        self.chk_autostart.toggled.connect(self._toggle_autostart)

        # IPv6 support toggle
        self.chk_ipv6 = QCheckBox("IPv6")
        self.chk_ipv6.setEnabled(detect_ipv6_support())
        self.chk_ipv6.setChecked(bool(self.controller.config.get("ipv6_enabled", False)))
        self.chk_ipv6.toggled.connect(self._toggle_ipv6)
        self.row(self.chk_autostart, self.chk_ipv6)

        # Scheduled hotspot toggle and time inputs
        self.chk_schedule = QCheckBox("Scheduled")
        self.chk_schedule.setChecked(bool(self.controller.config.get("schedule_enabled", False)))
        self.chk_schedule.toggled.connect(self._toggle_schedule)

        self.time_sched_on = QTimeEdit()
        self.time_sched_on.setDisplayFormat("HH:mm")
        t_on = QTime.fromString(str(self.controller.config.get("schedule_on", "08:00")), "HH:mm")
        self.time_sched_on.setTime(t_on if t_on.isValid() else QTime(8, 0))
        self.time_sched_on.timeChanged.connect(self._time_sched_changed)

        self.time_sched_off = QTimeEdit()
        self.time_sched_off.setDisplayFormat("HH:mm")
        t_off = QTime.fromString(str(self.controller.config.get("schedule_off", "22:00")), "HH:mm")
        self.time_sched_off.setTime(t_off if t_off.isValid() else QTime(22, 0))
        self.time_sched_off.timeChanged.connect(self._time_sched_changed)

        self.row(self.chk_schedule, QLabel("On:"), self.time_sched_on, QLabel("Off:"), self.time_sched_off)

        # Battery saver toggle and threshold
        self.chk_battery = QCheckBox("Battery saver")
        self.chk_battery.setChecked(bool(self.controller.config.get("battery_saver_enabled", False)))
        self.chk_battery.toggled.connect(self._toggle_battery_saver)

        self.spin_battery = QSpinBox()
        self.spin_battery.setRange(5, 50)
        self.spin_battery.setSuffix("%")
        try:
            val = int(self.controller.config.get("battery_threshold", 20))
            self.spin_battery.setValue(max(5, min(50, val)))
        except (ValueError, TypeError):
            self.spin_battery.setValue(20)
        self.spin_battery.valueChanged.connect(self._battery_threshold_changed)

        self.row(self.chk_battery, self.spin_battery)

    def _toggle_autostart(self, checked: bool) -> None:
        """Handle autostart checkbox toggle."""
        ok = set_autostart(checked)
        if not ok:
            self.chk_autostart.blockSignals(True)
            self.chk_autostart.setChecked(is_autostart_enabled())
            self.chk_autostart.blockSignals(False)

    def _toggle_ipv6(self, checked: bool) -> None:
        """Handle IPv6 checkbox toggle and apply sysctl if hotspot is active."""
        self.controller.config["ipv6_enabled"] = checked
        save_app_config(self.controller.config)
        if self.controller.detect_state() == "on":
            val = "0" if checked else "1"
            rc, out = priv(["sysctl", "-w", f"net.ipv6.conf.{AP_IF}.disable_ipv6={val}"])
            if rc == 0:
                logger.info("IPv6 sysctl disable_ipv6=%s applied on %s", val, AP_IF)
            else:
                logger.warning("Failed applying IPv6 sysctl on %s: %s", AP_IF, out)

    def _toggle_schedule(self, checked: bool) -> None:
        """Handle schedule checkbox toggle and persist to config."""
        self.controller.config["schedule_enabled"] = checked
        save_app_config(self.controller.config)

    def _time_sched_changed(self) -> None:
        """Handle schedule time changes and persist to config."""
        self.controller.config["schedule_on"] = self.time_sched_on.time().toString("HH:mm")
        self.controller.config["schedule_off"] = self.time_sched_off.time().toString("HH:mm")
        save_app_config(self.controller.config)

    def _toggle_battery_saver(self, checked: bool) -> None:
        """Handle battery saver checkbox toggle and persist to config."""
        self.controller.config["battery_saver_enabled"] = checked
        save_app_config(self.controller.config)

    def _battery_threshold_changed(self, value: int) -> None:
        """Handle battery threshold spinbox value change and persist to config."""
        self.controller.config["battery_threshold"] = value
        save_app_config(self.controller.config)

    def refresh(self) -> None:
        """Update channel-sync service status, VPN bypass state, channels, autostart, and health."""
        _, out = run_rc(["systemctl", "is-active",
                         "hotspot-channelsync.service"], timeout=10, max_retries=1)
        state = out.strip().splitlines()[0] if out else "unknown"
        sta, ap = iface_info(STA_IF), iface_info(AP_IF)
        bypass = "on" if vpn_bypass_on() else "OFF"
        mf_enabled = self.controller.config.get("mac_filter_enabled", False)
        mf_mode = self.controller.config.get("mac_filter_mode", "allow")
        mf_status = (
            f"Active ({mf_mode})" if mf_enabled else "OFF"
        ) + f" ({len(self.controller.allowed_macs)} allowed, {len(self.controller.blocked_macs)} blocked)"

        lines = [f"channel-sync: {state} | VPN bypass: {bypass}"]
        if DISABLED_FLAG.exists():
            lines.append("Hotspot: manually stopped")
        lines.append(f"MAC filter: {mf_status}")
        lines.append(f"wlan0 ch{sta.get('channel', '?')} | ap0 ch{ap.get('channel', '?')}")
        self.info.setText("\n".join(lines))
        self.chk_autostart.blockSignals(True)
        self.chk_autostart.setChecked(is_autostart_enabled())
        self.chk_autostart.blockSignals(False)

        # Sync IPv6
        self.chk_ipv6.blockSignals(True)
        self.chk_ipv6.setEnabled(detect_ipv6_support())
        self.chk_ipv6.setChecked(bool(self.controller.config.get("ipv6_enabled", False)))
        self.chk_ipv6.blockSignals(False)

        # Sync schedule
        self.chk_schedule.blockSignals(True)
        self.time_sched_on.blockSignals(True)
        self.time_sched_off.blockSignals(True)
        self.chk_schedule.setChecked(bool(self.controller.config.get("schedule_enabled", False)))
        t_on = QTime.fromString(str(self.controller.config.get("schedule_on", "08:00")), "HH:mm")
        if t_on.isValid():
            self.time_sched_on.setTime(t_on)
        t_off = QTime.fromString(str(self.controller.config.get("schedule_off", "22:00")), "HH:mm")
        if t_off.isValid():
            self.time_sched_off.setTime(t_off)
        self.chk_schedule.blockSignals(False)
        self.time_sched_on.blockSignals(False)
        self.time_sched_off.blockSignals(False)

        # Sync battery saver
        self.chk_battery.blockSignals(True)
        self.spin_battery.blockSignals(True)
        self.chk_battery.setChecked(bool(self.controller.config.get("battery_saver_enabled", False)))
        try:
            b_val = int(self.controller.config.get("battery_threshold", 20))
            self.spin_battery.setValue(max(5, min(50, b_val)))
        except (ValueError, TypeError):
            self.spin_battery.setValue(20)
        self.chk_battery.blockSignals(False)
        self.spin_battery.blockSignals(False)

        health = self.controller.last_health
        if health:
            self.health_info.setText(f"<b>Health:</b> {health.get('summary', 'unknown')}")
        else:
            self.health_info.setText("<b>Health:</b> checking…")


class ClientsDialog(QDialog):
    """Dialog displaying connected Wi-Fi clients with kick capability."""

    def __init__(self, controller: HotspotPanel, parent: QWidget | None = None) -> None:
        """Initialize the client list dialog."""
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle(f"Connected Clients ({AP_IF})")
        self.setMinimumWidth(440)
        self.setStyleSheet(get_theme_stylesheet(self.palette()))

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        self.summary_label = QLabel("…")
        self.summary_label.setProperty("metricLabel", True)
        self.summary_label.setFont(get_monospace_font(pixel_size=12))
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
        self.controller.invalidate_cache()
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
                info.setProperty("metricLabel", True)
                info.setFont(get_monospace_font(pixel_size=12))

                # Allow button
                btn_allow = QPushButton("+ Allow")
                btn_allow.setFixedWidth(75)
                if mac.lower() in self.controller.allowed_macs:
                    btn_allow.setText("Allowed")
                    btn_allow.setEnabled(False)
                else:
                    btn_allow.clicked.connect(lambda _, m=mac: self._allow(m))

                btn_kick = QPushButton("Disconnect")
                btn_kick.setFixedWidth(90)
                btn_kick.clicked.connect(lambda _, m=mac: self._kick(m))

                row_layout.addWidget(info, 1)
                row_layout.addWidget(btn_allow)
                row_layout.addWidget(btn_kick)
                self.clients_layout.addWidget(row)

        self.clients_layout.addStretch()

    def _allow(self, mac: str) -> None:
        """Add MAC address to allowed list and refresh client list."""
        self.controller.add_allowed_mac(mac)
        self.refresh_clients()

    def _kick(self, mac: str) -> None:
        """Disconnect specified station MAC and refresh the view."""
        self.controller.disconnect_client(mac)
        self.refresh_clients()


class MacFilterDialog(QDialog):
    """Dialog for configuring MAC address filtering (allow/deny lists)."""

    def __init__(self, controller: HotspotPanel, parent: QWidget | None = None) -> None:
        """Initialize MAC filter dialog."""
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("MAC Address Filter")
        self.setMinimumWidth(460)
        self.setMinimumHeight(440)
        self.setStyleSheet(get_theme_stylesheet(self.palette()))

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 14)

        # Policy Controls
        self.chk_enable = QCheckBox("Enable MAC Filtering")
        self.chk_enable.setChecked(self.controller.config.get("mac_filter_enabled", False))
        layout.addWidget(self.chk_enable)

        mode_box = QGroupBox("Filter Mode")
        mode_layout = QHBoxLayout(mode_box)
        self.radio_allow = QRadioButton("Allowlist (Only allowed MACs)")
        self.radio_deny = QRadioButton("Denylist (Block listed MACs)")
        mode = self.controller.config.get("mac_filter_mode", "allow")
        if mode == "deny":
            self.radio_deny.setChecked(True)
        else:
            self.radio_allow.setChecked(True)
        mode_layout.addWidget(self.radio_allow)
        mode_layout.addWidget(self.radio_deny)
        layout.addWidget(mode_box)

        # Tabs for Allowed and Blocked lists
        self.tabs = QTabWidget()

        # Tab 1: Allowed MACs (allowed_macs.txt)
        tab_allow = QWidget()
        lay_allow = QVBoxLayout(tab_allow)
        lay_allow.setContentsMargins(8, 8, 8, 8)
        self.list_allowed = QListWidget()
        self.list_allowed.setFont(get_monospace_font(pixel_size=12))
        self.list_allowed.setProperty("metricLabel", True)
        for mac in sorted(self.controller.allowed_macs):
            self.list_allowed.addItem(mac)
        lay_allow.addWidget(self.list_allowed)

        add_allow_row = QHBoxLayout()
        self.input_allow = QLineEdit()
        self.input_allow.setFont(get_monospace_font(pixel_size=12))
        self.input_allow.setPlaceholderText("00:11:22:33:44:55")
        btn_add_allow = QPushButton("Add MAC")
        btn_add_allow.clicked.connect(self._add_allowed_mac)
        add_allow_row.addWidget(self.input_allow, 1)
        add_allow_row.addWidget(btn_add_allow)
        lay_allow.addLayout(add_allow_row)

        btn_allow_row = QHBoxLayout()
        btn_add_client = QPushButton("Add Connected Client…")
        btn_add_client.clicked.connect(self._add_connected_to_allow)
        btn_del_allow = QPushButton("Remove Selected")
        btn_del_allow.clicked.connect(self._remove_selected_allowed)
        btn_allow_row.addWidget(btn_add_client)
        btn_allow_row.addStretch()
        btn_allow_row.addWidget(btn_del_allow)
        lay_allow.addLayout(btn_allow_row)
        self.tabs.addTab(tab_allow, f"Allowed MACs ({ALLOWED_MACS_FILE.name})")

        # Tab 2: Blocked MACs (blocked_macs.txt)
        tab_deny = QWidget()
        lay_deny = QVBoxLayout(tab_deny)
        lay_deny.setContentsMargins(8, 8, 8, 8)
        self.list_blocked = QListWidget()
        self.list_blocked.setFont(get_monospace_font(pixel_size=12))
        self.list_blocked.setProperty("metricLabel", True)
        for mac in sorted(self.controller.blocked_macs):
            self.list_blocked.addItem(mac)
        lay_deny.addWidget(self.list_blocked)

        add_deny_row = QHBoxLayout()
        self.input_deny = QLineEdit()
        self.input_deny.setFont(get_monospace_font(pixel_size=12))
        self.input_deny.setPlaceholderText("00:11:22:33:44:55")
        btn_add_deny = QPushButton("Add MAC")
        btn_add_deny.clicked.connect(self._add_blocked_mac)
        add_deny_row.addWidget(self.input_deny, 1)
        add_deny_row.addWidget(btn_add_deny)
        lay_deny.addLayout(add_deny_row)

        btn_deny_row = QHBoxLayout()
        btn_del_deny = QPushButton("Remove Selected")
        btn_del_deny.clicked.connect(self._remove_selected_blocked)
        btn_deny_row.addStretch()
        btn_deny_row.addWidget(btn_del_deny)
        lay_deny.addLayout(btn_deny_row)
        self.tabs.addTab(tab_deny, f"Blocked MACs ({BLOCKED_MACS_FILE.name})")

        layout.addWidget(self.tabs, 1)

        # Bottom buttons
        bottom = QHBoxLayout()
        btn_save = QPushButton("Save & Apply")
        btn_save.clicked.connect(self._save_and_apply)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        bottom.addStretch()
        bottom.addWidget(btn_save)
        bottom.addWidget(btn_cancel)
        layout.addLayout(bottom)

    def _normalize_mac(self, text: str) -> str:
        """Validate and return normalized lowercase colon-separated MAC, or '' if invalid."""
        cleaned = text.strip().lower().replace("-", ":")
        if re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", cleaned):
            return cleaned
        return ""

    def _add_allowed_mac(self) -> None:
        """Add MAC from input field into allowed list."""
        mac = self._normalize_mac(self.input_allow.text())
        if not mac:
            QMessageBox.warning(self, "Invalid MAC", "Please enter a valid MAC address (e.g. 00:11:22:33:44:55).")
            return
        existing = [self.list_allowed.item(i).text() for i in range(self.list_allowed.count())]
        if mac not in existing:
            self.list_allowed.addItem(mac)
        self.input_allow.clear()

    def _add_connected_to_allow(self) -> None:
        """Add connected clients to allowed list."""
        stations = station_dump()
        if not stations:
            QMessageBox.information(self, "Connected Clients", "No clients currently connected.")
            return
        existing = [self.list_allowed.item(i).text() for i in range(self.list_allowed.count())]
        added = 0
        for mac, _ in stations:
            norm = mac.lower().replace("-", ":")
            if norm not in existing:
                self.list_allowed.addItem(norm)
                existing.append(norm)
                added += 1
        QMessageBox.information(self, "Connected Clients", f"Added {added} connected station(s) to allowed list.")

    def _remove_selected_allowed(self) -> None:
        """Remove selected items from allowed list."""
        for item in self.list_allowed.selectedItems():
            self.list_allowed.takeItem(self.list_allowed.row(item))

    def _add_blocked_mac(self) -> None:
        """Add MAC from input field into blocked list."""
        mac = self._normalize_mac(self.input_deny.text())
        if not mac:
            QMessageBox.warning(self, "Invalid MAC", "Please enter a valid MAC address (e.g. 00:11:22:33:44:55).")
            return
        existing = [self.list_blocked.item(i).text() for i in range(self.list_blocked.count())]
        if mac not in existing:
            self.list_blocked.addItem(mac)
        self.input_deny.clear()

    def _remove_selected_blocked(self) -> None:
        """Remove selected items from blocked list."""
        for item in self.list_blocked.selectedItems():
            self.list_blocked.takeItem(self.list_blocked.row(item))

    def _save_and_apply(self) -> None:
        """Persist lists and configuration, then enforce filtering policy."""
        allowed = {self.list_allowed.item(i).text().lower() for i in range(self.list_allowed.count())}
        blocked = {self.list_blocked.item(i).text().lower() for i in range(self.list_blocked.count())}

        self.controller.allowed_macs = allowed
        self.controller.blocked_macs = blocked
        save_mac_file(ALLOWED_MACS_FILE, allowed)
        save_mac_file(BLOCKED_MACS_FILE, blocked)

        self.controller.config["mac_filter_enabled"] = self.chk_enable.isChecked()
        self.controller.config["mac_filter_mode"] = "deny" if self.radio_deny.isChecked() else "allow"
        save_app_config(self.controller.config)

        kicked = self.controller.enforce_mac_filter()
        msg = f"MAC filter configuration saved.\n{len(allowed)} allowed, {len(blocked)} blocked."
        if kicked > 0:
            msg += f"\nDisconnected {kicked} unauthorized station(s)."
        QMessageBox.information(self, "MAC Filter Saved", msg)
        self.accept()


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
        self.setObjectName("dropdownCanvas")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(380)
        self.setMinimumHeight(320)
        # Note: Maximum width constraint removed to allow window resizing

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        top = QHBoxLayout()
        top.setContentsMargins(0, 4, 0, 8)
        top.setSpacing(10)
        header = QLabel("<b>Hotspot Hub</b> — dropdown canvas")
        header.setProperty("headingLabel", True)
        header.setFont(get_app_font(pixel_size=16, bold=True))
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedSize(36, 36)
        close_btn.clicked.connect(self.hide)
        top.addWidget(header, 1)
        top.addWidget(close_btn)
        root.addLayout(top)

        self.sections: list[PanelSection] = []

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;} QScrollArea>QWidget>QWidget{background:transparent;}")

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(10)

        self._build_sections(container_layout)

        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        # Bottom row with hint label and resize grip at bottom-right
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("Right-click tray for menu • Quit there. "
                      "Add future mods as new PanelSection.")
        hint.setWordWrap(True)
        hint.setEnabled(False)
        self.size_grip = QSizeGrip(self)
        self.size_grip.setToolTip("Resize panel")
        bottom_bar.addWidget(hint, 1)
        bottom_bar.addWidget(self.size_grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        root.addLayout(bottom_bar)

        self.restore_settings()
        self._applying_theme: bool = False
        self._in_change_event: bool = False
        self._last_applied_theme: str | None = None
        self.apply_theme()

    def save_settings(self) -> None:
        """Save window geometry to QSettings."""
        settings = QSettings("hotspot-hub", "hotspot-hub")
        settings.setValue("panel_geometry", self.saveGeometry())

    def restore_settings(self) -> None:
        """Restore window geometry from QSettings."""
        settings = QSettings("hotspot-hub", "hotspot-hub")
        geo = settings.value("panel_geometry")
        if geo is not None:
            self.restoreGeometry(geo)

    def apply_theme(self) -> None:
        """Apply dynamic stylesheet adhering to palette and dark mode.

        Guards against recursive re-application and no-op redundant style changes.
        """
        if self._applying_theme:
            return
        self._applying_theme = True
        try:
            new_theme = get_theme_stylesheet(self.palette())
            if self._last_applied_theme != new_theme:
                self._last_applied_theme = new_theme
                self.setStyleSheet(new_theme)
        finally:
            self._applying_theme = False

    def changeEvent(self, event: QEvent) -> None:
        """Re-apply styling when system theme or palette changes with recursion guard.

        Args:
            event: Qt event passed to the widget.
        """
        if self._in_change_event or self._applying_theme:
            super().changeEvent(event)
            return
        self._in_change_event = True
        try:
            if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
                self.apply_theme()
            super().changeEvent(event)
        finally:
            self._in_change_event = False

    def _build_sections(self, container_layout: QVBoxLayout) -> None:
        """Instantiate and register panel section cards into the layout.

        Args:
            container_layout: Layout of the scroll area container widget.
        """
        # EXTENSION POINT: append future PanelSection instances here.
        for cls in (HotspotSection, WarpSection, BandwidthSection, SystemSection):
            sec = cls(self.controller, self)
            self.sections.append(sec)
            container_layout.addWidget(sec)

        # Absorb extra vertical space so cards keep their natural height
        # instead of stretching with large gaps when the window is tall.
        container_layout.addStretch(1)

    def refresh_all(self) -> None:
        """Refresh all registered panel sections."""
        for s in self.sections:
            try:
                s.refresh()
            except Exception:
                pass

    def refresh_bandwidth(self) -> None:
        """Refresh only the bandwidth section on periodic background timer."""
        for s in self.sections:
            if isinstance(s, BandwidthSection):
                try:
                    s.refresh()
                except Exception:
                    pass

    def showEvent(self, event: Any) -> None:
        """Resume refresh timer and update sections when panel is displayed."""
        super().showEvent(event)
        self.controller.on_panel_shown()

    def hideEvent(self, event: Any) -> None:
        """Pause refresh timer and save geometry when panel is hidden."""
        self.save_settings()
        super().hideEvent(event)
        self.controller.on_panel_hidden()

    def resizeEvent(self, event: Any) -> None:
        """Save geometry when resized."""
        super().resizeEvent(event)
        self.save_settings()

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
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        # Font integration: set application font to Manrope (primary) with fallbacks
        init_application_fonts(self.app)
        try:  # silence "Icon theme gnome not found" on KDE; prefer breeze
            if QIcon.themeName() in ("", "gnome"):
                QIcon.setThemeName("breeze")
        except Exception:
            pass
        if not QSystemTrayIcon.isSystemTrayAvailable():
            print("WARNING: no system tray detected", file=sys.stderr)
        self.fallback_icon = QIcon.fromTheme(
            "hotspot-hub",
            QIcon.fromTheme("network-wireless-hotspot",
                            QIcon.fromTheme("network-wireless"))
        )
        self.current_state: str = "off"
        self.state: str = "off"
        self._last_error: str | None = None
        initial_icon = make_status_icon(self.current_state)
        self.tray = QSystemTrayIcon(
            initial_icon if not initial_icon.isNull() else self.fallback_icon,
            self.app
        )

        # Persistent configuration & MAC filtering state
        self.config: dict[str, Any] = load_app_config()
        wpa3_ok = detect_wpa3_support()
        if self.config.get("wpa3_supported") != wpa3_ok:
            self.config["wpa3_supported"] = wpa3_ok
            save_app_config(self.config)

        self._schedule_last_action: str | None = None
        self._battery_action_taken: bool = False

        self.allowed_macs: set[str] = load_mac_file(ALLOWED_MACS_FILE)
        self.blocked_macs: set[str] = load_mac_file(BLOCKED_MACS_FILE)

        self.menu = QMenu()  # right-click fallback menu
        self.tray.setContextMenu(self.menu)
        self.panel = DropdownPanel(self)
        self._build_menu()
        self.tray.activated.connect(self._on_tray_activated)

        # Global keyboard shortcut for toggling hotspot
        self.shortcut = None
        shortcut_key = os.environ.get("HOTSPOT_HUB_SHORTCUT", "Ctrl+Alt+H").strip()
        if shortcut_key and shortcut_key.lower() not in ("none", "disabled", "off", "0"):
            self.shortcut = QShortcut(QKeySequence(shortcut_key), self.panel)
            self.shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            self.shortcut.activated.connect(self.toggle_hotspot)

        # Main refresh timer running on 30s (30000ms) interval for status updates,
        # scheduled hotspot checks, and battery-aware auto-disable.
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(30000)

        self._known_macs: set[str] = {mac.lower() for mac, _ in station_dump()}
        self.client_timer = QTimer()  # poll station_dump every 10 s for notifications
        self.client_timer.timeout.connect(self._poll_stations)
        self.client_timer.start(10000)

        # Health check timer: run on startup and every 60s
        self.last_health: dict[str, Any] = {}
        self.health_timer = QTimer()
        self.health_timer.timeout.connect(self._run_health_check)
        self.health_timer.start(60000)
        self._run_health_check()

        self.refresh()
        self.tray.show()

    @property
    def ap0_available(self) -> bool:
        """Return True if AP interface (ap0) exists and is accessible."""
        return Path(f"/sys/class/net/{AP_IF}").exists() or bool(iface_info(AP_IF))

    def invalidate_cache(self) -> None:
        """Invalidate subprocess command caches."""
        invalidate_cache()

    def show_ap0_error_dialog(self, error_msg: str = "") -> None:
        """Show a user-friendly error dialog with troubleshooting steps when ap0 is unavailable."""
        parent = self.panel if self.panel.isVisible() else None
        dlg = QMessageBox(parent)
        dlg.setIcon(QMessageBox.Icon.Critical)
        dlg.setWindowTitle("Hotspot Hub — AP Interface Error")
        dlg.setText(f"<h3>Unable to create or access AP interface ({AP_IF})</h3>")

        details = (
            f"<p><b>Error Details:</b><br><code>{error_msg or 'Interface not found'}</code></p>"
            "<p><b>Troubleshooting Steps:</b></p>"
            "<ol>"
            "<li><b>Check AP support:</b> Verify your Wi-Fi card supports concurrent AP mode:<br>"
            "&nbsp;&nbsp;<code>iw list | grep -A 10 'valid interface combinations'</code></li>"
            "<li><b>Check permissions:</b> Ensure sudoers file is installed for passwordless interface creation:<br>"
            "&nbsp;&nbsp;<code>sudo -n iw phy phy0 interface add ap0 type __ap</code></li>"
            "<li><b>Unblock Wi-Fi:</b> Ensure wireless is not software/hardware blocked:<br>"
            "&nbsp;&nbsp;<code>rfkill list</code></li>"
            "<li><b>Manual interface creation:</b> Try adding interface manually in terminal:<br>"
            "&nbsp;&nbsp;<code>sudo iw phy $(ls /sys/class/ieee80211 | head -n1) interface add ap0 type __ap</code><br>"
            "&nbsp;&nbsp;<code>sudo ip link set ap0 up</code></li>"
            "<li><b>Documentation:</b> Refer to project troubleshooting guide at:<br>"
            f"&nbsp;&nbsp;<code>{DOC_PATH}</code></li>"
            "</ol>"
        )
        dlg.setInformativeText(details)
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
        dlg.exec()

    def on_panel_shown(self) -> None:
        """Resume main refresh timer and refresh panel when opened."""
        if not self.timer.isActive():
            self.timer.start(30000)
        self.refresh()

    def on_panel_hidden(self) -> None:
        """Handle dropdown panel hidden event."""
        pass

    def check_schedule(self) -> None:
        """Check and enforce scheduled hotspot on/off times.

        If schedule_enabled is True, parses HH:MM strings and compares with datetime.now()
        hour/minute. Triggers hotspot_on() or hotspot_off() once per matching minute.
        """
        if not self.config.get("schedule_enabled", False):
            self._schedule_last_action = None
            return

        now = datetime.now()
        sched_on = str(self.config.get("schedule_on", "08:00")).strip()
        sched_off = str(self.config.get("schedule_off", "22:00")).strip()

        try:
            on_h, on_m = map(int, sched_on.split(":"))
            matches_on = (now.hour == on_h and now.minute == on_m)
        except Exception:
            matches_on = False

        try:
            off_h, off_m = map(int, sched_off.split(":"))
            matches_off = (now.hour == off_h and now.minute == off_m)
        except Exception:
            matches_off = False

        if matches_on:
            if self._schedule_last_action != "on":
                self._schedule_last_action = "on"
                logger.info("Schedule triggered: turning hotspot ON (%02d:%02d)", now.hour, now.minute)
                self.hotspot_on()
        elif matches_off:
            if self._schedule_last_action != "off":
                self._schedule_last_action = "off"
                logger.info("Schedule triggered: turning hotspot OFF (%02d:%02d)", now.hour, now.minute)
                self.hotspot_off()
        else:
            self._schedule_last_action = None

    def check_battery(self) -> None:
        """Check battery level and auto-disable hotspot if battery falls to or below threshold.

        Tracks _battery_action_taken with hysteresis: resets only when battery recovers
        above threshold + 5%.
        """
        if not self.config.get("battery_saver_enabled", False):
            self._battery_action_taken = False
            return

        percent = read_battery_percent()
        if percent is None:
            return

        try:
            threshold = int(self.config.get("battery_threshold", 20))
        except (ValueError, TypeError):
            threshold = 20

        # Hysteresis: clear action taken when battery recovers above threshold + 5
        if percent > threshold + 5:
            self._battery_action_taken = False

        if percent <= threshold:
            if not self._battery_action_taken and self.detect_state() == "on":
                self._battery_action_taken = True
                logger.warning(
                    "Battery level (%d%%) is at or below threshold (%d%%). Auto-disabling hotspot.",
                    percent, threshold
                )
                self.hotspot_off()
                self.notify(
                    "Battery saver",
                    f"Hotspot disabled to conserve battery ({percent}% <= {threshold}%)."
                )

    def _run_health_check(self) -> None:
        """Execute periodic health check, log results, and update system UI."""
        try:
            self.last_health = health_check()
            logger.info("Health check: %s", self.last_health.get("summary", "unknown"))
        except Exception as e:
            logger.error("Health check error: %s", e, exc_info=True)
            self.last_health = {"healthy": False, "summary": f"Health check error: {e}"}
        if self.panel.isVisible():
            self.panel.refresh_all()

    def toggle_hotspot(self) -> None:
        """Toggle hotspot state between on and off."""
        if self.detect_state() == "on":
            self.hotspot_off()
        else:
            self.hotspot_on()

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
        self.invalidate_cache()
        pos = QCursor.pos()
        w = self.panel.width() if self.panel.width() >= 340 else self.panel.sizeHint().width()
        h = self.panel.height() if self.panel.height() >= 240 else self.panel.sizeHint().height()
        self.panel.move(max(10, pos.x() - w // 2), max(10, pos.y() - h - 20))
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def detect_state(self) -> str:
        """Detect current hotspot state: 'on' (active), 'off' (inactive), or 'error'."""
        try:
            # Red: error if ap0 is missing
            ap0_exists = Path(f"/sys/class/net/{AP_IF}").exists() or bool(iface_info(AP_IF))
            if not ap0_exists:
                return "error"

            # Red: error if nmcli failed
            rc, out = run_rc(["nmcli", "-t", "-f", "NAME,DEVICE", "con", "show", "--active"], timeout=5, max_retries=1)
            if rc != 0:
                return "error"

            active_map: dict[str, str] = {}
            for line in out.splitlines():
                if ":" in line:
                    n, _, d = line.rpartition(":")
                    active_map[n.strip()] = d.strip()

            # Green: hotspot active (Hotspot-Shared on ap0)
            if active_map.get(HOTSPOT_CON) == AP_IF:
                self._last_error = None
                return "on"

            # Red: error if last command failed
            if self._last_error:
                return "error"

            # Grey: hotspot inactive
            return "off"
        except Exception:
            return "error"

    def _update_tray_icon(self) -> None:
        """Update tray icon based on current hotspot state, with fallback."""
        try:
            icon = make_status_icon(self.current_state)
            if icon.isNull():
                icon = self.fallback_icon
            self.tray.setIcon(icon)
        except Exception:
            self.tray.setIcon(self.fallback_icon)

    def refresh(self) -> None:
        """Refresh tray tooltip, status indicator icon, and visible panel sections."""
        status_err = ""
        try:
            sec = get_hotspot_security()
            sta, ap, n = hotspot_state()
            text = f"{sta} | {ap} [{sec}] | clients {n} | WARP {warp_status()}"
        except Exception as e:
            status_err = str(e)
            text = f"Status error: {e}"

        if status_err:
            self.current_state = "error"
        else:
            self.current_state = self.detect_state()
        self.state = self.current_state

        self._update_tray_icon()
        self.status_action.setText(text[:160])
        self.tray.setToolTip("Hotspot Hub\n" + text)

        # Auto-control checks: scheduled hotspot and battery-aware auto-disable
        self.check_schedule()
        self.check_battery()

        # Lazy refresh: Only update panel sections when panel is visible
        if self.panel.isVisible():
            self.panel.refresh_bandwidth()
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
        self.invalidate_cache()
        logger.info("Hotspot On requested")
        ok, msg = hotspot_start()
        if ok:
            self._last_error = None
            logger.info("Hotspot On succeeded: %s", msg)
            if "ipv6_enabled" in self.config:
                val = "0" if self.config.get("ipv6_enabled", False) else "1"
                priv(["sysctl", "-w", f"net.ipv6.conf.{AP_IF}.disable_ipv6={val}"])
            self.notify("Hotspot On", f"{HOTSPOT_CON} activated on {AP_IF}.")
        else:
            self._last_error = msg
            logger.error("Hotspot On failed: %s", msg)
            self.notify("Hotspot On failed", msg)
            if not self.ap0_available:
                self.show_ap0_error_dialog(msg)
        self.refresh()

    def hotspot_off(self) -> None:
        """Deactivate hotspot connection."""
        self.invalidate_cache()
        logger.info("Hotspot Off requested")
        ok, msg = hotspot_stop()
        if ok:
            self._last_error = None
            logger.info("Hotspot Off succeeded: %s", msg)
            self.notify("Hotspot Off", f"{HOTSPOT_CON} deactivated.")
        else:
            self._last_error = msg
            logger.error("Hotspot Off failed: %s", msg)
            self.notify("Hotspot Off", msg)
        self.refresh()

    def hotspot_restart(self) -> None:
        """Cycle hotspot connection off and on."""
        self.invalidate_cache()
        logger.info("Hotspot Restart requested")
        ok, msg = hotspot_cycle()
        if ok:
            self._last_error = None
            logger.info("Hotspot Restart succeeded: %s", msg)
            self.notify("Hotspot Restart", f"{HOTSPOT_CON} restarted.")
        else:
            self._last_error = msg
            logger.error("Hotspot Restart failed: %s", msg)
            self.notify("Hotspot Restart", f"Restart failed: {msg}")
            if not self.ap0_available:
                self.show_ap0_error_dialog(msg)
        self.refresh()

    def _poll_stations(self) -> None:
        """Poll station_dump() every 10s, enforce MAC filter, and notify on client connect or disconnect."""
        try:
            current_stations = station_dump()
            current_macs = {mac.lower() for mac, _ in current_stations}
        except Exception:
            return

        connected = current_macs - self._known_macs
        disconnected = self._known_macs - current_macs

        for mac in sorted(connected):
            logger.info("Client connected to hotspot: %s", mac)
            if not self.is_mac_authorized(mac):
                logger.warning("Unauthorized client %s connected! Kicking per MAC filter policy.", mac)
                self.disconnect_client(mac)
                self.tray.showMessage("Blocked Unauthorized Client",
                                      f"Blocked client {mac} by MAC filter policy",
                                      QSystemTrayIcon.MessageIcon.Warning, 5000)
                continue
            self.tray.showMessage("Client Connected",
                                  f"Client {mac} connected to hotspot",
                                  QSystemTrayIcon.MessageIcon.Information, 5000)

        for mac in sorted(disconnected):
            logger.info("Client disconnected from hotspot: %s", mac)
            self.tray.showMessage("Client Disconnected",
                                  f"Client {mac} disconnected from hotspot",
                                  QSystemTrayIcon.MessageIcon.Information, 5000)

        self._known_macs = {mac.lower() for mac, _ in station_dump()}

    def is_mac_authorized(self, mac: str) -> bool:
        """Check if MAC is authorized under current filtering policy."""
        if not self.config.get("mac_filter_enabled", False):
            return True
        mac_lower = mac.lower().replace("-", ":")
        mode = self.config.get("mac_filter_mode", "allow")
        if mode == "deny":
            return mac_lower not in self.blocked_macs
        return mac_lower in self.allowed_macs

    def enforce_mac_filter(self) -> int:
        """Check all currently connected stations and disconnect unauthorized ones using iw station del."""
        if not self.config.get("mac_filter_enabled", False):
            return 0
        stations = station_dump()
        kicked_count = 0
        for mac, _ in stations:
            if not self.is_mac_authorized(mac):
                logger.warning("Enforcing MAC filter: kicking unauthorized station %s", mac)
                self.disconnect_client(mac)
                kicked_count += 1
        return kicked_count

    def add_allowed_mac(self, mac: str) -> bool:
        """Add MAC address to allowed list and persist to ~/.config/hotspot-hub/allowed_macs.txt."""
        cleaned = mac.strip().lower().replace("-", ":")
        if not re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", cleaned):
            return False
        self.allowed_macs.add(cleaned)
        save_mac_file(ALLOWED_MACS_FILE, self.allowed_macs)
        logger.info("Added %s to allowed MAC list", cleaned)
        self.notify("MAC Filter", f"Added {cleaned} to allowed list.")
        return True

    def add_blocked_mac(self, mac: str) -> bool:
        """Add MAC address to blocked list and persist to ~/.config/hotspot-hub/blocked_macs.txt."""
        cleaned = mac.strip().lower().replace("-", ":")
        if not re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", cleaned):
            return False
        self.blocked_macs.add(cleaned)
        save_mac_file(BLOCKED_MACS_FILE, self.blocked_macs)
        logger.info("Added %s to blocked MAC list", cleaned)
        self.notify("MAC Filter", f"Added {cleaned} to blocked list.")
        return True

    def show_mac_filter(self) -> None:
        """Open the MAC Filter dialog."""
        dlg = MacFilterDialog(self, self.panel if self.panel.isVisible() else None)
        dlg.exec()

    def set_band(self, band: str) -> tuple[bool, str]:
        """Modify hotspot Wi-Fi band ('2.4GHz', '5GHz', or 'Auto') and persist preference in config."""
        self.invalidate_cache()
        logger.info("Setting hotspot band to %s", band)
        ok, msg = modify_hotspot_band(band)
        if ok:
            self.config["band"] = band
            save_app_config(self.config)
            self.notify("Band Configuration", f"Wi-Fi band updated to {band}.")
            if self.detect_state() == "on":
                self.hotspot_restart()
        else:
            logger.error("Failed to set band to %s: %s", band, msg)
        self.refresh()
        return ok, msg

    def disconnect_client(self, mac: str) -> tuple[int, str]:
        """Disconnect a station via `iw dev ap0 station del <mac>` and refresh."""
        self.invalidate_cache()
        logger.info("Disconnect client requested: %s", mac)
        ok, msg = kick_client(mac)
        if ok:
            logger.info("Client %s disconnected successfully: %s", mac, msg)
            self.notify("Client Disconnected", msg)
            rc, out = 0, msg
        else:
            logger.error("Failed to disconnect client %s: %s", mac, msg)
            self.notify("Disconnect Failed", msg)
            rc, out = 1, msg
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
        dialog.setStyleSheet(get_theme_stylesheet(dialog.palette()))
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
        self.invalidate_cache()
        logger.info("WARP command requested: %s", op)
        rc, out = run_rc(["warp-cli", "--accept-tos", op], timeout=30)
        if rc == 0:
            logger.info("WARP %s succeeded: %s", op, out)
        else:
            logger.error("WARP %s failed: %s", op, out)
        self.notify(f"WARP {op}", (out[-300:] or "done") if rc == 0
                    else (out[-300:] or "warp-cli failed"))
        self.refresh()

    def sync_restart(self) -> None:
        """Restart hotspot-channelsync systemd service."""
        self.invalidate_cache()
        logger.info("Restarting hotspot-channelsync service")
        rc, out = priv(["systemctl", "restart",
                        "hotspot-channelsync.service"])
        if rc == 0:
            logger.info("hotspot-channelsync restarted successfully")
        else:
            logger.error("hotspot-channelsync restart failed: %s", out)
        self.notify("Channel-sync", "Service restarted." if rc == 0
                    else (out[-300:] or "restart failed (sudoers?)"))

    def fix_routing(self) -> None:
        """Run hotspot-channelsync script to enforce VPN bypass routing."""
        self.invalidate_cache()
        logger.info("Running hotspot-channelsync script to fix client routing")
        rc, out = priv(["/usr/local/bin/hotspot-channelsync.sh"])
        if rc == 0:
            logger.info("hotspot-channelsync script completed successfully")
        else:
            logger.error("hotspot-channelsync script failed: %s", out)
        self.notify("Client routing",
                    "VPN bypass enforced (pref 50) + channel synced."
                    if vpn_bypass_on() else "Sync ran; verify bypass below.")

    def run(self) -> None:
        """Run the Qt application event loop."""
        sys.exit(self.app.exec())


if __name__ == "__main__":
    HotspotPanel().run()
