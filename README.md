# RISARMS Portal — Unified Desktop Application

**Version:** `0.6.0`
**Status:** Source and automated tests implemented. Local macOS Intel packaging
and release metadata checks are available. Physical Apple Silicon, Windows, Linux,
and Void smoke runs, signed/notarized installers, and long-duration validation are
**not yet performed**. Beta assets are unsigned Windows and ad-hoc-signed,
unnotarized macOS.
**Platforms:** macOS Intel and Apple Silicon, Windows x86_64, Linux x86_64
(Python `>=3.10`, Tkinter/ttk). iOS TestFlight and Android beta distribution are
future work only.

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
  settings, server status and a queued owner-action inbox. Approval requires a
  registered owner device plus password re-authentication.
- Local ASCS/ASIS actions ask through a loopback approval dialog; closing it,
  timing out, or losing the broker denies the action.
- Ed25519-verified GitHub Releases update manifests with hash-checked
  downloads. Updates are never installed silently.
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
directories without opening a window. CORE-CLIENT is not required. Local chat
uses Ollama directly; signed update checking uses `cryptography`.

## Tests and lint

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check risarms_ui tests packaging
```

Current result: **286 tests passed** (macOS Python 3.14). Tests do not require a
display; GUI smoke tests require Tk. The verified CORE-HOST baseline is 911
passed / 4 skipped and CORE-CLIENT is 225 passed / 1 skipped.

## Build desktop artifacts

```bash
python -m pip install -e ".[package]"
python packaging/build.py
```

Artifacts are written to `dist/`:

- macOS: `RISARMS-Portal-0.6.0-macos-x86_64.zip` / `macos-arm64.zip`
- Windows: `RISARMS-Portal-0.6.0-windows-x86_64.zip` plus `.exe`/`.msi` installers
- Linux: `RISARMS-Portal-0.6.0-linux-x86_64.tar.gz` plus `.deb`/`.AppImage`
- `SHA256SUMS-<platform>` and `sbom-<platform>.cdx.json`

Builds are unsigned by default. macOS is ad-hoc signed by `packaging/build.py`
when no Developer ID is configured; it is not notarized. Windows builds are
unsigned. Optional hooks:

| Variable | Purpose |
|---|---|
| `RISARMS_SIGN_IDENTITY` | macOS Developer ID identity |
| `RISARMS_NOTARY_PROFILE` | notarytool keychain profile |
| `RISARMS_SIGN_CERT` / `RISARMS_SIGN_PASSWORD` | Windows certificate |
| `RISARMS_TIMESTAMP_URL` | Windows RFC3161 timestamp server |

Never commit certificates, private keys, notarization profiles or passwords.

## Release workflow

`.github/workflows/desktop-release.yml` runs tests and packaging on macOS Intel,
macOS Apple Silicon, Windows x86_64, and Linux x86_64. Tagged releases use
`v0.6.0` or `v0.6.0-beta.N`, validate the tag against `pyproject.toml` and
`risarms_ui.__version__`, and create a **draft** GitHub release. The workflow
never publishes automatically and never overwrites an existing release.

Draft beta assets are labeled unsigned and unnotarized. The aggregate
`SHA256SUMS`, per-platform checksums, SBOMs, and Ed25519-signed `update.json` are
draft assets for testing. Public distribution and signed auto-updates remain
gated on a production update key and Apple/Windows signing credentials.

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
├── local.py          local Ollama/service runner (stdlib, no CORE imports)
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
| UI/HTTP/connection tests | IMPLEMENTED (286 passed) |
| CORE-HOST desktop control contracts | IMPLEMENTED (911 passed / 4 skipped) |
| CORE-CLIENT tests | IMPLEMENTED (225 passed / 1 skipped) |
| Tk GUI launch in this workspace | PENDING (Tk not installed) |
| Physical Apple Silicon run | NOT YET PERFORMED |
| Physical Windows run | NOT YET PERFORMED |
| Physical Linux run | NOT YET PERFORMED |
| Void Linux glibc smoke test | NOT YET PERFORMED |
| Signed/notarized installers | NOT YET PERFORMED (hooks implemented) |
| Public signed auto-updates | GATED (no production key) |
| LAN reconnect/revocation and 24/7 use | NOT YET PERFORMED |
| iOS TestFlight / Android beta | FUTURE WORK |

The `tests/` directory also retains protocol-compatibility tests migrated from
CORE-CLIENT; the native TCP/TLS client itself lives in the separate
`Kishir298/CORE-CLIENT` repository, which this application embeds as a
sibling checkout — it is not part of RISARMS-ui.
