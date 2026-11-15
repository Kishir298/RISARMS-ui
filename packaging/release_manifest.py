"""Build and Ed25519-sign the GitHub Releases update manifest."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PRODUCT = "RISARMS-UI"


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--min-version", default="0.6.0")
    parser.add_argument("--channel", choices=("stable", "beta"), default="stable")
    args = parser.parse_args()
    key_text = os.environ.get("UPDATE_SIGNING_KEY", "").strip()
    if not key_text:
        raise SystemExit("UPDATE_SIGNING_KEY is required; refusing to publish an unsigned update.")
    private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(key_text, validate=True))
    artifacts, sums = [], []
    for path in sorted(args.dist.iterdir()):
        if not path.is_file() or path.name in ("update.json", "SHA256SUMS"):
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        sums.append(f"{digest}  {path.name}")
        artifacts.append({
            "name": path.name,
            "url": f"https://github.com/{args.repository}/releases/download/{args.tag}/{path.name}",
            "sha256": digest,
            "size": path.stat().st_size,
        })
    if not artifacts:
        raise SystemExit("No release artifacts found.")
    payload = {
        "schema": 1,
        "product": PRODUCT,
        "version": args.version,
        "channel": args.channel,
        "min_version": args.min_version,
        "artifacts": artifacts,
    }
    payload["signature"] = base64.b64encode(private.sign(canonical(payload))).decode("ascii")
    (args.dist / "update.json").write_text(json.dumps(payload, indent=2) + "\n")
    (args.dist / "SHA256SUMS").write_text("\n".join(sums) + "\n")


if __name__ == "__main__":
    main()
