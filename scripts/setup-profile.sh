#!/usr/bin/env bash
# setup-profile.sh - create/update the Hotspot-Shared NM profile (no secrets shipped).
# Usage: setup-profile.sh [--ssid NAME] [--password PSK] [--band bg|a|auto] [--wpa3] [--show]
# New installs prompt for SSID/PSK and WPA3 if supported. Existing profiles
# are only retuned unless explicit flags are passed. Never stores PSK outside NM.
set -u
CON="Hotspot-Shared"
AP_IF="ap0"
SSID=""
SSID_GIVEN=0
PSK=""
PSK_GIVEN=0
SHOW=0
BAND_GIVEN=0
BAND_ARG=""
KEY_MGMT=""
SEC_GIVEN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --ssid) SSID="${2:?}"; SSID_GIVEN=1; shift 2;;
    --password) PSK="${2:?}"; PSK_GIVEN=1; shift 2;;
    --band) BAND_ARG="${2:?}"; BAND_GIVEN=1; shift 2;;
    --wpa3) KEY_MGMT="sae"; SEC_GIVEN=1; shift;;
    --wpa2) KEY_MGMT="wpa-psk"; SEC_GIVEN=1; shift;;
    --security)
      case "${2:?}" in
        wpa3|sae|wpa3-sae) KEY_MGMT="sae";;
        *) KEY_MGMT="wpa-psk";;
      esac
      SEC_GIVEN=1; shift 2;;
    --show) SHOW=1; shift;;
    -h|--help) sed -n '1,14p' "$0"; exit 0;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done

if [ "$SHOW" = 1 ]; then
  nmcli con show "$CON" | grep -E "ssid|mode|band|channel|method|key-mgmt|proto|pmf" || exit 1
  exit 0
fi

EXISTS=0
nmcli con show "$CON" >/dev/null 2>&1 && EXISTS=1

# Probe iw phy for SAE (WPA3-Personal) support
SAE_SUPPORTED=0
if iw phy 2>/dev/null | grep -qi "SAE"; then
  SAE_SUPPORTED=1
fi

if [ "$EXISTS" = 0 ] && [ "$SSID_GIVEN" = 0 ]; then
  printf 'SSID [Hotspot-Hub]: '
  read -r SSID_IN || true
  [ -n "${SSID_IN:-}" ] && SSID="$SSID_IN" || SSID="Hotspot-Hub"
elif [ "$SSID_GIVEN" = 0 ]; then
  SSID="$(nmcli -g 802-11-wireless.ssid con show "$CON" 2>/dev/null || echo Hotspot-Hub)"
  [ -n "$SSID" ] || SSID="Hotspot-Hub"
fi

# Detect or prompt for security mode
if [ "$SEC_GIVEN" = 0 ]; then
  if [ "$EXISTS" = 0 ] && [ "$SAE_SUPPORTED" = 1 ]; then
    printf 'WPA3-SAE support detected on Wi-Fi adapter. Enable WPA3-SAE? [y/N]: '
    read -r USE_SAE || true
    case "${USE_SAE:-}" in
      [yY]|[yY][eE][sS]) KEY_MGMT="sae";;
      *) KEY_MGMT="wpa-psk";;
    esac
  elif [ "$EXISTS" = 1 ]; then
    CUR_KEY_MGMT="$(nmcli -g 802-11-wireless-security.key-mgmt con show "$CON" 2>/dev/null || echo wpa-psk)"
    case "$CUR_KEY_MGMT" in
      *sae*) KEY_MGMT="sae";;
      *) KEY_MGMT="wpa-psk";;
    esac
  else
    KEY_MGMT="wpa-psk"
  fi
fi

if [ "$EXISTS" = 0 ] && [ "$PSK_GIVEN" = 0 ]; then
  sec_label="WPA2"
  [ "$KEY_MGMT" = "sae" ] && sec_label="WPA3-SAE"
  printf '%s passphrase (min 8 chars, input hidden): ' "$sec_label"
  stty -echo; read -r PSK; stty echo; echo
  PSK_GIVEN=1
