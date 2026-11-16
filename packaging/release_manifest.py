"""Build and Ed25519-sign the GitHub Releases update manifest."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from risarms_ui import __version__  # noqa: E402

PRODUCT = "RISARMS-UI"
AUXILIARY_PREFIXES = ("SHA256SUMS", "sbom")


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def release_artifacts(dist: Path) -> list[Path]:
    """Return distributable artifacts, excluding checksums, SBOMs and manifests."""
    artifacts = []
    for path in sorted(Path(dist).iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if name == "update.json" or name.startswith(AUXILIARY_PREFIXES):
            continue
        if name.endswith(".cdx.json"):
            continue
        artifacts.append(path)
    return artifacts


def build_payload(dist, version, repository, tag, min_version, channel, private):
    artifacts = []
    for path in release_artifacts(Path(dist)):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        artifacts.append({
            "name": path.name,
            "url": f"https://github.com/{repository}/releases/download/{tag}/{path.name}",
            "sha256": digest,
            "size": path.stat().st_size,
        })
    if not artifacts:
        raise SystemExit("No release artifacts found.")
    payload = {
        "schema": 1,
        "product": PRODUCT,
        "version": version,
        "channel": channel,
        "min_version": min_version,
        "artifacts": artifacts,
    }
    payload["signature"] = base64.b64encode(private.sign(canonical(payload))).decode("ascii")
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--min-version", default=__version__)
    parser.add_argument("--channel", choices=("stable", "beta"), default="stable")
    args = parser.parse_args()
    key_text = os.environ.get("UPDATE_SIGNING_KEY", "").strip()
    if not key_text:
        raise SystemExit("UPDATE_SIGNING_KEY is required; refusing to publish an unsigned update.")
    private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(key_text, validate=True))
    payload = build_payload(
        args.dist, args.version, args.repository, args.tag,
        args.min_version, args.channel, private,
    )
    (args.dist / "update.json").write_text(json.dumps(payload, indent=2) + "\n")


if __name__ == "__main__":
    main()
