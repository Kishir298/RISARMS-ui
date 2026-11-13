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
DIST = ROOT / "dist"
VERSION = "0.6.0"


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


def archive(path: Path, platform_tag: str) -> Path:
    suffix = "zip" if sys.platform in ("darwin", "win32") else "tar.gz"
    destination = DIST / f"RISARMS-Portal-{VERSION}-{platform_tag}.{suffix}"
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


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is required: pip install -e '.[package]'", file=sys.stderr)
        return 2
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(ROOT / "packaging" / "risarms-ui.spec"))
    machine = platform.machine().lower() or "unknown"
    if sys.platform == "darwin":
        app = DIST / "RISARMS Portal.app"
        if not app.exists():
            raise SystemExit("Expected macOS application bundle was not produced.")
        sign_macos(app)
        destination = archive(app, f"macos-{machine}")
        notarize_macos(app, destination)
    elif sys.platform == "win32":
        target = DIST / "RISARMS-Portal"
        if not target.exists():
            raise SystemExit("Expected Windows application directory was not produced.")
        sign_windows(target / "risarms-ui.exe")
        destination = archive(target, f"windows-{machine}")
    else:
        target = DIST / "RISARMS-Portal"
        if not target.exists():
            raise SystemExit("Expected Linux application directory was not produced.")
        destination = archive(target, f"linux-{machine}")
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    sums = DIST / "SHA256SUMS"
    with sums.open("a", encoding="utf-8") as file:
        file.write(f"{digest}  {destination.name}\n")
    print(f"Artifact: {destination}")
    print(f"SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
