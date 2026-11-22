"""Signed update manifests reject tampering, wrong products and bad artifacts."""
from __future__ import annotations

import base64
import hashlib
import os

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


def test_beta_manifest_versions_are_ordered_and_updates_detected():
    private, public = keypair()
    update = updates.verify_manifest(
        signed(
            manifest(version="0.6.0-beta.2", channel="beta", min_version="0.6.0-beta.0"),
            private,
        ),
        public,
        channel="beta",
    )
    assert update.version == "0.6.0-beta.2"
    assert updates.update_available("0.6.0-beta.1", update) is True
    assert updates.update_available("0.6.0-beta.2", update) is False
    assert updates.update_available("0.6.0", update) is False


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


def update_with(*names):
    return updates.Update(
        "RISARMS-UI", "0.7.0", "stable", "0.6.0",
        tuple(updates.Artifact(name, "https://example.com/" + name, "a" * 64, 1) for name in names),
    )


def test_installer_selection_is_platform_and_architecture_aware():
    release = update_with(
        "RISARMS-Portal-linux-amd64.deb", "RISARMS-Portal-linux-arm64.deb",
        "RISARMS-Portal-macos-arm64.dmg", "RISARMS-Portal-windows-x64-setup.exe",
    )
    assert updates.select_artifact(release, "darwin", "arm64").name.endswith(".dmg")
    assert updates.select_artifact(release, "win32", "amd64").name.endswith(".exe")
    assert updates.select_artifact(release, "linux", "aarch64").name.endswith("arm64.deb")
    assert updates.select_artifact(release, "linux", "x86_64").name.endswith("amd64.deb")
    with pytest.raises(updates.UpdateError, match="unsafe"):
        updates.select_artifact(update_with("../evil.dmg"), "darwin")


def test_installer_command_does_not_use_a_shell(tmp_path):
    package = tmp_path / "RISARMS-Portal.deb"
    package.write_bytes(b"package")
    assert updates.installer_command(package, "linux") == ["xdg-open", str(package.resolve())]
    assert updates.installer_command(package, "darwin") == ["open", str(package.resolve())]
    appimage = tmp_path / "RISARMS-Portal.AppImage"
    appimage.write_bytes(b"binary")
    assert updates.installer_command(appimage, "linux") == [str(appimage.resolve())]
    if os.name == "posix":
        # Execute permission is a POSIX concept; Windows has no such mode bit.
        assert appimage.stat().st_mode & 0o100


def test_public_key_prefers_environment_and_bundle_over_override(monkeypatch):
    monkeypatch.delenv("RISARMS_UPDATE_PUBLIC_KEY", raising=False)
    monkeypatch.setattr(updates, "bundled_public_key", lambda: "bundled-key")
    assert updates.resolve_public_key("local-key") == "bundled-key"
    monkeypatch.setenv("RISARMS_UPDATE_PUBLIC_KEY", "environment-key")
    assert updates.resolve_public_key("local-key") == "environment-key"
    monkeypatch.delenv("RISARMS_UPDATE_PUBLIC_KEY")
    monkeypatch.setattr(updates, "bundled_public_key", lambda: "")
    assert updates.resolve_public_key("local-key") == "local-key"


def test_download_update_selects_installer_and_uses_private_directory(tmp_path, monkeypatch):
    body = b"deb payload"
    artifact = updates.Artifact(
        "risarms-portal_0.7.0_amd64.deb", "https://example.com/risarms.deb",
        hashlib.sha256(body).hexdigest(), len(body),
    )
    release = updates.Update("RISARMS-UI", "0.7.0", "stable", "0.6.0", (artifact,))

    class Response:
        def __enter__(self):
            self.stream = __import__("io").BytesIO(body)
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size):
            return self.stream.read(size)

    monkeypatch.setattr(updates, "urlopen", lambda *_args, **_kwargs: Response())
    path = updates.download_update(release, tmp_path / "updates", "linux", "x86_64")
    assert path.name == artifact.name
    assert path.read_bytes() == body
    assert (tmp_path / "updates").stat().st_mode & 0o700 == 0o700


def test_public_key_validation_rejects_bad_material():
    private, public = keypair()
    assert updates.validate_public_key(public) == public
    with pytest.raises(updates.UpdateError, match="base64"):
        updates.validate_public_key("not-base64")
    with pytest.raises(updates.UpdateError, match="32 bytes"):
        updates.validate_public_key(base64.b64encode(b"short").decode("ascii"))


def test_frozen_bundle_key_cannot_be_overridden_by_environment(monkeypatch):
    monkeypatch.setattr(updates, "bundled_public_key", lambda: "bundled-key")
    monkeypatch.setattr(updates.sys, "frozen", True, raising=False)
    monkeypatch.setenv("RISARMS_UPDATE_PUBLIC_KEY", "environment-key")
    assert updates.resolve_public_key("local-key") == "bundled-key"
