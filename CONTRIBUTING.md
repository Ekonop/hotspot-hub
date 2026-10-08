# Contributing

- Keep it secret-free: never commit SSIDs, passphrases, MACs, IPs, leases,
  or `~` paths. Grep before committing:
  `grep -rniE 'psk|passphrase|([0-9a-f]{2}:){5}[0-9a-f]{2}' --exclude-dir=.git .`
- Python: `python3 -m py_compile app/hotspot-hub.py`. Shell: `bash -n <file>`.
- Test on Arch/CachyOS + KDE Plasma; note your adapter (`lspci -k | grep -A3 Net`)
  and `iw list | grep -A6 'valid interface combinations'` in PRs.
- Docs live in `docs/`; keep README install steps working via `./install.sh`.
