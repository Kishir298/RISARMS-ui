"""Update-key resolution distinguishes production builds from validation builds."""

from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "resolve_update_key.py"


def run(tmp_path, **env_overrides):
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"UPDATE_PUBLIC_KEY", "RISARMS_VALIDATION_BUILD"}
    }
    environment["GITHUB_ENV"] = str(tmp_path / "github-env")
    environment["GITHUB_STEP_SUMMARY"] = str(tmp_path / "summary.md")
    environment.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        env=environment,
    )


def read_env(tmp_path) -> dict[str, str]:
    path = tmp_path / "github-env"
    if not path.is_file():
        return {}
    pairs = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            pairs[key] = value
    return pairs


def test_missing_key_without_validation_fails_closed(tmp_path):
    result = run(tmp_path)
    assert result.returncode != 0
    assert "UPDATE_PUBLIC_KEY is required" in result.stderr
    assert read_env(tmp_path) == {}


def test_validation_build_generates_ephemeral_key(tmp_path):
    result = run(tmp_path, RISARMS_VALIDATION_BUILD="1")
    assert result.returncode == 0, result.stderr
    written = read_env(tmp_path)
    raw = base64.b64decode(written["UPDATE_PUBLIC_KEY"], validate=True)
    assert len(raw) == 32
    assert written["RISARMS_UPDATE_KEY_FINGERPRINT"] == hashlib.sha256(raw).hexdigest()
    assert "validation-only" in result.stderr
    assert "not Apple/Windows OS code signing" in (
        tmp_path / "summary.md"
    ).read_text(encoding="utf-8")


def test_production_key_is_preserved(tmp_path):
    raw = bytes(range(32))
    key = base64.b64encode(raw).decode("ascii")
    result = run(tmp_path, UPDATE_PUBLIC_KEY=key)
    assert result.returncode == 0, result.stderr
    written = read_env(tmp_path)
    assert written["UPDATE_PUBLIC_KEY"] == key
    assert written["RISARMS_UPDATE_KEY_FINGERPRINT"] == hashlib.sha256(raw).hexdigest()
    assert "validation-only" not in result.stderr


def test_invalid_production_key_is_rejected(tmp_path):
    result = run(tmp_path, UPDATE_PUBLIC_KEY="not-a-key")
    assert result.returncode != 0
    assert "UPDATE_PUBLIC_KEY is invalid" in result.stderr
