# Architecture

Single-radio concurrent STA+AP. The Wi-Fi card exposes one PHY with an
interface combination like `#{managed} <= 2, #{AP} <= 1, #channels <= 1`,
so station and AP must share one channel.

```text
campus/router ──(STA wlan0, DHCP client)── host ──NAT/masquerade── (AP ap0, 10.42.0.1/24)── clients
                                            └─ dnsmasq (DHCP 10.42.0.10-254, DNS) on ap0
```

## Components

| Piece | Role |
|---|---|
| `ap0` (via `iw phy phy0 interface add ap0 type __ap`) | Virtual AP interface on the same PHY/channel as STA |
| NM profile `Hotspot-Shared` | SSID/security/band/channel/binding to `ap0`, shared IPv4 |
| `hotspot-channelsync.sh` | Poll-safe sync: reads STA channel/freq (`iw dev`), maps `<3000MHz=bg else a`, updates profile, restarts AP |
| `90-hotspot-channelsync` (NM dispatcher) | Event-driven sync on STA up/reapply/connectivity/DHCP change |
| `hotspot-channelsync.service` | 10 s poll fallback + recreates `ap0` after reboot |
| `99-hotspot.conf` | Persists `net.ipv4.ip_forward=1` |
| `hotspot-hub.sudoers` (`%wheel` NOPASSWD, scoped) | Passwordless panel ops |
| `hotspot-hub.py` (PyQt6 tray + dropdown canvas) | UI: `PanelSection` cards (Hotspot/WARP/System), tray menu fallback |

## Traffic notes

- Host VPNs (e.g. WARP via fwmark+policy table) typically cover local sockets
  only; NATed hotspot clients follow the main table directly. Both can stay on.
- `nm-shared-ap0` nftables chain masquerades `10.42.0.0/24` to the uplink.
