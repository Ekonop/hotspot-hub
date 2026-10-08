# Hotspot Hub

KDE system-tray app for concurrent Wi-Fi hotspot (STA/AP, single radio),
WARP toggle, and channel-sync control on Arch/CachyOS (Plasma Wayland,
MediaTek MT7922).

![icon](icons/hotspot-hub.svg)

## Layout

- `app/hotspot-hub.py` — tray app with dropdown canvas (`DropdownPanel`).
  Left-click opens cards (**Hotspot**, **WARP**, **System**); right-click
  shows the classic menu. Add future mods as `PanelSection` subclasses,
  registered in `DropdownPanel._build_sections()` (marked EXTENSION POINT).
- `helpers/` — `hotspot-on.sh` / `hotspot-off.sh` (idempotent, `sudo -n`).
- `icons/hotspot-hub.svg` — app icon (blue hotspot glyph + amber node).
- `src/` — system files: `hotspot-channelsync.sh`,
  `90-hotspot-channelsync` (NM dispatcher),
  `hotspot-channelsync.service`, `99-hotspot.conf` (ip_forward),
  `hotspot-hub.sudoers` (`%wheel` NOPASSWD, scoped).
- `scripts/setup-profile.sh` — create/retune `Hotspot-Shared` NM profile
  (prompts for SSID/PSK on fresh installs; never ships secrets).
- `scripts/stage-and-build.sh`, `packaging/arch/{PKGBUILD,hotspot-hub.install}`
  — Arch package `hotspot-hub 0.1.0`.
- `install.sh` / `uninstall.sh` / `Makefile` — direct install without makepkg.

No plaintext passwords anywhere.

## Quick install (from this repo)

```bash
./install.sh            # files + sysctl + ufw + service + profile prompt + autostart
hotspot-hub &
```

Options: `./install.sh --no-profile --no-ufw --no-enable`

## Arch package

```bash
make package            # builds hotspot-hub-0.1.0-1-any.pkg.tar.zst
sudo pacman -U packaging/arch/hotspot-hub-0.1.0-1-any.pkg.tar.zst
/usr/share/hotspot-hub/setup-profile.sh
```

## Uninstall

```bash
./uninstall.sh          # keeps NM profile + UFW rules
./uninstall.sh --purge  # also deletes profile + UFW rules
```

## Docs

Full setup/ops/troubleshooting: `~/doc/hotspot-system/`
(`README`, `SETUP`, `OPERATIONS`, `TROUBLESHOOTING`, `FILES`).

## Troubleshooting

- **Tray icon not showing**: Plasma → System Tray Settings → set Hotspot Hub
  visible. Re-login after enabling autostart.
- **Password prompt appears**: check `/etc/sudoers.d/hotspot-hub` exists and
  `sudo -n cat /var/lib/NetworkManager/dnsmasq-ap0.leases` works without a
  prompt (you must be in `wheel`).
- **AP won't start / wrong channel**: STA and AP share one radio —
  `systemctl status hotspot-channelsync.service`,
  `journalctl -t hotspot-channelsync`.
- **Wayland popup warnings**: fixed by using `Qt.Tool` (no grab) instead of
  `Qt.Popup`; if you see grab warnings, pull latest `app/hotspot-hub.py`.
- **WARP "not installed"**: install `cloudflare-warp-bin`, then
  `warp-cli --accept-tos register`.
