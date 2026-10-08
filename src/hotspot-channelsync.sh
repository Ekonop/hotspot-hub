#!/bin/bash
# Auto-sync Hotspot-Shared AP channel/band to wlan0 STA channel (single-radio #channels<=1)
# Idempotent, safe to run frequently.
# Universal: STA = any active wifi on $STA_IF except $HOTSPOT_CON (env-overridable).
STA_IF="${STA_IF:-wlan0}"
AP_IF="${AP_IF:-ap0}"
HOTSPOT_CON="${HOTSPOT_CON:-Hotspot-Shared}"
LOG_TAG="hotspot-channelsync"
log() { logger -t "$LOG_TAG" "$*"; echo "$(date -Is) $*" >&2; }
sta_up() { nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | grep -E ":${STA_IF}$" | grep -vq "^${HOTSPOT_CON}:"; }

# 1. ensure ap0 exists
if ! iw dev "$AP_IF" info >/dev/null 2>&1; then
  log "$AP_IF missing, creating"
  iw phy phy0 interface add "$AP_IF" type __ap 2>&1 | logger -t "$LOG_TAG"
  ip link set "$AP_IF" up
fi

# 2. read STA channel/freq
WLAN_INFO=$(iw dev "$STA_IF" info 2>/dev/null)
CH=$(echo "$WLAN_INFO" | awk '/channel/ {print $2}')
FREQ=$(echo "$WLAN_INFO" | awk '/channel/ {print $3}' | tr -d '()')
if [ -z "$CH" ] || [ -z "$FREQ" ]; then
  log "wlan0 not associated yet, skip (no channel)"
  exit 0
fi
if [ "$FREQ" -lt 3000 ]; then BAND="bg"; else BAND="a"; fi

# 3. read current hotspot profile
CUR_CH=$(nmcli -g 802-11-wireless.channel connection show "$HOTSPOT_CON" 2>/dev/null)
CUR_BAND=$(nmcli -g 802-11-wireless.band connection show "$HOTSPOT_CON" 2>/dev/null)
if [ -z "$CUR_CH" ]; then log "$HOTSPOT_CON profile missing, skip"; exit 1; fi

if [ "$CH" = "$CUR_CH" ] && [ "$BAND" = "$CUR_BAND" ]; then
  if ! nmcli -t -f NAME,DEVICE connection show --active | grep -q "^${HOTSPOT_CON}:${AP_IF}$"; then
    if sta_up; then
      log "Hotspot not active, bringing up on ch $CH $BAND"
      nmcli connection up "$HOTSPOT_CON" 2>&1 | logger -t "$LOG_TAG"
    fi
  fi
  exit 0
fi

log "STA moved to ch $CH freq $FREQ ($BAND), hotspot was ch $CUR_CH $CUR_BAND -> updating"
nmcli connection modify "$HOTSPOT_CON" 802-11-wireless.channel "$CH" 802-11-wireless.band "$BAND"
if sta_up; then
  nmcli connection up "$HOTSPOT_CON" 2>&1 | logger -t "$LOG_TAG"
  log "Hotspot restarted on ch $CH $BAND"
else
  log "$STA_IF not connected, profile updated, AP restart deferred"
fi
