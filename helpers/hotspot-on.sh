#!/usr/bin/env bash
# Idempotent hotspot ON: ensure ap0 exists (sudo -n via sudoers), then nmcli up.
# No passwords in this file. Safe to run repeatedly.
set -u
AP_IF="ap0"
CON="Hotspot-Shared"

PHY="$(ls /sys/class/ieee80211/ 2>/dev/null | head -1)"
if [ -z "$PHY" ]; then echo "No WiFi PHY found" >&2; exit 1; fi
if ! iw dev "$AP_IF" info >/dev/null 2>&1; then
  echo "ap0 missing, creating on $PHY (passwordless sudo)..."
  sudo -n iw phy "$PHY" interface add "$AP_IF" type __ap || {
    echo "FAILED: could not create ap0" >&2; exit 1; }
  sudo -n ip link set "$AP_IF" up || true
fi

# Already active? nothing to do.
if nmcli -t -f NAME,DEVICE con show --active | grep -q "^${CON}:${AP_IF}$"; then
  echo "Hotspot-Shared already active on ap0."
  exit 0
fi

nmcli con up "$CON" && echo "Hotspot-Shared activated."
