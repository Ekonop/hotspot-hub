#!/usr/bin/env bash
# helpers/hotspot-adblock.sh - Hotspot Hub Hybrid AdBlocker & Proxy Shield
# Manages:
#  1. DNS sinkhole via dnsmasq (addn-hosts) on ap0
#  2. Local HTTP proxy via Privoxy on 10.42.0.1:8118
#  3. WPAD / PAC proxy auto-config via DHCP option 252
#  4. Transparent redirection for DNS (port 53) and HTTP (port 80)
#
# Safe for repeated runs. Requires root/sudo.
set -euo pipefail

ADBLOCK_DIR="/etc/hotspot-hub"
HOSTS_FILE="$ADBLOCK_DIR/adblock-hosts.txt"
CUSTOM_FILE="$ADBLOCK_DIR/custom-blocklist.txt"
BACKUP_FILE="$ADBLOCK_DIR/adblock-hosts.bak"
DNSMASQ_CONF="/etc/NetworkManager/dnsmasq-shared.d/50-adblock.conf"
ENABLED_FLAG="$ADBLOCK_DIR/adblock.enabled"
AP_IF="${HOTSPOT_HUB_AP_IF:-ap0}"
PROXY_PORT=8118
WPAD_URL="http://10.42.0.1:9090/wpad.dat"
PRIVOXY_CONF="/etc/privoxy/config"

mkdir -p "$ADBLOCK_DIR"
touch "$CUSTOM_FILE"

reload_dnsmasq() {
  if pkill -HUP -f "dnsmasq.*$AP_IF" 2>/dev/null; then
    return 0
  fi
  if [ -f "/run/nm-dnsmasq-$AP_IF.pid" ]; then
    kill -HUP "$(cat "/run/nm-dnsmasq-$AP_IF.pid")" 2>/dev/null || true
  fi
}

setup_privoxy_conf() {
  if [ -f "$PRIVOXY_CONF" ] && ! grep -q "Hotspot Hub Proxy AdBlocker" "$PRIVOXY_CONF"; then
    cat << "EOF" >> "$PRIVOXY_CONF"

# ========================================================
# Hotspot Hub Proxy AdBlocker Configuration
# ========================================================
listen-address  10.42.0.1:8118
permit-access   10.42.0.0/24
permit-access   127.0.0.1
accept-intercepted-requests 1
enable-remote-toggle 0
# ========================================================
EOF
  fi

  local user_action="/etc/privoxy/user.action"
  if [ -f "$user_action" ] && ! grep -q "Hotspot Hub YouTube & AdBlock Rules" "$user_action"; then
    cat << "EOF" >> "$user_action"

# ========================================================
# Hotspot Hub YouTube & AdBlock Rules
# ========================================================
{ +block{YouTube Ads & Tracking} }
.youtube.com/api/stats/ads
.youtube.com/pagead/
.youtube.com/ptracking
.youtube.com/api/stats/qoe
.youtube.com/api/stats/playback
.youtube.com/youtubei/v1/log_event
.youtube.com/api/stats/watchtime
.youtube.com/pcs/activeview
.googlevideo.com/videoplayback\?.*(&|%26)oad=
.googlevideo.com/videoplayback\?.*(&|%26)ctier=L
.googlevideo.com/initplayback\?.*(&|%26)oad=
s.youtube.com
video-stats.l.google.com
ads.youtube.com
ad.youtube.com
.doubleclick.net
.googleads.g.doubleclick.net
.pubads.g.doubleclick.net
.pagead2.googlesyndication.com
.adservice.google.com
# ========================================================
EOF
  fi
}

