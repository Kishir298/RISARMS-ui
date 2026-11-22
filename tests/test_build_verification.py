"""The frozen bundle must be able to start before it is packaged.

A build can report success and still produce an executable that dies on its
first import (the Linux artifacts shipped with ``No module named '_struct'``
because two Python 3.12 toolchains were mixed). ``build.verify_frozen_binary``
is the guard that turns that into a build failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))

import build  # noqa: E402  (local packaging helper)

WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="the fixture uses a POSIX shell script"
)


def make_bundle(directory: Path, exit_code: int) -> Path:
    target = directory / "RISARMS-Portal"
    target.mkdir(parents=True)
    executable = target / "risarms-ui"
    executable.write_text(
        f"#!/bin/sh\necho 'RISARMS Portal 0.6.0'\nexit {exit_code}\n", encoding="utf-8"
    )
    executable.chmod(0o755)
    return target


@WINDOWS_ONLY
def test_verify_frozen_binary_accepts_a_bundle_that_starts(tmp_path):
    build.verify_frozen_binary(make_bundle(tmp_path, 0))


@WINDOWS_ONLY
def test_verify_frozen_binary_rejects_a_bundle_that_cannot_start(tmp_path):
    with pytest.raises(SystemExit, match="failed to start"):
        build.verify_frozen_binary(make_bundle(tmp_path, 1))


def test_verify_frozen_binary_requires_a_produced_executable(tmp_path):
    with pytest.raises(SystemExit, match="was not produced"):
        build.verify_frozen_binary(tmp_path)


def test_build_verifies_the_bundle_before_archiving():
    source = Path(build.__file__).read_text(encoding="utf-8")
    assert source.index("verify_frozen_binary(DIST") < source.index("machine = normalize_arch")


def test_linux_build_keeps_setup_python_off_the_job():
    """setup-python exports its own libpython; mixing it with the Tk-enabled
    distribution Python is what broke the Linux bundle."""
    workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(
        encoding="utf-8"
    )
    # Three jobs set up Python; only the build job's step may be Linux-excluded.
    condition = workflow.index("if: runner.os != 'Linux'")
    step = workflow[condition:].split("- name:", 1)[0]
    assert "actions/setup-python@v5" in step
    # Linux must still get a Tk-enabled distribution interpreter.
    assert "if: runner.os == 'Linux'" in workflow
    assert "python3-tk" in workflow
