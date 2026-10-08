#!/usr/bin/env bash
# Idempotent hotspot OFF: nmcli down (no sudo needed via polkit). Keeps ap0.
set -u
CON="Hotspot-Shared"
AP_IF="ap0"

if ! nmcli -t -f NAME,DEVICE con show --active | grep -q "^${CON}:"; then
  echo "Hotspot-Shared already inactive."
  exit 0
fi

nmcli con down "$CON" && echo "Hotspot-Shared deactivated."
