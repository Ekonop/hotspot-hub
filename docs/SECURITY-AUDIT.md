# Hotspot Hub — Security & Reliability Audit

Audit date: 2026-10-24
Scope: `app/hotspot-hub.py`, `cli/hotspot-hub-cli.py`, `src/hotspot-hub.sudoers`,
`src/` dispatchers, `helpers/`.

---

## 1. Least-privilege sudoers ✅

`src/hotspot-hub.sudoers` grants passwordless root only for **exact binaries and
argument prefixes** — no blanket `NOPASSWD: ALL`, no wildcard on shell or editors.

Every `priv()` / `sudo -n` call site maps to a rule:

| Call site | Sudoers rule |
|-----------|--------------|
| `iw phy <phy> interface add ap0 type __ap` | `iw phy * interface add ap0 type __ap` |
| `ip link set ap0 up` | `ip link set ap0 *` |
| `iw dev ap0 station del <mac>` | `iw dev ap0 station del *` |
| `systemctl {restart,status,is-active} hotspot-channelsync.service` | exact service name |
| `cat /var/lib/NetworkManager/dnsmasq-ap0.leases` | exact path |
| `nft list *` | `nft list *` (read-only) |
| `/usr/local/bin/hotspot-channelsync.sh` | exact path |
| `touch /run/hotspot-hub-disabled` | exact path |
| `rm -f /run/hotspot-hub-disabled` | exact path |
| `sysctl -w net.ipv6.conf.ap0.disable_ipv6=<0\|1>` | `sysctl -w net.ipv6.conf.ap0.disable_ipv6=*` |

Notes:
- Wildcards are restricted to values that are already constrained by the app
  (auto-detected `phy`, fixed `ap0` interface name, IPv6 boolean).
- The IPv6 `sysctl` rule was added in this audit — it was previously missing,
  so the toggle fell back to `pkexec` (GUI prompt). It is now passwordless.
- `nft list *` is read-only; no `nft add/flush` rule is granted.

## 2. No shell injection ✅

- **Zero** occurrences of `shell=True`, `os.system`, or `os.popen` across
  `app/`, `cli/`, `helpers/`, `src/`.
- All subprocess invocations go through `run()` / `run_rc()` / `priv()`, which
  take a `Sequence[str]` and pass it to `subprocess.run(cmd, ...)` as a list.
- User- and network-derived values (MAC addresses, band names, interface names)
  are either regex-validated (`load_mac_file`, station dump parser) or selected
  from fixed enum lists (`QComboBox` band options) before reaching a command.
- MAC addresses used in `station del` are validated by
  `^([0-9a-f]{2}:){5}[0-9a-f]{2}$` at parse time.

## 3. Secrets handling (PSK) ✅

- The hotspot PSK is read only in `show_qr_code()` via
  `nmcli -s -g 802-11-wireless-security.psk ...` (`-s` = secrets flag).
- It is passed directly to `qr_code_png()` and never:
  - written to the config file (`config.json` stores no PSK),
  - passed to `logger.*` (grep confirms no `psk`/`password`/`secret` in any
    logger call),
  - included in a command argument (the *command* contains only the connection
    name, not the secret).
- On success `run()` returns stdout without logging it; on failure only
  stderr/return code are logged. The PSK therefore never reaches the log file.

## 4. Error handling policy ✅ (by design)

The app is a long-lived tray process: an unhandled exception would kill the
hotspot controls. The broad `except Exception` blocks are **intentional** at
these boundaries:

- **Subprocess layer** (`run` / `run_rc` / `priv`): documented "never raises" —
  returns `''` / `(1, '')` so callers can degrade gracefully.
- **UI refresh loops** (`refresh_all`, section `refresh`): one broken card must
  not stop the others from updating.
- **Config / file I/O**: logged via `logger.warning`/`error` with the path.

Tighter handling (`PermissionError`, `OSError`, `json.JSONDecodeError`,
`subprocess.TimeoutExpired`) is already used where the failure mode is known —
e.g. `set_disabled_flag`, `load_mac_file`, `save_app_config`, `lease_count`.

Recommendation: keep broad catches at process/UI boundaries; new pure parsing
code should raise specific exceptions (covered by unit tests in `tests/`).

---

## Verification

```bash
# sudoers syntax
visudo -c -f src/hotspot-hub.sudoers

# no shell injection
grep -rn "shell=True\|os.system\|os.popen" app/ cli/ helpers/ src/

# no secrets in logs
grep -n "logger\." app/hotspot-hub.py | grep -iE "psk|password|secret"

# lint + tests
ruff check app/ cli/ tests/
python3 -m unittest discover -s tests -v
```
