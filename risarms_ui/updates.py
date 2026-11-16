"""Ed25519-verified GitHub Releases update manifests and artifacts."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .release import version_key

MAX_MANIFEST = 1024 * 1024
MAX_ARTIFACT = 4 * 1024 * 1024 * 1024
INSTALLER_SUFFIXES = {
    "darwin": (".dmg", ".pkg", ".zip"),
    "win32": (".exe", ".msi", ".zip"),
    "linux": (".deb", ".appimage", ".tar.gz", ".zip"),
}


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Artifact:
    name: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Update:
    product: str
    version: str
    channel: str
    min_version: str
    artifacts: tuple[Artifact, ...]


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_url(url):
    parsed = urlparse(str(url))
    if parsed.scheme == "https" and parsed.hostname:
        return url
    if parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "::1", "localhost"):
        return url
    raise UpdateError("Update URLs must use HTTPS (loopback HTTP is allowed for tests).")


def _version(value):
    if not isinstance(value, str) or not value.strip():
        raise UpdateError("Update version is missing.")
    try:
        return version_key(value)
    except ValueError as exc:
        raise UpdateError("Update version is not numeric.") from exc


def verify_manifest(payload, public_key, product="RISARMS-UI", channel="stable"):
    if not isinstance(payload, dict):
        raise UpdateError("Update manifest is invalid.")
    signature = payload.get("signature")
    if not isinstance(signature, str) or not signature:
        raise UpdateError("Update manifest is not signed.")
    body = {key: value for key, value in payload.items() if key != "signature"}
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
        key.verify(base64.b64decode(signature, validate=True), _canonical(body))
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise UpdateError("Update signature verification failed.") from exc
    if body.get("schema") != 1:
        raise UpdateError("Unsupported update manifest schema.")
    if body.get("product") != product:
        raise UpdateError("Update manifest is for a different product.")
    if body.get("channel") != channel:
        raise UpdateError("Update manifest is for a different channel.")
    artifacts = body.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise UpdateError("Update manifest has no artifacts.")
    result = []
    for item in artifacts:
        if not isinstance(item, dict):
            raise UpdateError("Update artifact is invalid.")
        digest, size = item.get("sha256"), item.get("size")
        if not isinstance(digest, str) or len(digest) != 64:
            raise UpdateError("Update artifact hash is invalid.")
        if not isinstance(size, int) or not 0 < size <= MAX_ARTIFACT:
            raise UpdateError("Update artifact size is invalid.")
        result.append(Artifact(str(item.get("name", "")), _safe_url(item.get("url")), digest.lower(), size))
    return Update(
        product=body["product"], version=str(body["version"]), channel=body["channel"],
        min_version=str(body.get("min_version", "0.0.0")), artifacts=tuple(result),
    )


def fetch_manifest(url, public_key, product="RISARMS-UI", channel="stable"):
    _safe_url(url)
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "RISARMS-UI updater"})
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read(MAX_MANIFEST + 1)
    except (HTTPError, URLError, OSError) as exc:
        raise UpdateError("Update manifest could not be fetched.") from exc
    if len(raw) > MAX_MANIFEST:
        raise UpdateError("Update manifest is too large.")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise UpdateError("Update manifest is not valid JSON.") from exc
    return verify_manifest(payload, public_key, product, channel)


def update_available(current, update):
    return _version(update.version) > _version(current) and _version(current) >= _version(update.min_version)


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_artifact(artifact, destination):
    _safe_url(artifact.url)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(artifact.url, headers={"User-Agent": "RISARMS-UI updater"})
    digest = hashlib.sha256()
    total = 0
    try:
        with urlopen(request, timeout=60) as response, tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=destination.name + ".", delete=False
        ) as stream:
            temp = Path(stream.name)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > artifact.size or total > MAX_ARTIFACT:
                    raise UpdateError("Update artifact exceeded its declared size.")
                digest.update(chunk)
                stream.write(chunk)
    except (HTTPError, URLError, OSError) as exc:
        raise UpdateError("Update artifact could not be downloaded.") from exc
    if total != artifact.size or digest.hexdigest() != artifact.sha256:
        temp.unlink(missing_ok=True)
        raise UpdateError("Update artifact hash or size did not match the signed manifest.")
    os.replace(temp, destination)
    return destination


def bundled_public_key():
    """Return the public key shipped next to this module, if any."""
    path = Path(__file__).with_name("update_public_key.txt")
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def validate_public_key(value):
    """Return canonical base64 for a 32-byte Ed25519 public key."""
    text = str(value or "").strip()
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as exc:
        raise UpdateError("The Ed25519 public key is not valid base64.") from exc
    if len(raw) != 32:
        raise UpdateError("The Ed25519 public key must contain 32 bytes.")
    return base64.b64encode(raw).decode("ascii")


def resolve_public_key(override=""):
    """Prefer the packaged key, then an environment key, then a dev override."""
    bundled = bundled_public_key()
    if bundled and getattr(sys, "frozen", False):
        return bundled
    return (
        os.environ.get("RISARMS_UPDATE_PUBLIC_KEY", "").strip()
        or bundled
        or str(override or "").strip()
    )


def select_artifact(update, platform_name=None, machine=None):
    """Choose a native installer for this platform, never a traversal name."""
    platform_name = platform_name or ("darwin" if sys.platform == "darwin" else "win32" if sys.platform == "win32" else "linux")
    suffixes = INSTALLER_SUFFIXES.get(platform_name)
    if not suffixes:
        raise UpdateError("This platform has no supported update installer.")
    candidates = []
    for artifact in update.artifacts:
        name = artifact.name
        if not name or name in (".", "..") or "/" in name or "\\" in name or Path(name).name != name:
            raise UpdateError("Update artifact name is unsafe.")
        lowered = name.lower()
        for index, suffix in enumerate(suffixes):
            if lowered.endswith(suffix):
                candidates.append((index, artifact))
                break
    if not candidates:
        raise UpdateError("This release has no installer for this platform.")
    candidates.sort(key=lambda item: item[0])
    if machine:
        aliases = {
            "amd64": ("amd64", "x86_64", "x64"),
            "x86_64": ("amd64", "x86_64", "x64"),
            "arm64": ("arm64", "aarch64"),
            "aarch64": ("arm64", "aarch64"),
        }.get(str(machine).lower(), (str(machine).lower(),))
        preferred = [
            item for item in candidates
            if any(alias in item[1].name.lower() for alias in aliases)
        ]
        if preferred:
            return preferred[0][1]
    return candidates[0][1]


def download_update(update, directory, platform_name=None, machine=None):
    """Download one signed installer into a private staging directory."""
    artifact = select_artifact(update, platform_name, machine)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    destination = directory / artifact.name
    if destination.is_symlink():
        destination.unlink()
    if destination.exists():
        if destination.stat().st_size == artifact.size and _file_sha256(destination) == artifact.sha256:
            return destination
        destination.unlink()
    return download_artifact(artifact, destination)


def installer_command(path, platform_name=None):
    """Return the OS command used to hand a verified artifact to the user."""
    path = Path(path).resolve()
    if not path.is_file():
        raise UpdateError("The verified update artifact is missing.")
    platform_name = platform_name or ("darwin" if sys.platform == "darwin" else "win32" if sys.platform == "win32" else "linux")
    if platform_name == "darwin":
        return ["open", str(path)]
    if platform_name == "win32":
        return [str(path)]
    if path.suffix.lower() == ".appimage":
        try:
            path.chmod(0o700)
        except OSError as exc:
            raise UpdateError("The AppImage could not be made executable.") from exc
        return [str(path)]
    return ["xdg-open", str(path)]


def launch_installer(path, platform_name=None):
    """Open the verified installer; the user still completes installation."""
    command = installer_command(path, platform_name)
    if (platform_name or sys.platform) == "win32":
        try:
            os.startfile(command[0])
        except OSError as exc:
            raise UpdateError("The update installer could not be opened.") from exc
        return command[0]
    try:
        subprocess.Popen(command)
    except OSError as exc:
        raise UpdateError("The update installer could not be opened.") from exc
    return command[0]