apply_iptables_rules() {
  # 1. Intercept port 53 (DNS) to force all client DNS into local dnsmasq sinkhole
  iptables -t nat -C PREROUTING -i "$AP_IF" -p udp --dport 53 -j REDIRECT --to-ports 53 2>/dev/null || \
    iptables -t nat -A PREROUTING -i "$AP_IF" -p udp --dport 53 -j REDIRECT --to-ports 53 2>/dev/null || true
  iptables -t nat -C PREROUTING -i "$AP_IF" -p tcp --dport 53 -j REDIRECT --to-ports 53 2>/dev/null || \
    iptables -t nat -A PREROUTING -i "$AP_IF" -p tcp --dport 53 -j REDIRECT --to-ports 53 2>/dev/null || true

  # 2. Transparent HTTP redirection to Privoxy port 8118
  iptables -t nat -C PREROUTING -i "$AP_IF" -p tcp --dport 80 -j REDIRECT --to-ports "$PROXY_PORT" 2>/dev/null || \
    iptables -t nat -A PREROUTING -i "$AP_IF" -p tcp --dport 80 -j REDIRECT --to-ports "$PROXY_PORT" 2>/dev/null || true

  # 3. Block QUIC (UDP 443) on hotspot clients.
  # Forces iOS YouTube, mobile Safari, and apps to immediately fallback to TCP HTTPS,
  # preventing UDP connection migration, WPAD/proxy evasion, and DoH bypass.
  iptables -C FORWARD -i "$AP_IF" -p udp --dport 443 -j REJECT --reject-with icmp-port-unreachable 2>/dev/null || \
    iptables -I FORWARD 1 -i "$AP_IF" -p udp --dport 443 -j REJECT --reject-with icmp-port-unreachable 2>/dev/null || true

  # 4. Block DNS-over-TLS (TCP 853) so client apps cannot bypass local dnsmasq sinkhole
  iptables -C FORWARD -i "$AP_IF" -p tcp --dport 853 -j REJECT 2>/dev/null || \
    iptables -I FORWARD 1 -i "$AP_IF" -p tcp --dport 853 -j REJECT 2>/dev/null || true
}

remove_iptables_rules() {
  iptables -t nat -D PREROUTING -i "$AP_IF" -p udp --dport 53 -j REDIRECT --to-ports 53 2>/dev/null || true
  iptables -t nat -D PREROUTING -i "$AP_IF" -p tcp --dport 53 -j REDIRECT --to-ports 53 2>/dev/null || true
  iptables -t nat -D PREROUTING -i "$AP_IF" -p tcp --dport 80 -j REDIRECT --to-ports "$PROXY_PORT" 2>/dev/null || true
  iptables -D FORWARD -i "$AP_IF" -p udp --dport 443 -j REJECT --reject-with icmp-port-unreachable 2>/dev/null || true
  iptables -D FORWARD -i "$AP_IF" -p tcp --dport 853 -j REJECT 2>/dev/null || true
}

