#!/usr/bin/env bash
# Idempotent hotspot OFF: nmcli down (no sudo needed via polkit). Keeps ap0.
set -u
CON="Hotspot-Shared"
AP_IF="ap0"
DISABLED_FLAG="${DISABLED_FLAG:-/run/hotspot-hub-disabled}"

if ! nmcli -t -f NAME,DEVICE con show --active | grep -q "^${CON}:"; then
  echo "Hotspot-Shared already inactive."
  touch "$DISABLED_FLAG" 2>/dev/null || sudo -n touch "$DISABLED_FLAG" 2>/dev/null || true
  exit 0
fi

if nmcli con down "$CON"; then
  touch "$DISABLED_FLAG" 2>/dev/null || sudo -n touch "$DISABLED_FLAG" 2>/dev/null || true
  echo "Hotspot-Shared deactivated."
fi
