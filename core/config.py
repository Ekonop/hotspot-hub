"""Configuration management for Hotspot Hub."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("hotspot-hub")

CONFIG_DIR = Path.home() / ".config" / "hotspot-hub"
CONFIG_FILE = CONFIG_DIR / "config.json"
ENV_CONFIG_FILE = CONFIG_DIR / "config"
ALLOWED_MACS_FILE = CONFIG_DIR / "allowed_macs.txt"
BLOCKED_MACS_FILE = CONFIG_DIR / "blocked_macs.txt"
DISABLED_FLAG = Path("/run/hotspot-hub-disabled")


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
    "profiles": ["Hotspot-Shared"],
    "active_profile": "Hotspot-Shared",
    "reconnect_enabled": True,
    "reconnect_cooldown_sec": 300,
    "reconnect_backoff_sec": 30,
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



# Env-config loader (reads ~/.config/hotspot-hub/config KEY=VALUE)
def load_env_config() -> None:
    """Load env-style config file into os.environ."""
    try:
        if ENV_CONFIG_FILE.is_file():
            for line in ENV_CONFIG_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass

load_env_config()


def detect_wpa3_support() -> bool:
    """Probe iw phy for SAE (WPA3-Personal) capability."""
    try:
        import subprocess
        result = subprocess.run(["iw", "phy"], capture_output=True, text=True, timeout=5)
        return "SAE" in result.stdout
    except Exception:
        return False