seed_baseline_blocklist() {
  cat << "EOF" > "$HOSTS_FILE"
# Hotspot Hub Seed Blocklist - High-Priority Ad & Tracker Networks
0.0.0.0 doubleclick.net
0.0.0.0 adservice.google.com
0.0.0.0 googleadservices.com
0.0.0.0 googlesyndication.com
0.0.0.0 pagead2.googlesyndication.com
0.0.0.0 ad.doubleclick.net
0.0.0.0 securepubads.g.doubleclick.net
0.0.0.0 tpc.googlesyndication.com
0.0.0.0 ads.pubmatic.com
0.0.0.0 ib.adnxs.com
0.0.0.0 ads.rubiconproject.com
0.0.0.0 c.amazon-adsystem.com
0.0.0.0 aax.amazon-adsystem.com
0.0.0.0 ad.adroll.com
0.0.0.0 telemetry.app-measurement.com
0.0.0.0 crashlytics.com
0.0.0.0 app-measurement.com
0.0.0.0 graph.facebook.com
0.0.0.0 pixel.facebook.com
0.0.0.0 an.facebook.com
0.0.0.0 ads-twitter.com
0.0.0.0 static.ads-twitter.com
0.0.0.0 analytics.twitter.com
0.0.0.0 telemetry.sdk.spotify.com
0.0.0.0 spclient.wg.spotify.com/ads
0.0.0.0 ad.crwdcntrl.net
0.0.0.0 b.scorecardresearch.com
0.0.0.0 sb.scorecardresearch.com
0.0.0.0 quantserve.com
0.0.0.0 edge.quantserve.com
0.0.0.0 tracking.taboola.com
0.0.0.0 trc.taboola.com
0.0.0.0 outbrain.com
0.0.0.0 traffic.outbrain.com
0.0.0.0 ad.criteo.com
0.0.0.0 static.criteo.net
0.0.0.0 openx.net
0.0.0.0 us-u.openx.net
0.0.0.0 serving-sys.com
0.0.0.0 smartadserver.com
0.0.0.0 moatads.com
0.0.0.0 applovin.com
0.0.0.0 d.applovin.com
0.0.0.0 unityads.unity3d.com
0.0.0.0 vungle.com
0.0.0.0 adcolony.com
0.0.0.0 ironSource.com
0.0.0.0 inmobi.com
0.0.0.0 taboola.com
# Top Adult / Explicit Destinations Baseline
0.0.0.0 pornhub.com
0.0.0.0 xvideos.com
0.0.0.0 xnxx.com
0.0.0.0 redtube.com
0.0.0.0 xhamster.com
0.0.0.0 chaturbate.com
0.0.0.0 stripchat.com
0.0.0.0 livejasmin.com
0.0.0.0 onlyfans.com
0.0.0.0 fansly.com
0.0.0.0 brazzers.com
0.0.0.0 youporn.com
0.0.0.0 spankbang.com
0.0.0.0 rule34.xxx
0.0.0.0 nhentai.net
EOF
  if [ -f "$CUSTOM_FILE" ]; then
    cat "$CUSTOM_FILE" >> "$HOSTS_FILE"
  fi
}

cmd_enable() {
  echo "==> Enabling Hotspot Hub AdBlocker & Proxy Shield..."

  # 1. Ensure dnsmasq configuration with Apple Private Relay & DoH suppression
  cat << EOF > "$DNSMASQ_CONF"
# Hotspot Hub AdBlocker & Shield
addn-hosts=$HOSTS_FILE
dhcp-option=252,$WPAD_URL

# Signal Apple devices (iOS/macOS) to disable iCloud Private Relay on this hotspot network
# (Per official Apple documentation: returning NXDOMAIN forces iOS to use local network DNS)
server=/mask.icloud.com/
server=/mask-h2.icloud.com/
server=/mask.apple-dns.net/

# Sinkhole public DoH endpoints to force client devices to use local dnsmasq
address=/dns.google/0.0.0.0
address=/dns.google.com/0.0.0.0
address=/cloudflare-dns.com/0.0.0.0
address=/dns.quad9.net/0.0.0.0
address=/doh.cleanbrowsing.org/0.0.0.0
address=/dns.adguard.com/0.0.0.0
EOF

  # 2. Ensure blocklist is populated
  if [ ! -s "$HOSTS_FILE" ]; then
    if [ -s "$BACKUP_FILE" ]; then
      cp "$BACKUP_FILE" "$HOSTS_FILE"
    else
      seed_baseline_blocklist
    fi
  fi

  # 3. Reload dnsmasq sinkhole
  reload_dnsmasq

  # 4. Privoxy proxy setup
  setup_privoxy_conf
  systemctl restart privoxy.service 2>/dev/null || true

  # 5. Apply iptables redirection
  apply_iptables_rules

  touch "$ENABLED_FLAG"
  local count
  count="$(wc -l < "$HOSTS_FILE" | tr -d ' ')"
  echo "Shield enabled: $count active adblock rules, Privoxy on 10.42.0.1:$PROXY_PORT (WPAD at $WPAD_URL)"
}

