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

# 0. Keep hotspot clients out of VPN/WARP policy tables.
# WARP installs a capture rule (fwmark 0x100cf -> table 65743) and CHANGES its
# priority between runs (seen: 32765, 999, 99). Any hardcoded priority gets
# outranked, which is why client internet kept dying whenever WARP (re)connects.
# Fix: enforce our bypass at pref 50 (below 0/local and below any VPN rule) and
# re-add it every run, so it is always evaluated first. Both rules are needed:
#  - from <ap-subnet> : policy-routes NATed client traffic into the main table
#  - iif <ap-if>      : covers forwarded frames before any CONNMARK is set
AP_SUBNET="${AP_SUBNET:-10.42.0.0/24}"
BYPASS_PREF="${BYPASS_PREF:-50}"

bypass_ok() {
  ip rule show 2>/dev/null | grep -q "^${BYPASS_PREF}:	from ${AP_SUBNET} lookup main" &&
  ip rule show 2>/dev/null | grep -q "^${BYPASS_PREF}:	from all iif ${AP_IF} lookup main"
}

if command -v ip >/dev/null 2>&1; then
  # drop stale duplicates (any priority) before re-adding
  while ip rule del from "$AP_SUBNET" table main 2>/dev/null; do :; done
  while ip rule del from all iif "$AP_IF" table main 2>/dev/null; do :; done
  ip rule add from "$AP_SUBNET" lookup main pref "$BYPASS_PREF" 2>/dev/null
  ip rule add from all iif "$AP_IF" lookup main pref "$BYPASS_PREF" 2>/dev/null
  if bypass_ok; then
    log "vpn bypass enforced at pref $BYPASS_PREF ($AP_SUBNET + iif $AP_IF -> main)"
  else
    log "WARN: vpn bypass could not be enforced; client traffic may enter a VPN tunnel"
  fi
fi

# 1. ensure ap0 exists
if ! iw dev "$AP_IF" info >/dev/null 2>&1; then
  PHY="$(ls /sys/class/ieee80211/ 2>/dev/null | head -1)"
  log "$AP_IF missing, creating on $PHY"
  iw phy "$PHY" interface add "$AP_IF" type __ap 2>&1 | logger -t "$LOG_TAG"
  ip link set "$AP_IF" up
fi

DISABLED_FLAG="${DISABLED_FLAG:-/run/hotspot-hub-disabled}"
if [ -f "$DISABLED_FLAG" ]; then
  log "Hotspot manually disabled, skipping auto-start"
  exit 0
fi

# re-gate right before activating: the toggle-off path touches the flag just
# before/after `nmcli down`, and a tick that already passed the top check can
# otherwise resurrect a hotspot the user just turned off.
up_hotspot() {
  if [ -f "$DISABLED_FLAG" ]; then
    log "Hotspot manually disabled at up-time, skipping"
    return 1
  fi
  nmcli connection up "$HOTSPOT_CON"
  return $?
}

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
      up_hotspot 2>&1 | logger -t "$LOG_TAG"
    fi
  fi
  exit 0
fi

log "STA moved to ch $CH freq $FREQ ($BAND), hotspot was ch $CUR_CH $CUR_BAND -> updating"
nmcli connection modify "$HOTSPOT_CON" 802-11-wireless.channel "$CH" 802-11-wireless.band "$BAND"
if sta_up; then
  up_hotspot 2>&1 | logger -t "$LOG_TAG"
  log "Hotspot restarted on ch $CH $BAND"
else
  log "$STA_IF not connected, profile updated, AP restart deferred"
fi
