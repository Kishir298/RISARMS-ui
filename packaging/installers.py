"""Native installer builders for the RISARMS desktop application.

PyInstaller produces an application directory (``COLLECT``) and, on macOS, an
``.app`` bundle. Those are archives, not installers, so this module wraps the
platform packaging tools:

  macOS    hdiutil          -> .dmg
  Linux    dpkg-deb         -> .deb
  Linux    appimagetool     -> .AppImage
  Windows  Inno Setup (iscc) -> .exe installer
  Windows  WiX (candle/light) -> .msi installer

Every builder returns ``None`` with a printed reason when its tool is missing,
so a build never silently claims an artifact it did not produce. Signing
credentials are read from the environment elsewhere and are never written here.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def _run(command, cwd=None):
    print("+", " ".join(str(item) for item in command))
    subprocess.run(command, check=True, cwd=cwd)


def build_dmg(app: Path, version: str, output: Path) -> Path | None:
    """Create a compressed macOS disk image containing the .app bundle."""
    if sys.platform != "darwin":
        return None
    if shutil.which("hdiutil") is None:
        print("hdiutil is unavailable; skipping the DMG installer.")
        return None
    staging = output.parent / "dmg-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copytree(app, staging / app.name, symlinks=True)
    (staging / "Applications").symlink_to("/Applications")
    if output.exists():
        output.unlink()
    _run([
        "hdiutil", "create", "-volname", f"RISARMS Portal {version}",
        "-srcfolder", str(staging), "-ov", "-format", "UDZO", str(output),
    ])
    shutil.rmtree(staging, ignore_errors=True)
    return output if output.exists() else None


def build_deb(app_dir: Path, version: str, arch: str, output: Path) -> Path | None:
    """Create a Debian package that installs the application under /opt."""
    if shutil.which("dpkg-deb") is None:
        print("dpkg-deb is unavailable; skipping the DEB installer.")
        return None
    staging = output.parent / "deb-staging"
    if staging.exists():
        shutil.rmtree(staging)
    target = staging / "opt" / "risarms-portal"
    target.parent.mkdir(parents=True)
    shutil.copytree(app_dir, target)
    launcher = staging / "usr" / "bin" / "risarms-ui"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexec /opt/risarms-portal/risarms-ui \"$@\"\n", encoding="utf-8")
    launcher.chmod(0o755)
    desktop = staging / "usr" / "share" / "applications" / "risarms-portal.desktop"
    desktop.parent.mkdir(parents=True)
    desktop.write_text(
        "[Desktop Entry]\nType=Application\nName=RISARMS Portal\n"
        "Exec=/opt/risarms-portal/risarms-ui\nTerminal=false\nCategories=Utility;\n",
        encoding="utf-8",
    )
    control = staging / "DEBIAN" / "control"
    control.parent.mkdir(parents=True)
    control.write_text(
        "Package: risarms-portal\n"
        f"Version: {version}\n"
        f"Architecture: {arch}\n"
        "Maintainer: RISARMS\n"
        "Description: R.I.S.A.R.M.S. unified desktop application\n"
        "Depends: libx11-6, libtk8.6 | tk\n",
        encoding="utf-8",
    )
    if output.exists():
        output.unlink()
    _run(["dpkg-deb", "--build", "--root-owner-group", str(staging), str(output)])
    shutil.rmtree(staging, ignore_errors=True)
    return output if output.exists() else None


def build_appimage(app_dir: Path, version: str, arch: str, output: Path) -> Path | None:
    """Create an AppImage when appimagetool is available."""
    tool = shutil.which("appimagetool")
    if tool is None:
        print("appimagetool is unavailable; skipping the AppImage installer.")
        return None
    appdir = output.parent / "RISARMS-Portal.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    shutil.copytree(app_dir, appdir)
    (appdir / "AppRun").write_text(
        '#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\nexec "$HERE/risarms-ui" "$@"\n',
        encoding="utf-8",
    )
    (appdir / "AppRun").chmod(0o755)
    (appdir / "risarms-portal.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=RISARMS Portal\n"
        "Exec=risarms-ui\nIcon=risarms-portal\nTerminal=false\nCategories=Utility;\n",
        encoding="utf-8",
    )
    if output.exists():
        output.unlink()
    _run([tool, "--no-appstream", str(appdir), str(output)])
    shutil.rmtree(appdir, ignore_errors=True)
    return output if output.exists() else None


def build_windows_exe(app_dir: Path, version: str, output: Path) -> Path | None:
    """Create a Windows installer with Inno Setup when it is installed."""
    compiler = shutil.which("iscc") or shutil.which("ISCC")
    if compiler is None:
        print("Inno Setup (iscc) is unavailable; skipping the EXE installer.")
        return None
    script = output.parent / "risarms-portal.iss"
    script.write_text(
        "[Setup]\n"
        "AppName=RISARMS Portal\n"
        f"AppVersion={version}\n"
        "DefaultDirName={autopf}\\RISARMS Portal\n"
        "DefaultGroupName=RISARMS\n"
        "OutputBaseFilename=RISARMS-Portal-Setup\n"
        "Compression=lzma2\nSolidCompression=yes\n"
        "[Files]\n"
        f'Source: "{app_dir}\\*"; DestDir: "{{app}}"; Flags: recursesubdirs\n'
        "[Icons]\n"
        'Name: "{group}\\RISARMS Portal"; Filename: "{app}\\risarms-ui.exe"\n'
        'Name: "{autodesktop}\\RISARMS Portal"; Filename: "{app}\\risarms-ui.exe"; Tasks: desktopicon\n'
        "[Tasks]\n"
        'Name: "desktopicon"; Description: "Create a desktop shortcut"\n',
        encoding="utf-8",
    )
    _run([compiler, str(script)])
    produced = output.parent / "RISARMS-Portal-Setup.exe"
    if produced.exists() and produced != output:
        produced.replace(output)
    return output if output.exists() else None


def build_windows_msi(app_dir: Path, version: str, output: Path) -> Path | None:
    """Create an MSI with WiX when the toolchain is installed."""
    candle = shutil.which("candle")
    light = shutil.which("light")
    if not candle or not light:
        print("WiX (candle/light) is unavailable; skipping the MSI installer.")
        return None
    harvest = shutil.which("heat")
    if harvest is None:
        print("WiX heat is unavailable; skipping the MSI installer.")
        return None
    wxs = output.parent / "risarms-portal.wxs"
    _run([harvest, "dir", str(app_dir), "-cg", "AppFiles", "-dr", "INSTALLDIR",
          "-gg", "-g1", "-sf", "-srd", "-var", "var.SourceDir", "-out", str(wxs)])
    project = output.parent / "risarms-portal.wixproj.wxs"
    project.write_text(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<Wix xmlns=\"http://schemas.microsoft.com/wix/2006/wi\">\n"
        "<Product Id=\"*\" Name=\"RISARMS Portal\" Language=\"1033\" Version=\""
        + version + "\" Manufacturer=\"RISARMS\" UpgradeCode=\"7c7b6f8e-3f37-4a1e-9b3c-6f0d5a2e9c11\">\n"
        "<Package InstallerVersion=\"500\" Compressed=\"yes\" InstallScope=\"perMachine\"/>\n"
        # heat's components reference Media 1, which only the Product defines.
        "<Media Id=\"1\" Cabinet=\"product.cab\" EmbedCab=\"yes\"/>\n"
        "<Directory Id=\"TARGETDIR\" Name=\"SourceDir\"><Directory Id=\"ProgramFilesFolder\">\n"
        "<Directory Id=\"INSTALLDIR\" Name=\"RISARMS Portal\"/></Directory></Directory>\n"
        "<Feature Id=\"DefaultFeature\" Title=\"RISARMS Portal\" Level=\"1\">\n"
        "<ComponentGroupRef Id=\"AppFiles\"/></Feature>\n"
        "</Product>\n</Wix>\n",
        encoding="utf-8",
    )
    # ``heat`` produces a ComponentGroup fragment without a Product element,
    # so both the fragment and the authored project must be compiled and the
    # linker needs both objects.
    fragment_obj = output.parent / "risarms-portal-fragment.wixobj"
    project_obj = output.parent / "risarms-portal.wixobj"
    _run([candle, "-dSourceDir=" + str(app_dir), "-out", str(fragment_obj), str(wxs)])
    _run([candle, "-dSourceDir=" + str(app_dir), "-out", str(project_obj), str(project)])
    if output.exists():
        output.unlink()
    _run([light, "-out", str(output), str(project_obj), str(fragment_obj)])
    return output if output.exists() else None
