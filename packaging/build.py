"""Build unsigned desktop artifacts with optional signing hooks.

Environment hooks (all optional, never logged):
  RISARMS_SIGN_IDENTITY     macOS Developer ID identity for codesign
  RISARMS_NOTARY_PROFILE    xcrun notarytool keychain profile
  RISARMS_SIGN_CERT         Windows .pfx certificate path
  RISARMS_SIGN_PASSWORD     Windows certificate password
  RISARMS_TIMESTAMP_URL     Windows RFC3161 timestamp server
"""
from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

import embed_update_key  # noqa: E402  (local packaging helper)
import installers  # noqa: E402  (local packaging helper)

from risarms_ui import __version__  # noqa: E402
from risarms_ui.release import debian_arch, normalize_arch, platform_tag  # noqa: E402

DIST = ROOT / "dist"
VERSION = __version__


def run(*command, **kwargs):
    print("+", " ".join(str(item) for item in command))
    subprocess.run(command, check=True, cwd=ROOT, **kwargs)


def sign_macos(app: Path):
    identity = os.environ.get("RISARMS_SIGN_IDENTITY")
    if not identity:
        run("codesign", "--force", "--deep", "--sign", "-", str(app))
        print("Ad-hoc signed (no Developer ID configured).")
        return
    run("codesign", "--force", "--deep", "--options", "runtime", "--timestamp", "--sign", identity, str(app))
    print("Signed with the configured Developer ID.")


def notarize_macos(app: Path, archive: Path):
    profile = os.environ.get("RISARMS_NOTARY_PROFILE")
    if not profile:
        return
    run("xcrun", "notarytool", "submit", str(archive), "--keychain-profile", profile, "--wait")
    run("xcrun", "stapler", "staple", str(app))


def sign_windows(target: Path):
    certificate = os.environ.get("RISARMS_SIGN_CERT")
    if not certificate:
        print("Unsigned Windows build (no certificate configured).")
        return
    command = ["signtool", "sign", "/fd", "sha256", "/f", certificate]
    password = os.environ.get("RISARMS_SIGN_PASSWORD")
    if password:
        command += ["/p", password]
    timestamp = os.environ.get("RISARMS_TIMESTAMP_URL", "http://timestamp.digicert.com")
    command += ["/tr", timestamp, "/td", "sha256", str(target)]
    run(*command)


def archive(path: Path, tag: str) -> Path:
    suffix = "zip" if sys.platform in ("darwin", "win32") else "tar.gz"
    destination = DIST / f"RISARMS-Portal-{VERSION}-{tag}.{suffix}"
    if destination.exists():
        destination.unlink()
    if suffix == "zip":
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as bundle:
            if path.is_dir():
                for item in sorted(path.rglob("*")):
                    if item.is_file():
                        bundle.write(item, item.relative_to(path.parent))
            else:
                bundle.write(path, path.name)
    else:
        with tarfile.open(destination, "w:gz") as bundle:
            bundle.add(path, arcname=path.name)
    return destination


def native_installers(app_dir: Path, app_bundle: Path | None, machine: str) -> list:
    """Produce real platform installers; report what could not be built."""
    built = []
    if sys.platform == "darwin" and app_bundle is not None:
        built.append(installers.build_dmg(
            app_bundle, VERSION, DIST / f"RISARMS-Portal-{VERSION}-macos-{machine}.dmg"))
    elif sys.platform == "win32":
        built.append(installers.build_windows_exe(
            app_dir, VERSION, DIST / f"RISARMS-Portal-{VERSION}-windows-{machine}-setup.exe"))
        built.append(installers.build_windows_msi(
            app_dir, VERSION, DIST / f"RISARMS-Portal-{VERSION}-windows-{machine}.msi"))
    else:
        arch = debian_arch(machine)
        built.append(installers.build_deb(
            app_dir, VERSION, arch, DIST / f"risarms-portal_{VERSION}_{arch}.deb"))
        built.append(installers.build_appimage(
            app_dir, VERSION, machine, DIST / f"RISARMS-Portal-{VERSION}-linux-{machine}.AppImage"))
    produced = [item for item in built if item is not None]
    skipped = len(built) - len(produced)
    if skipped:
        print(f"{skipped} native installer(s) skipped: the packaging tool is not installed.")
    return produced


def main() -> int:
    embed_update_key.embed()
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is required: pip install -e '.[package]'", file=sys.stderr)
        return 2
    try:
        import tkinter  # noqa: F401
    except ImportError:
        # Never ship a bundle that cannot open its own window.
        print(
            "Tk is required to package the desktop application. On macOS install "
            "python-tk for your Python, on Debian/Ubuntu install python3-tk. "
            "No artifact was produced.",
            file=sys.stderr,
        )
        return 2
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(ROOT / "packaging" / "risarms-ui.spec"))
    machine = normalize_arch(platform.machine())
    tag = platform_tag(sys.platform, machine)
    if sys.platform == "darwin":
        app = DIST / "RISARMS Portal.app"
        if not app.exists():
            raise SystemExit("Expected macOS application bundle was not produced.")
        sign_macos(app)
        destination = archive(app, tag)
        notarize_macos(app, destination)
    elif sys.platform == "win32":
        target = DIST / "RISARMS-Portal"
        if not target.exists():
            raise SystemExit("Expected Windows application directory was not produced.")
        sign_windows(target / "risarms-ui.exe")
        destination = archive(target, tag)
    else:
        target = DIST / "RISARMS-Portal"
        if not target.exists():
            raise SystemExit("Expected Linux application directory was not produced.")
        destination = archive(target, tag)
    app_bundle = DIST / "RISARMS Portal.app" if sys.platform == "darwin" else None
    app_dir = DIST / "RISARMS-Portal"
    artifacts = [destination] + native_installers(app_dir, app_bundle, machine)
    sums = DIST / f"SHA256SUMS-{tag}"
    # newline="\n" keeps the checksum file LF-only on Windows so it verifies
    # with sha256sum -c on Linux and macOS.
    sums.write_text("", encoding="utf-8", newline="\n")
    for artifact in artifacts:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        with sums.open("a", encoding="utf-8", newline="\n") as file:
            file.write(f"{digest}  {artifact.name}\n")
        print(f"Artifact: {artifact}")
        print(f"SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
