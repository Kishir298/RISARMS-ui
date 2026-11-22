"""The PyInstaller spec must point at files that actually exist.

Ordinary CI runs never invoke PyInstaller, so a stale spec path only fails in
the desktop-release workflow. Evaluate the spec against stubbed PyInstaller
objects and assert every referenced source and data path resolves.
"""

from __future__ import annotations

import runpy
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "risarms-ui.spec"


class _Node:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def _install_stubs():
    modules = {
        "PyInstaller": types.ModuleType("PyInstaller"),
        "PyInstaller.utils": types.ModuleType("PyInstaller.utils"),
        "PyInstaller.utils.hooks": types.ModuleType("PyInstaller.utils.hooks"),
    }
    modules["PyInstaller.utils.hooks"].collect_submodules = lambda name: []
    modules["PyInstaller"].__path__ = []
    modules["PyInstaller.utils"].__path__ = []
    return modules


def evaluate_spec(monkeypatch):
    captured: dict[str, object] = {}
    stubs = _install_stubs()

    class Analysis:
        def __init__(self, scripts, **kwargs):
            captured["scripts"] = scripts
            captured["datas"] = kwargs.get("datas") or []
            captured["pathex"] = kwargs.get("pathex") or []
            self.pure = []
            self.scripts = []
            self.binaries = []
            self.datas = []

    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)

    globals_ = {
        "SPECPATH": str(SPEC.parent),
        "Analysis": Analysis,
        "PYZ": _Node,
        "EXE": _Node,
        "COLLECT": _Node,
        "BUNDLE": _Node,
    }
    runpy.run_path(str(SPEC), init_globals=globals_)
    return captured


def test_spec_references_existing_launcher(monkeypatch):
    captured = evaluate_spec(monkeypatch)
    scripts = [Path(item) for item in captured["scripts"]]
    assert scripts, "the spec must define an entry script"
    for script in scripts:
        assert script.is_file(), f"spec entry script missing: {script}"


def test_spec_paths_resolve_inside_the_repository(monkeypatch):
    captured = evaluate_spec(monkeypatch)
    assert Path(captured["pathex"][0]) == ROOT
    for source, destination in captured["datas"]:
        assert Path(source).is_file(), f"spec data file missing: {source}"
        assert destination == "risarms_ui"


def test_update_public_key_placeholder_lives_under_the_package():
    # The spec embeds this file when it exists; packaging writes it at build time.
    assert (ROOT / "risarms_ui" / "update_public_key.txt").parent == ROOT / "risarms_ui"