cmd_disable() {
  echo "==> Disabling Hotspot Hub AdBlocker & Proxy Shield..."

  # Backup current blocklist and truncate
  if [ -s "$HOSTS_FILE" ]; then
    cp "$HOSTS_FILE" "$BACKUP_FILE"
  fi
  : > "$HOSTS_FILE"

  # Reload dnsmasq sinkhole to clear blocked entries
  reload_dnsmasq

  # Stop Privoxy
  systemctl stop privoxy.service 2>/dev/null || true

  # Remove iptables redirection
  remove_iptables_rules

  rm -f "$ENABLED_FLAG"
  echo "Shield disabled: sinkhole cleared, Privoxy stopped, direct connection restored."
}

cmd_status() {
  local is_enabled="false"
  local rules_count=0
  local proxy_running="false"

  if [ -f "$ENABLED_FLAG" ] && [ -s "$HOSTS_FILE" ]; then
    is_enabled="true"
    rules_count="$(wc -l < "$HOSTS_FILE" | tr -d ' ')"
  elif [ -s "$HOSTS_FILE" ]; then
    rules_count="$(wc -l < "$HOSTS_FILE" | tr -d ' ')"
    if [ "$rules_count" -gt 0 ]; then
      is_enabled="true"
    fi
  fi

  if systemctl is-active --quiet privoxy.service 2>/dev/null; then
    proxy_running="true"
  fi

  cat << EOF
{
  "enabled": $is_enabled,
  "rules_count": $rules_count,
  "proxy_running": $proxy_running,
  "proxy_port": $PROXY_PORT,
  "wpad_url": "$WPAD_URL",
  "dns_sinkhole": $is_enabled
}
EOF
}

cmd_update() {
  echo "==> Fetching latest upstream blocklists (StevenBlack + YouTube Ads + Telemetry)..."
  local tmp_dir
  tmp_dir="$(mktemp -d /tmp/adblock-update.XXXXXX)"
  local tmp_hosts="$tmp_dir/hosts_raw.txt"
  local tmp_merged="$tmp_dir/merged.txt"

  # 1. StevenBlack Unified + Adult/Porn Hosts (152k+ ad, malware & explicit adult domains)
  curl -sSL --connect-timeout 10 --max-time 45 \
    "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn/hosts" \
    -o "$tmp_dir/stevenblack.txt" || true

  if [ -s "$tmp_dir/stevenblack.txt" ]; then
    grep "^0\.0\.0\.0 " "$tmp_dir/stevenblack.txt" | \
      grep -v -E " 0\.0\.0\.0$| localhost$| local$| broadcasthost$" >> "$tmp_hosts" || true
  fi

  # 2. Dedicated YouTube Ads Blacklist (16k+ in-video ad serving hosts & tracking endpoints)
  curl -sSL --connect-timeout 10 --max-time 45 \
    "https://raw.githubusercontent.com/kboghdady/youTube_ads_4_pi-hole/master/youtubelist.txt" \
    -o "$tmp_dir/yt.txt" || true

  if [ -s "$tmp_dir/yt.txt" ]; then
    sed -e 's/^[[:space:]]*//;s/[[:space:]]*$//' "$tmp_dir/yt.txt" | \
      grep -v "^#" | grep -v "^$" | \
      awk '{ if ($1 ~ /^[0-9]/) print $0; else print "0.0.0.0 " $1 }' >> "$tmp_hosts" || true
  fi

  # 3. High-Priority Popular App & Video Ad Endpoints (YouTube, TikTok, Spotify, Smart TVs)
  cat << "EOF" >> "$tmp_hosts"
0.0.0.0 s.youtube.com
0.0.0.0 video-stats.l.google.com
0.0.0.0 youtube-ui.l.google.com
0.0.0.0 ads.youtube.com
0.0.0.0 ad.youtube.com
0.0.0.0 manifest.googlevideo.com
0.0.0.0 googleads.g.doubleclick.net
0.0.0.0 pubads.g.doubleclick.net
0.0.0.0 securepubads.g.doubleclick.net
0.0.0.0 pagead2.googlesyndication.com
0.0.0.0 pagead2.googleadservices.com
0.0.0.0 adservice.google.com
0.0.0.0 googleadservices.com
0.0.0.0 partnerad.l.doubleclick.net
0.0.0.0 static.doubleclick.net
0.0.0.0 stats.g.doubleclick.net
0.0.0.0 www-googletagmanager.l.google.com
0.0.0.0 yt3.ggpht.com
0.0.0.0 redirector.googlevideo.com
0.0.0.0 ads.tiktok.com
0.0.0.0 analytics.tiktok.com
0.0.0.0 log.byteoversea.com
0.0.0.0 mon.byteoversea.com
0.0.0.0 business-api.tiktok.com
0.0.0.0 spclient.wg.spotify.com/ads
0.0.0.0 audio-ak-spotify-com.akamaized.net/ads
0.0.0.0 heads-ak-spotify-com.akamaized.net
0.0.0.0 countess.twitch.tv
0.0.0.0 usher.ttvnw.net/ad
0.0.0.0 ads.rubiconproject.com
0.0.0.0 c.amazon-adsystem.com
0.0.0.0 aax.amazon-adsystem.com
0.0.0.0 samsungcloudsolution.com
0.0.0.0 samsungadhub.com
0.0.0.0 ads.roku.com
0.0.0.0 track.roku.com
0.0.0.0 lgsmartad.com
0.0.0.0 unityads.unity3d.com
0.0.0.0 d.applovin.com
0.0.0.0 vungle.com
0.0.0.0 adcolony.com
0.0.0.0 ironSource.com
0.0.0.0 inmobi.com
EOF

  # 4. Merge custom user blocklist
  if [ -s "$CUSTOM_FILE" ]; then
    cat "$CUSTOM_FILE" >> "$tmp_hosts"
  fi

  if [ -s "$tmp_hosts" ]; then
    sort -u "$tmp_hosts" > "$HOSTS_FILE"
    rm -rf "$tmp_dir"
    reload_dnsmasq
    local count
    count="$(wc -l < "$HOSTS_FILE" | tr -d ' ')"
    touch "$ENABLED_FLAG"
    echo "Successfully updated blocklist: $count rules active."
  else
    rm -rf "$tmp_dir"
    echo "Upstream download failed; using seed baseline..."
    seed_baseline_blocklist
    reload_dnsmasq
    local count
    count="$(wc -l < "$HOSTS_FILE" | tr -d ' ')"
    echo "Seed baseline applied: $count rules active."
  fi
}

