"""Embed the production Ed25519 update public key into the source build."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from risarms_ui.updates import UpdateError, validate_public_key  # noqa: E402

KEY_FILE = Path(__file__).resolve().parents[1] / "risarms_ui" / "update_public_key.txt"


def embed(value=None, path=KEY_FILE):
    key = os.environ.get("UPDATE_PUBLIC_KEY", "") if value is None else value
    try:
        canonical = validate_public_key(key)
    except UpdateError as exc:
        raise SystemExit(f"UPDATE_PUBLIC_KEY is required: {exc}") from exc
    path = Path(path)
    path.write_text(canonical + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    print(embed())
