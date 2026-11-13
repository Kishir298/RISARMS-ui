"""Command-line entrypoint for the RISARMS Portal desktop application.

``--doctor`` performs dependency and platform checks without opening a
window, which is useful on headless build agents and fresh installs.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys

from . import APP_NAME, __version__


def _doctor() -> int:
    checks = {
        "application": APP_NAME,
        "version": __version__,
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
    }
    failures = []

    try:
        import tkinter

        checks["tkinter"] = f"Tk {tkinter.TkVersion}"
        try:
            probe = tkinter.Tk()
            probe.withdraw()
            probe.destroy()
            checks["display"] = "available"
        except Exception as exc:
            checks["display"] = f"unavailable: {exc}"
            failures.append("display")
    except Exception as exc:
        checks["tkinter"] = f"missing: {exc}"
        failures.append("tkinter")

    try:
        import keyring

        backend = keyring.get_keyring()
        checks["keyring"] = f"{backend.__class__.__module__}.{backend.__class__.__name__}"
        if "fail" in backend.__class__.__module__.lower():
            failures.append("keyring")
    except Exception as exc:
        checks["keyring"] = f"missing: {exc}"
        failures.append("keyring")

    try:
        import websockets

        checks["websockets"] = websockets.__version__
    except Exception as exc:
        checks["websockets"] = f"missing: {exc}"
        failures.append("websockets")

    try:
        import client

        checks["core-client"] = getattr(client, "__version__", "installed")
    except Exception as exc:
        checks["core-client"] = f"missing: {exc}"
        failures.append("core-client")

    try:
        from .paths import config_directory, data_directory

        for path in (config_directory(), data_directory()):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            probe = path / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        checks["application directories"] = str(config_directory())
    except Exception as exc:
        checks["application directories"] = f"not writable: {exc}"
        failures.append("directories")

    checks["result"] = "ready" if not failures else "not ready: " + ", ".join(sorted(set(failures)))
    print(json.dumps(checks, indent=2))
    return 0 if not failures else 1


def _fatal_tkinter(exc: Exception) -> int:
    message = [
        f"{APP_NAME} cannot start because Tk is not available for this Python:",
        f"  {sys.executable}",
        f"  {exc}",
        "",
        "Install a Python distribution that includes Tcl/Tk:",
        "  macOS (Homebrew):  brew install python-tk@3.14",
        "  Debian/Ubuntu:     sudo apt install python3-tk",
        "  Windows:           use the python.org installer with Tcl/Tk enabled",
        "",
        "No local data was modified. Run 'risarms-ui --doctor' for details.",
    ]
    print("\n".join(message), file=sys.stderr)
    return 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="risarms-ui", description=f"{APP_NAME} {__version__}")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--doctor", action="store_true", help="check dependencies and platform support, then exit")
    parser.add_argument("--no-events", action="store_true", help="disable the WSS event channel and use polling only")
    parser.add_argument("--no-auto-connect", action="store_true", help="start offline even when a device credential is remembered")
    parser.add_argument("--url", help="save the CORE-HOST portal URL before launching")
    options = parser.parse_args(argv)

    if options.doctor:
        return _doctor()

    if options.url:
        from .connection.api import validate_origin
        from .paths import server_url_file

        try:
            portal_url = validate_origin(options.url)
        except ValueError as exc:
            parser.error(str(exc))
        try:
            server_url_file().parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.environ["RISARMS_UI_PORTAL_URL"] = portal_url
            server_url_file().write_text(portal_url + "\n", encoding="utf-8")
            server_url_file().chmod(0o600)
        except OSError as exc:
            print(f"Could not save the portal URL: {exc}", file=sys.stderr)
            return 2

    if options.no_auto_connect:
        os.environ["RISARMS_UI_NO_AUTO_CONNECT"] = "1"

    try:
        from .app import App, setup_logging
    except ModuleNotFoundError as exc:
        if exc.name in ("tkinter", "_tkinter"):
            return _fatal_tkinter(exc)
        raise
    except Exception as exc:
        print(f"{APP_NAME} failed to import its interface: {exc}", file=sys.stderr)
        return 2

    setup_logging()
    try:
        app = App(enable_events=not options.no_events)
    except RuntimeError as exc:
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(APP_NAME, str(exc))
            root.destroy()
        except Exception:
            print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"{APP_NAME} could not start: {exc}", file=sys.stderr)
        return 2
    try:
        app.root.mainloop()
    finally:
        try:
            app.close()
        except Exception:
            pass
    return 0
