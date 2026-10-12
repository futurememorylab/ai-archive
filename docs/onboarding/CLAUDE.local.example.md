# CLAUDE.local.md template

Copy to `CLAUDE.local.md` in the repo root (gitignored) and edit for your
machine. Claude Code loads it next to `CLAUDE.md`. Example content from the
maintainer's macOS laptop:

## Network

- CatDV is at `192.168.1.41:8080` and needs the office VPN.
- VPN, credentials and contacts: see the parent `sikl/CLAUDE.md`.

## Shell

- node/npm/npx come from nvm and aren't on PATH in non-interactive shells:
  `source ~/.nvm/nvm.sh` first, or use absolute paths.
- Python 3.14 venvs are broken on this machine; use 3.12 or 3.13.
- Use absolute tool paths in checks: `/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN`,
  `/usr/sbin/lsof -nP -iTCP@192.168.1.41:8080`.

## Network ops

- If `git fetch`, `pip` or `ping` shows no traffic within a couple of
  seconds, treat it as a network failure instead of waiting.
