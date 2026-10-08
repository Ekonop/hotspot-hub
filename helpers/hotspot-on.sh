#!/usr/bin/env bash
# Idempotent hotspot ON: ensure ap0 exists (sudo -n via sudoers), then nmcli up.
# No passwords in this file. Safe to run repeatedly.
set -u
AP_IF="ap0"
CON="Hotspot-Shared"

if ! iw dev "$AP_IF" info >/dev/null 2>&1; then
  echo "ap0 missing, creating (passwordless sudo)..."
  sudo -n iw phy phy0 interface add "$AP_IF" type __ap || {
    echo "FAILED: could not create ap0" >&2; exit 1; }
  sudo -n ip link set "$AP_IF" up || true
fi

# Already active? nothing to do.
if nmcli -t -f NAME,DEVICE con show --active | grep -q "^${CON}:${AP_IF}$"; then
  echo "Hotspot-Shared already active on ap0."
  exit 0
fi

nmcli con up "$CON" && echo "Hotspot-Shared activated."
