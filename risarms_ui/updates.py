"""Ed25519-verified GitHub Releases update manifests and artifacts."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MAX_MANIFEST = 1024 * 1024
MAX_ARTIFACT = 4 * 1024 * 1024 * 1024


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
    parts = value.strip().lstrip("v").split(".")
    if not all(part.isdigit() for part in parts[:3]):
        raise UpdateError("Update version is not numeric.")
    return tuple(int(part) for part in (parts + ["0", "0", "0"])[:3])


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


def download_artifact(artifact, destination):
    destination = Path(destination)
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
