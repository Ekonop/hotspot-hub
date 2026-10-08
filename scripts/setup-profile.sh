#!/usr/bin/env bash
# setup-profile.sh - create/update the Hotspot-Shared NM profile (no secrets shipped).
# Usage: setup-profile.sh [--ssid NAME] [--password PSK] [--show]
# New installs prompt for SSID/PSK. Existing profiles are only retuned
# (interface ap0, WPA2-CCMP, shared IPv4, current STA channel) unless
# --ssid/--password are explicitly given. Never stores PSK outside NM.
set -u
CON="Hotspot-Shared"
AP_IF="ap0"
SSID=""
SSID_GIVEN=0
PSK=""
PSK_GIVEN=0
SHOW=0

while [ $# -gt 0 ]; do
  case "$1" in
    --ssid) SSID="${2:?}"; SSID_GIVEN=1; shift 2;;
    --password) PSK="${2:?}"; PSK_GIVEN=1; shift 2;;
    --show) SHOW=1; shift;;
    -h|--help) sed -n '1,12p' "$0"; exit 0;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done

if [ "$SHOW" = 1 ]; then
  nmcli con show "$CON" | grep -E "ssid|mode|band|channel|method|key-mgmt|proto|pmf" || exit 1
  exit 0
fi

EXISTS=0
nmcli con show "$CON" >/dev/null 2>&1 && EXISTS=1

if [ "$EXISTS" = 0 ] && [ "$SSID_GIVEN" = 0 ]; then
  printf 'SSID [Hotspot-Hub]: '
  read -r SSID_IN || true
  [ -n "${SSID_IN:-}" ] && SSID="$SSID_IN" || SSID="Hotspot-Hub"
elif [ "$SSID_GIVEN" = 0 ]; then
  SSID="$(nmcli -g 802-11-wireless.ssid con show "$CON" 2>/dev/null || echo Hotspot-Hub)"
  [ -n "$SSID" ] || SSID="Hotspot-Hub"
fi

if [ "$EXISTS" = 0 ] && [ "$PSK_GIVEN" = 0 ]; then
  printf 'WPA2 passphrase (min 8 chars, input hidden): '
  stty -echo; read -r PSK; stty echo; echo
  PSK_GIVEN=1
fi
if [ "$PSK_GIVEN" = 1 ] && [ "${#PSK}" -lt 8 ]; then
  echo "Passphrase too short (min 8)." >&2; exit 1
fi

CH="$(iw dev wlan0 info 2>/dev/null | awk '/channel/ {print $2}')"
[ -n "$CH" ] || CH=11
FREQ="$(iw dev wlan0 info 2>/dev/null | awk '/channel/ {print $3}' | tr -d '()')"
BAND="bg"; [ -n "$FREQ" ] && [ "$FREQ" -ge 3000 ] && BAND="a"

if [ "$EXISTS" = 1 ]; then
  echo "Retuning existing profile $CON (SSID kept unless --ssid given)..."
  args=(connection.interface-name "$AP_IF"
    802-11-wireless.mode ap
    802-11-wireless.band "$BAND" 802-11-wireless.channel "$CH"
    802-11-wireless.cloned-mac-address permanent
    wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn
    wifi-sec.pairwise ccmp wifi-sec.group ccmp wifi-sec.pmf optional
    ipv4.method shared ipv4.addresses 10.42.0.1/24 ipv6.method disabled)
  [ "$SSID_GIVEN" = 1 ] && args+=(802-11-wireless.ssid "$SSID")
  nmcli con modify "$CON" "${args[@]}"
  [ "$PSK_GIVEN" = 1 ] && nmcli con modify "$CON" wifi-sec.psk "$PSK"
else
  echo "Creating profile $CON (SSID=$SSID)..."
  nmcli con add type wifi ifname "$AP_IF" mode ap con-name "$CON" \
    autoconnect no ssid "$SSID" \
    wifi.band "$BAND" wifi.channel "$CH" \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK" \
    ipv4.method shared ipv4.addresses 10.42.0.1/24 ipv6.method disabled
  nmcli con modify "$CON" \
    802-11-wireless.cloned-mac-address permanent \
    wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp \
    wifi-sec.pmf optional
fi
echo "Profile ready: band=$BAND ch=$CH (SSID/PSK changed only if given)."
echo "Start with: nmcli con up $CON"
