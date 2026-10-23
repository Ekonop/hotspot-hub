# Configuration

All names are generic. Defaults work for a typical single-radio laptop
(`wlan0` STA + `ap0` AP). Override without editing code:

| Setting | Env var | Default |
|---|---|---|
| STA interface | `HOTSPOT_HUB_STA_IF` | `wlan0` |
| AP interface | `HOTSPOT_HUB_AP_IF` | `ap0` |
| NM hotspot profile | `HOTSPOT_HUB_CON` | `Hotspot-Shared` |
| Docs path (Open Documentation) | `HOTSPOT_HUB_DOCS` | `~/doc/hotspot-system/README.md`, else repo `docs/README.md` |
| Toggle shortcut | `HOTSPOT_HUB_SHORTCUT` | `Ctrl+Alt+H` |

The channel-sync script honors `STA_IF`, `AP_IF`, `HOTSPOT_CON` the same way.

## Wi-Fi profile

The package never ships secrets. Create the hotspot profile on first install:

```bash
/usr/share/hotspot-hub/setup-profile.sh        # prompts SSID + passphrase
/usr/share/hotspot-hub/setup-profile.sh --ssid MyHub --password 'long-random-psk'
/usr/share/hotspot-hub/setup-profile.sh --show  # inspect (no secrets printed)
```

Security applied: WPA2-Personal (or WPA3-SAE if supported and enabled),
`proto rsn`, `pairwise/group ccmp`, IPv4 shared `10.42.0.1/24`, IPv6 disabled.
WPA3-SAE is auto-detected via `iw phy` and can be enabled during setup via
`--wpa3` or through the configuration dialog.

## MAC Filtering

Hotspot Hub supports client access control through allow and block lists located in
`~/.config/hotspot-hub/`:

- `allowed_macs.txt`: Allowed client MAC addresses (one per line).
- `blocked_macs.txt`: Blocked client MAC addresses (one per line).
- `config.json`: Stores `mac_filter_enabled` (boolean) and `mac_filter_mode` (`"allow"` or `"deny"`).

When MAC filtering is enabled, unauthorized stations are automatically kicked
using `iw dev ap0 station del <mac>`. MACs can be managed via the System card's
"MAC Filter" dialog, the Connected Clients dialog, or via `hotspot-hub-cli mac-filter`.

## 5GHz Band Support

The Wi-Fi band can be configured as `2.4GHz`, `5GHz`, or `Auto`:
- In the GUI: via the Band selector dropdown in the Hotspot card.
- In CLI: `hotspot-hub-cli band [2.4GHz|5GHz|Auto]`.
- In setup: `setup-profile.sh --band [2.4|5|auto]`.

Available channels on the adapter's Wi-Fi PHY are automatically validated before
applying band modifications to NetworkManager. Preference is persisted in
`~/.config/hotspot-hub/config.json`.

## Window Geometry Persistence

`DropdownPanel` window dimensions and position are stored and restored via `QSettings`
under organization `hotspot-hub`. The maximum width restriction has been removed,
and a resize grip is provided at the bottom-right corner.

## Firewall / forwarding / VPN coexistence

- `net.ipv4.ip_forward=1` via `src/99-hotspot.conf`.
- UFW (if present): `ufw allow in on ap0` and
  `ufw route allow in on ap0 out on wlan0`. Without these, DHCP from
  `dnsmasq` never reaches clients (empty leases, `authorized yes` but
  `rx >> tx`).
- VPN/WARP bypass: VPN clients (e.g. WARP table `65743`) capture most public
  space, including NATed hotspot flows. `hotspot-channelsync.sh` therefore
  enforces a bypass **at pref 50** — both `from <ap-subnet> lookup main` and
  `from all iif <ap-if> lookup main`. The pref-50 value matters: WARP changes
  its capture-rule priority between runs (seen 32765 / 999 / 99), so any
  hardcoded lower-priority bypass gets silently outranked and clients lose
  internet. The script deletes stale duplicates and re-adds every run
  (10 s poll + NM events), so it is self-repairing. Verify with:
  `ip route get 8.8.8.8 from <client-ip> iif ap0` (expect `dev <uplink>`).
