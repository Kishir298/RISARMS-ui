"""Release tags, version ordering and packaging metadata stay consistent."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from risarms_ui import __version__, release, updates

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("ref", "version", "base", "channel", "prerelease"),
    [
        ("v0.6.0", "0.6.0", "0.6.0", "stable", False),
        ("0.6.0", "0.6.0", "0.6.0", "stable", False),
        ("v0.6.0-beta.1", "0.6.0-beta.1", "0.6.0", "beta", True),
        ("v0.6.0-beta.12", "0.6.0-beta.12", "0.6.0", "beta", True),
    ],
)
def test_release_ref_parsing(ref, version, base, channel, prerelease):
    target = release.parse_release_ref(ref)
    assert (target.version, target.base_version, target.channel, target.prerelease) == (
        version, base, channel, prerelease,
    )


@pytest.mark.parametrize("ref", ["v0.6", "v0.6.0-rc.1", "v0.6.0-beta", "release-1", ""])
def test_unsupported_release_refs_are_rejected(ref):
    with pytest.raises(ValueError, match="Unsupported release tag"):
        release.parse_release_ref(ref)


def test_version_ordering_handles_beta_suffixes():
    assert release.version_key("0.6.0-beta.2") > release.version_key("0.6.0-beta.1")
    assert release.version_key("0.6.0") > release.version_key("0.6.0-beta.2")
    assert release.version_key("0.7.0") > release.version_key("0.6.9")
    with pytest.raises(ValueError):
        release.version_key("not-a-version")


@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        ("AMD64", "x86_64"),
        ("amd64", "x86_64"),
        ("x86_64", "x86_64"),
        ("x64", "x86_64"),
        ("ARM64", "arm64"),
        ("aarch64", "arm64"),
        ("arm64", "arm64"),
        ("riscv64", "riscv64"),
    ],
)
def test_architecture_normalization(machine, expected):
    assert release.normalize_arch(machine) == expected


def test_platform_and_debian_architecture_names():
    assert release.platform_tag("darwin", "arm64") == "macos-arm64"
    assert release.platform_tag("win32", "AMD64") == "windows-x86_64"
    assert release.platform_tag("linux", "x86_64") == "linux-x86_64"
    assert release.debian_arch("x86_64") == "amd64"
    assert release.debian_arch("aarch64") == "arm64"


def test_project_versions_and_build_script_are_consistent():
    result = subprocess.run(
        [sys.executable, str(ROOT / "packaging" / "validate_release.py"), "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    beta_ref = f"v{__version__}-beta.1"
    result = subprocess.run(
        [sys.executable, str(ROOT / "packaging" / "validate_release.py"), "--ref", beta_ref, "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run(
        [sys.executable, str(ROOT / "packaging" / "validate_release.py"), "--ref", "v99.99.99", "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode != 0


def test_validate_release_emits_beta_metadata_outputs(tmp_path):
    output = tmp_path / "github-output"
    ref = f"v{__version__}-beta.2"
    result = subprocess.run(
        [
            sys.executable, str(ROOT / "packaging" / "validate_release.py"),
            "--ref", ref,
            "--github-output", str(output),
        ],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    values = dict(
        line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines()
    )
    assert values["version"] == f"{__version__}-beta.2"
    assert values["base_version"] == __version__
    assert values["min_version"] == f"{__version__}-beta.0"
    assert values["channel"] == "beta"
    assert values["prerelease"] == "true"
    assert values["release"] == "true"
    assert values["tag"] == ref


def test_release_manifest_keeps_beta_suffix_and_ignores_auxiliary_files(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "RISARMS-Portal-0.6.0-macos-arm64.dmg").write_bytes(b"artifact")
    (dist / "SHA256SUMS-macos-arm64").write_text("checksums\n", encoding="utf-8")
    (dist / "sbom-macos-arm64.cdx.json").write_text("{}\n", encoding="utf-8")
    (dist / "sbom-aggregate.cdx.json").write_text("{}\n", encoding="utf-8")
    private = Ed25519PrivateKey.generate()
    key = base64.b64encode(private.private_bytes_raw()).decode("ascii")
    env = os.environ.copy()
    env["UPDATE_SIGNING_KEY"] = key
    result = subprocess.run(
        [
            sys.executable, str(ROOT / "packaging" / "release_manifest.py"),
            "--dist", str(dist),
            "--version", "0.6.0-beta.1",
            "--repository", "example/RISARMS-ui",
            "--tag", "v0.6.0-beta.1",
            "--channel", "beta",
        ],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads((dist / "update.json").read_text(encoding="utf-8"))
    assert payload["version"] == "0.6.0-beta.1"
    assert payload["channel"] == "beta"
    assert [item["name"] for item in payload["artifacts"]] == [
        "RISARMS-Portal-0.6.0-macos-arm64.dmg",
    ]
    public = base64.b64encode(private.public_key().public_bytes_raw()).decode("ascii")
    verified = updates.verify_manifest(payload, public, channel="beta")
    assert verified.version == "0.6.0-beta.1"
    assert not (dist / "SHA256SUMS").exists()


def test_release_manifest_fails_closed_without_signing_key(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "RISARMS-Portal-0.6.0-macos-arm64.dmg").write_bytes(b"artifact")
    env = os.environ.copy()
    env.pop("UPDATE_SIGNING_KEY", None)
    result = subprocess.run(
        [
            sys.executable, str(ROOT / "packaging" / "release_manifest.py"),
            "--dist", str(dist),
            "--version", "0.6.0",
            "--repository", "example/RISARMS-ui",
            "--tag", "v0.6.0",
        ],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "UPDATE_SIGNING_KEY" in result.stderr
    assert not (dist / "update.json").exists()


def test_sbom_merge_deduplicates_platform_components(tmp_path):
    first = tmp_path / "sbom-macos-arm64.cdx.json"
    first.write_text(json.dumps({
        "components": [
            {"type": "library", "name": "cryptography", "version": "42.0", "purl": "pkg:pypi/cryptography@42.0"},
            {"type": "library", "name": "shared", "version": "1.0", "purl": "pkg:pypi/shared@1.0"},
        ],
    }), encoding="utf-8")
    second = tmp_path / "sbom-linux-x86_64.cdx.json"
    second.write_text(json.dumps({
        "components": [
            {"type": "library", "name": "shared", "version": "1.0", "purl": "pkg:pypi/shared@1.0"},
            {"type": "library", "name": "websockets", "version": "12.0", "purl": "pkg:pypi/websockets@12.0"},
        ],
    }), encoding="utf-8")
    output = tmp_path / "sbom-aggregate.cdx.json"
    result = subprocess.run(
        [
            sys.executable, str(ROOT / "packaging" / "sbom.py"),
            "--merge", str(first), str(second),
            "--output", str(output),
            "--version", "0.6.0",
        ],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["metadata"]["component"]["version"] == "0.6.0"
    assert [item["name"] for item in payload["components"]] == [
        "cryptography", "shared", "websockets",
    ]
