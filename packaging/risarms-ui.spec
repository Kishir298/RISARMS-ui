# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the RISARMS Portal desktop application."""
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
HIDDEN = [
    "websockets.sync.client",
    "keyring.backends",
    "keyring.backends.macOS",
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "keyring.backends.kwallet",
]
HIDDEN += collect_submodules("keyring.backends")

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT.parent)],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tkinter.test", "test"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="risarms-ui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="RISARMS-Portal",
)
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="RISARMS Portal.app",
        icon=None,
        bundle_identifier="org.risarms.portal",
        info_plist={
            "CFBundleDisplayName": "RISARMS Portal",
            "CFBundleShortVersionString": "0.6.0",
            "CFBundleVersion": "0.6.0",
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
        },
    )