fi
if [ "$PSK_GIVEN" = 1 ] && [ "${#PSK}" -lt 8 ]; then
  echo "Passphrase too short (min 8)." >&2; exit 1
fi

CH="$(iw dev wlan0 info 2>/dev/null | awk '/channel/ {print $2}')"
FREQ="$(iw dev wlan0 info 2>/dev/null | awk '/channel/ {print $3}' | tr -d '()')"

if [ "$BAND_GIVEN" = 1 ]; then
  case "$BAND_ARG" in
    a|5|5ghz|5GHz)
      BAND="a"
      if [ -z "$CH" ] || [ "$CH" -le 14 ]; then CH=36; fi
      ;;
    bg|2.4|2.4ghz|2.4GHz)
      BAND="bg"
      if [ -z "$CH" ] || [ "$CH" -gt 14 ]; then CH=11; fi
      ;;
    *)
      BAND="bg"; [ -n "$FREQ" ] && [ "$FREQ" -ge 3000 ] && BAND="a"
      [ -n "$CH" ] || CH=11
      ;;
  esac
else
  BAND="bg"; [ -n "$FREQ" ] && [ "$FREQ" -ge 3000 ] && BAND="a"
  [ -n "$CH" ] || CH=11
fi

if [ "$KEY_MGMT" = "sae" ]; then
  sec_args=(wifi-sec.key-mgmt sae wifi-sec.proto rsn wifi-sec.pmf required)
else
  sec_args=(wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn
            wifi-sec.pairwise ccmp wifi-sec.group ccmp wifi-sec.pmf optional)
fi

# --- Atomic profile update: backup before modifying, restore on failure ---
PROFILE_BACKUP=""
rollback_profile() {
  if [ -n "$PROFILE_BACKUP" ] && [ -f "$PROFILE_BACKUP" ]; then
    echo "Restoring profile $CON from backup due to failure..." >&2
    nmcli con delete "$CON" >/dev/null 2>&1 || true
    nmcli con import type wifi file "$PROFILE_BACKUP" >/dev/null 2>&1 || true
    rm -f "$PROFILE_BACKUP"
  fi
}
trap rollback_profile EXIT
# Export current profile to a backup file before any modification (if it exists).
if [ "$EXISTS" = 1 ]; then
  PROFILE_BACKUP="/tmp/hotspot-hub-profile-backup-$$.nmconnection"
  nmcli con export "$CON" "$PROFILE_BACKUP" 2>/dev/null || PROFILE_BACKUP=""
fi

if [ "$EXISTS" = 1 ]; then
  args=(connection.interface-name "$AP_IF"
    802-11-wireless.mode ap
    802-11-wireless.band "$BAND" 802-11-wireless.channel "$CH"
    802-11-wireless.cloned-mac-address preserve
    "${sec_args[@]}"
    ipv4.method shared ipv4.addresses 10.42.0.1/24 ipv6.method disabled)
  [ "$SSID_GIVEN" = 1 ] && args+=(802-11-wireless.ssid "$SSID")
  nmcli con modify "$CON" "${args[@]}"
  [ "$PSK_GIVEN" = 1 ] && nmcli con modify "$CON" wifi-sec.psk "$PSK"
else
  echo "Creating profile $CON (SSID=$SSID, Security=$KEY_MGMT)..."
  nmcli con add type wifi ifname "$AP_IF" mode ap con-name "$CON" \
    autoconnect no ssid "$SSID" \
    wifi.band "$BAND" wifi.channel "$CH" \
    wifi-sec.key-mgmt "$KEY_MGMT" wifi-sec.psk "$PSK" \
    ipv4.method shared ipv4.addresses 10.42.0.1/24 ipv6.method disabled
  nmcli con modify "$CON" \
    802-11-wireless.cloned-mac-address preserve \
    "${sec_args[@]}"
fi
# Success: disable rollback trap and clean up backup.
trap - EXIT
[ -n "$PROFILE_BACKUP" ] && [ -f "$PROFILE_BACKUP" ] && rm -f "$PROFILE_BACKUP"
echo "Profile ready: band=$BAND ch=$CH security=$KEY_MGMT (SSID/PSK changed only if given)."
echo "Start with: nmcli con up $CON"
