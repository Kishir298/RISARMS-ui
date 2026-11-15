"""Signed update manifests reject tampering, wrong products and bad artifacts."""
from __future__ import annotations

import base64
import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from risarms_ui import updates


def signed(payload, private):
    body = dict(payload)
    body.pop("signature", None)
    body["signature"] = base64.b64encode(private.sign(updates._canonical(body))).decode("ascii")
    return body


def manifest(**overrides):
    payload = {
        "schema": 1,
        "product": "RISARMS-UI",
        "version": "0.7.0",
        "channel": "stable",
        "min_version": "0.6.0",
        "artifacts": [{
            "name": "risarms-ui.zip",
            "url": "https://github.com/Kishir298/RISARMS-ui/releases/download/v0.7.0/risarms-ui.zip",
            "sha256": "a" * 64,
            "size": 10,
        }],
    }
    payload.update(overrides)
    return payload


def keypair():
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(private.public_key().public_bytes_raw()).decode("ascii")
    return private, public


def test_valid_manifest_and_update_detection():
    private, public = keypair()
    update = updates.verify_manifest(signed(manifest(), private), public)
    assert update.version == "0.7.0"
    assert update.artifacts[0].name == "risarms-ui.zip"
    assert updates.update_available("0.6.0", update) is True
    assert updates.update_available("0.7.0", update) is False
    assert updates.update_available("0.5.0", update) is False


def test_tampering_wrong_product_and_channel_are_rejected():
    private, public = keypair()
    payload = signed(manifest(), private)
    payload["version"] = "9.9.9"
    with pytest.raises(updates.UpdateError, match="signature"):
        updates.verify_manifest(payload, public)
    with pytest.raises(updates.UpdateError, match="product"):
        updates.verify_manifest(signed(manifest(product="OTHER"), private), public)
    with pytest.raises(updates.UpdateError, match="channel"):
        updates.verify_manifest(signed(manifest(channel="beta"), private), public, channel="stable")


def test_insecure_and_bad_artifacts_are_rejected():
    private, public = keypair()
    with pytest.raises(updates.UpdateError, match="HTTPS"):
        updates.verify_manifest(signed(manifest(artifacts=[{
            "name": "x", "url": "http://example.com/x", "sha256": "a" * 64, "size": 1,
        }]), private), public)
    with pytest.raises(updates.UpdateError, match="hash"):
        updates.verify_manifest(signed(manifest(artifacts=[{
            "name": "x", "url": "https://example.com/x", "sha256": "short", "size": 1,
        }]), private), public)


def test_artifact_download_checks_hash(tmp_path, monkeypatch):
    body = b"release payload"
    artifact = updates.Artifact("x.zip", "https://example.com/x.zip", hashlib.sha256(body).hexdigest(), len(body))
    source = tmp_path / "source"
    source.write_bytes(body)

    class Response:
        def __enter__(self):
            self.stream = source.open("rb")
            return self

        def __exit__(self, *_args):
            self.stream.close()
            return False

        def read(self, size):
            return self.stream.read(size)

    monkeypatch.setattr(updates, "urlopen", lambda *_args, **_kwargs: Response())
    destination = tmp_path / "download.zip"
    assert updates.download_artifact(artifact, destination) == destination
    assert destination.read_bytes() == body


def test_artifact_download_rejects_hash_mismatch(tmp_path, monkeypatch):
    artifact = updates.Artifact("x.zip", "https://example.com/x.zip", "0" * 64, 3)
    source = tmp_path / "source"
    source.write_bytes(b"bad")

    class Response:
        def __enter__(self):
            self.stream = source.open("rb")
            return self

        def __exit__(self, *_args):
            self.stream.close()
            return False

        def read(self, size):
            return self.stream.read(size)

    monkeypatch.setattr(updates, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(updates.UpdateError, match="hash"):
        updates.download_artifact(artifact, tmp_path / "download.zip")
