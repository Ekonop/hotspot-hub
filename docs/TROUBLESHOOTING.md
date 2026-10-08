# Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Client stuck at auth / `AP-STA-POSSIBLE-PSK-MISMATCH`, `authorized no` | Wrong passphrase (often a cached password from a reused SSID) | Forget the SSID on the client, re-enter the passphrase |
| Client shows IP-configuration error, leases empty, `rx >> tx` | Firewall drops DHCP (UFW `INPUT/FORWARD DROP`) | `ufw allow in on ap0`, `ufw route allow in on ap0 out on wlan0` |
| Clients associated but no internet | `net.ipv4.ip_forward=0` | Set `1` (see `99-hotspot.conf`), check `nft` masquerade table |
| No internet only while VPN/WARP is on | Hotspot flows captured into VPN policy table (e.g. WARP `65743`); UFW has no ap0→tun forward allow | Bypass rule `from 10.42.0.0/24 table main priority 1000` (auto-kept by sync script); verify `ip route get 8.8.8.8 from 10.42.0.10 iif ap0` shows the uplink |
| WPA3-SAE AP fails (`WPA initialization failed`) | Firmware lacks SAE AP support (STA-only SAE) | Stay on hardened WPA2 (rsn/ccmp/pmf optional); do not force `sae` |
| AP on wrong channel after STA roam | Sync service down | `systemctl status hotspot-channelsync.service`, `journalctl -t hotspot-channelsync` |
| AP MAC `00:00:65:00:72:00` / type stuck | Corrupted profile after failed SAE attempt | Reset `connection.interface-name ap0` + `cloned-mac-address permanent`, restart |
| Password prompt on every panel action | Sudoers file missing | Install `src/hotspot-hub.sudoers`, `visudo -c`, test `sudo -n cat <leases>` |

Collect evidence first: `iw dev ap0 station dump`,
`journalctl -u wpa_supplicant --since '10 min ago' | grep ap0`,
`journalctl | grep dnsmasq-dhcp | tail`.
