"""Platform-specific application directories.

Secrets are never stored here. This module only returns paths for
preferences, the offline database, cached files and logs.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def config_directory() -> Path:
    if os.environ.get("RISARMS_UI_HOME"):
        return Path(os.environ["RISARMS_UI_HOME"]).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "RISARMS" / "RISARMS-UI"


def data_directory() -> Path:
    path = config_directory() / "offline"
    path.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "posix":
            path.chmod(0o700)
    except OSError:
        pass
    return path


def preferences_file() -> Path:
    return config_directory() / "preferences.json"


def server_url_file() -> Path:
    return config_directory() / "server-url.txt"


def log_directory() -> Path:
    path = config_directory() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
