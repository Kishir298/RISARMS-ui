# RISARMS Portal — Unified Desktop Application

**Version:** `0.6.0`
**Status:** Source and automated tests implemented. Physical Windows/Linux runs,
signed installers and long-duration validation are **not yet performed**.
**Platforms:** macOS, Windows, Linux (Python `>=3.10`, Tkinter/ttk)

RISARMS Portal is the canonical desktop client for the R.I.S.A.R.M.S. ecosystem.
It connects to CORE-HOST and presents C.O.R.E., ASIS, ASCS, RESCS and TIVISS in
one application while keeping local profiles, history, records and files usable
offline.

```text
RISARMS Portal (this application)
        │ HTTPS + WSS
        ▼
CORE-HOST (127.0.0.1:8780 / 8781 behind Caddy)
        ├── ASIS     chat / owner services
        ├── ASCS     coding workspace
        ├── TIVISS   independent agent
        └── RESCS    records + files
```

The desktop app is a presentation client. It does not bypass CORE-HOST roles,
CSRF, audit, storage scope or owner re-authentication.

## Features

- Local profiles protected by a passphrase-derived verifier (scrypt); no
  recovery service. This is an application lock, not disk encryption.
- HTTPS login with password, registration request or first-owner setup.
- Remembered, scoped and revocable **device credentials** stored in the OS
  keychain (memory-only if keyring is unavailable).
- Automatic reconnect with backoff, 12-hour server sessions, renewal and a WSS
  control channel; polling continues when WSS is unavailable.
- Offline SQLite store for records, history, cached files and an explicit sync
  queue with conflict reporting.
- RESCS record and cloud-file workspaces with SHA-256 integrity checks and an
  8 MiB per-file limit.
- ASIS chat (server, local Ollama or local service), ASCS coding workspace and
  TIVISS workspace.
- Task jobs with output polling, cancellation and idempotent server creation.
- Owner-only administration for accounts, access requests, audit, quotas,
  settings and server status.
- Light/dark themes, responsive scrolling, keyboard basics and single-instance
  protection.
- PyInstaller one-directory builds for macOS, Windows and Linux, with optional
  signing/notarization hooks.

## Install for development

Tkinter must be present for the GUI:

```bash
# macOS with Homebrew Python
brew install python-tk@3.14

# Debian/Ubuntu
sudo apt install python3-tk
```

Then:

```bash
git clone https://github.com/Kishir298/RISARMS-ui.git
cd RISARMS-ui
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m risarms_ui --doctor
.venv/bin/python -m risarms_ui
```

`--doctor` checks Tk, the display, keyring, websockets and writable application
directories without opening a window. CORE-CLIENT is not required.

## Tests and lint

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check risarms_ui tests packaging
```

Current result: **205 tests passed** (macOS Python 3.14). Tests do not require a
display; GUI smoke tests require Tk.

## Build desktop artifacts

```bash
python -m pip install -e ".[package]"
python packaging/build.py
```

Artifacts are written to `dist/`:

- macOS: `RISARMS-Portal-0.6.0-macos-<arch>.zip` (contains `RISARMS Portal.app`)
- Windows: `RISARMS-Portal-0.6.0-windows-<arch>.zip`
- Linux: `RISARMS-Portal-0.6.0-linux-<arch>.tar.gz`
- `SHA256SUMS`

Builds are unsigned by default. Optional hooks:

| Variable | Purpose |
|---|---|
| `RISARMS_SIGN_IDENTITY` | macOS Developer ID identity |
| `RISARMS_NOTARY_PROFILE` | notarytool keychain profile |
| `RISARMS_SIGN_CERT` / `RISARMS_SIGN_PASSWORD` | Windows certificate |
| `RISARMS_TIMESTAMP_URL` | Windows RFC3161 timestamp server |

Never commit certificates, private keys, notarization profiles or passwords.

## Security notes

- Passwords, cookies, CSRF tokens and WSS tickets stay in memory.
- Only server-issued device credentials are persisted, through `keyring`.
- Remote servers require HTTPS. HTTP is accepted only for loopback.
- Redirects, environment proxies and origins containing credentials/paths are
  refused.
- Local concurrent edits are retained and reported as conflicts rather than
  overwritten.
- Logs never include passwords, session tokens, CSRF values or device
  credentials.
- Use full-disk encryption if local offline data needs encryption at rest.

## Layout

```text
risarms_ui/
├── app.py            workspace shell, local login, navigation, UI queue
├── entrypoint.py     CLI, --doctor, GUI startup
├── jobs.py           server job polling and local execution
├── layout.py         DPI-aware geometry and scrolling
├── local.py          local Ollama/service runner (from CORE-CLIENT)
├── paths.py          platform application directories
├── remote.py         authenticated sync helpers
├── store.py          offline SQLite profiles/records/files/history
├── theme.py          light/dark palettes
├── widgets.py        cards, status pill, scroll frame
├── connection/       HTTPS API, WSS client, state machine
├── pages/            home, chats, tasks, storage, files, devices, admin, settings
└── security/         OS keychain wrapper
```

## Documentation

- [System specification](https://github.com/Kishir298/RISARMS-DOCS/blob/main/systems/risarms-ui.md)
- [Build and release](https://github.com/Kishir298/RISARMS-DOCS/blob/main/development/desktop-app.md)
- [ADR 0010 — canonical desktop client](https://github.com/Kishir298/RISARMS-DOCS/blob/main/decisions/0010-risarms-ui-canonical-desktop-client.md)

## Validation status

| Item | Status |
|---|---|
| UI/HTTP/connection tests | IMPLEMENTED (205 passed) |
| CORE-HOST desktop control contracts | IMPLEMENTED (865 passed / 1 skipped) |
| Tk GUI launch in this workspace | PENDING (Tk installation incomplete) |
| Physical Windows run | NOT YET PERFORMED |
| Physical Linux run | NOT YET PERFORMED |
| Signed/notarized installers | NOT YET PERFORMED (hooks implemented) |
| LAN reconnect/revocation and 24/7 use | NOT YET PERFORMED |

The `tests/` directory also retains protocol-compatibility tests migrated from
CORE-CLIENT; the native TCP/TLS client itself remains in `CORE/CORE-CLIENT`.
