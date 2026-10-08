# Configuration

All names are generic. Defaults work for a typical single-radio laptop
(`wlan0` STA + `ap0` AP). Override without editing code:

| Setting | Env var | Default |
|---|---|---|
| STA interface | `HOTSPOT_HUB_STA_IF` | `wlan0` |
| AP interface | `HOTSPOT_HUB_AP_IF` | `ap0` |
| NM hotspot profile | `HOTSPOT_HUB_CON` | `Hotspot-Shared` |
| Docs path (Open Documentation) | `HOTSPOT_HUB_DOCS` | `~/doc/hotspot-system/README.md`, else repo `docs/README.md` |

The channel-sync script honors `STA_IF`, `AP_IF`, `HOTSPOT_CON` the same way.

## Wi-Fi profile

The package never ships secrets. Create the hotspot profile on first install:

```bash
/usr/share/hotspot-hub/setup-profile.sh        # prompts SSID + passphrase
/usr/share/hotspot-hub/setup-profile.sh --ssid MyHub --password 'long-random-psk'
/usr/share/hotspot-hub/setup-profile.sh --show  # inspect (no secrets printed)
```

Security applied: WPA2-Personal, `proto rsn` (WPA2-only), `pairwise/group ccmp`
(AES-only), `pmf optional`, IPv4 shared `10.42.0.1/24`, IPv6 disabled.
WPA3-SAE AP is intentionally not offered: MediaTek MT7921e/MT7922 firmware
rejects SAE AP init under `wpa_supplicant` (`WPA initialization failed`).

## Firewall / forwarding / VPN coexistence

- `net.ipv4.ip_forward=1` via `src/99-hotspot.conf`.
- UFW (if present): `ufw allow in on ap0` and
  `ufw route allow in on ap0 out on wlan0`. Without these, DHCP from
  `dnsmasq` never reaches clients (empty leases, `authorized yes` but
  `rx >> tx`).
- VPN/WARP bypass: VPN clients (e.g. WARP table `65743`) capture most public
  space, including NATed hotspot flows. `hotspot-channelsync.sh` therefore
  keeps `ip rule add from 10.42.0.0/24 table main priority 1000`, so hotspot
  clients always use the main table (direct uplink) while the host itself
  stays on VPN. Verify with:
  `ip route get 8.8.8.8 from 10.42.0.10 iif ap0` (expect `dev <uplink>`).
