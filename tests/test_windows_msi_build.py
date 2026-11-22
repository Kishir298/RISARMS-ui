"""The WiX MSI build must compile both the harvested fragment and the Product."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_installers():
    spec = importlib.util.spec_from_file_location(
        "risarms_packaging_installers", ROOT / "packaging" / "installers.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_exe_writes_beside_the_artifacts(tmp_path, monkeypatch):
    """Inno Setup 6 defaults to dist/Output; the build must pin OutputDir."""
    installers = load_installers()
    monkeypatch.setattr(
        installers.shutil,
        "which",
        lambda name: "/fake/iscc" if name.lower() == "iscc" else None,
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "risarms-ui.exe").write_bytes(b"exe")
    output = tmp_path / "RISARMS-Portal-0.6.0-windows-x86_64-setup.exe"
    scripts: list[str] = []

    def fake_run(command, cwd=None):
        script = Path(command[-1])
        scripts.append(script.read_text(encoding="utf-8"))
        produced = script.parent / "RISARMS-Portal-Setup.exe"
        produced.write_bytes(b"setup")

    monkeypatch.setattr(installers, "_run", fake_run)

    assert installers.build_windows_exe(app_dir, "0.6.0", output) == output
    assert output.read_bytes() == b"setup"
    assert f"OutputDir={output.parent}" in scripts[0]


def test_windows_msi_links_product_and_fragment(tmp_path, monkeypatch):
    installers = load_installers()
    tools = {name: f"/fake/{name}" for name in ("candle", "light", "heat")}
    monkeypatch.setattr(installers.shutil, "which", lambda name: tools.get(name))

    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "risarms-ui.exe").write_bytes(b"exe")
    output = tmp_path / "RISARMS-Portal-0.6.0-windows-x86_64.msi"
    commands: list[list[str]] = []

    def fake_run(command, cwd=None):
        commands.append(list(command))
        if Path(command[0]).name == "light":
            output.write_bytes(b"msi")

    monkeypatch.setattr(installers, "_run", fake_run)

    assert installers.build_windows_msi(app_dir, "0.6.0", output) == output

    candle_calls = [c for c in commands if Path(c[0]).name == "candle"]
    assert len(candle_calls) == 2, "both the fragment and the Product must compile"
    assert any(c[-1].endswith("risarms-portal.wxs") for c in candle_calls)
    assert any(c[-1].endswith("wixproj.wxs") for c in candle_calls)

    light_call = next(c for c in commands if Path(c[0]).name == "light")
    objects = [Path(item) for item in light_call[3:]]
    assert len(objects) == 2
    assert {item.name for item in objects} == {
        "risarms-portal.wixobj",
        "risarms-portal-fragment.wixobj",
    }

    # The harvested components reference Media 1; only the Product can define
    # it, otherwise light fails with LGHT0094.
    project = (tmp_path / "risarms-portal.wixproj.wxs").read_text(encoding="utf-8")
    assert '<Media Id="1"' in project
    assert "<Product " in project
