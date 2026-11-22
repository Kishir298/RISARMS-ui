"""Resolve the Ed25519 update public key a desktop build embeds.

Production builds require ``UPDATE_PUBLIC_KEY`` on the repository. A validation
build (``RISARMS_VALIDATION_BUILD=1``) generates an ephemeral key pair so the
installers can be exercised end to end; the private key is discarded, the
artifacts are marked as non-publishable, and no signed update manifest is
produced for them.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from risarms_ui.updates import UpdateError, validate_public_key  # noqa: E402

ENV_KEY = "UPDATE_PUBLIC_KEY"
FINGERPRINT_ENV = "RISARMS_UPDATE_KEY_FINGERPRINT"


def fingerprint(canonical_b64: str) -> str:
    """SHA-256 of the raw 32-byte key, for comparing build configurations."""
    return hashlib.sha256(base64.b64decode(canonical_b64)).hexdigest()


def generate_key() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    raw = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")


def resolve(environment: dict[str, str] | None = None) -> tuple[str, str, bool]:
    """Return ``(canonical_key, fingerprint, is_validation)``."""
    env = os.environ if environment is None else environment
    provided = (env.get(ENV_KEY) or "").strip()
    validation = (env.get("RISARMS_VALIDATION_BUILD") or "") == "1"

    if provided:
        try:
            canonical = validate_public_key(provided)
        except UpdateError as exc:
            raise SystemExit(f"{ENV_KEY} is invalid: {exc}") from exc
        return canonical, fingerprint(canonical), False
    if not validation:
        raise SystemExit(
            f"{ENV_KEY} is required for a production desktop build. Configure it "
            "on the repository, or dispatch desktop-release with "
            "validation_build=true to produce clearly marked, non-publishable "
            "artifacts."
        )
    canonical = generate_key()
    return canonical, fingerprint(canonical), True


def _append(path: str | None, line: str) -> None:
    if path:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def main() -> int:
    canonical, digest, validation = resolve()
    print(f"fingerprint={digest}")
    print(f"validation_build={'true' if validation else 'false'}")
    _append(os.environ.get("GITHUB_ENV"), f"{ENV_KEY}={canonical}")
    _append(os.environ.get("GITHUB_ENV"), f"{FINGERPRINT_ENV}={digest}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        label = (
            "ephemeral validation key"
            if validation
            else "repository production key"
        )
        _append(
            summary,
            f"- Update public key: `{digest}` ({label}). "
            "This is the Ed25519 update-manifest key, not Apple/Windows OS "
            "code signing.",
        )
    if validation:
        print(
            "Ephemeral update key generated: these artifacts are validation-only "
            "and must not be published.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