cmd_add() {
  local domain="${1:-}"
  if [ -z "$domain" ]; then
    echo "Usage: $0 add <domain>" >&2
    exit 1
  fi
  domain="$(echo "$domain" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  echo "0.0.0.0 $domain" >> "$CUSTOM_FILE"
  echo "0.0.0.0 $domain" >> "$HOSTS_FILE"
  sort -u -o "$CUSTOM_FILE" "$CUSTOM_FILE"
  sort -u -o "$HOSTS_FILE" "$HOSTS_FILE"
  reload_dnsmasq
  echo "Added $domain to adblock list."
}

cmd_remove() {
  local domain="${1:-}"
  if [ -z "$domain" ]; then
    echo "Usage: $0 remove <domain>" >&2
    exit 1
  fi
  domain="$(echo "$domain" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  if [ -f "$CUSTOM_FILE" ]; then
    sed -i "/[[:space:]]${domain}$/d" "$CUSTOM_FILE"
  fi
  if [ -f "$HOSTS_FILE" ]; then
    sed -i "/[[:space:]]${domain}$/d" "$HOSTS_FILE"
  fi
  reload_dnsmasq
  echo "Removed $domain from adblock list."
}

case "${1:-status}" in
  enable|on|start)
    cmd_enable
    ;;
  disable|off|stop)
    cmd_disable
    ;;
  status)
    cmd_status
    ;;
  update|refresh)
    cmd_update
    ;;
  add)
    cmd_add "${2:-}"
    ;;
  remove|del)
    cmd_remove "${2:-}"
    ;;
  *)
    echo "Usage: $0 {enable|disable|status|update|add <domain>|remove <domain>}" >&2
    exit 1
    ;;
esac
