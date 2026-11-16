"""Release tag parsing, version ordering and artifact name normalization."""
from __future__ import annotations

import re
from dataclasses import dataclass

_VERSION_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_RELEASE_TAG_RE = re.compile(
    r"^v?(?P<base>\d+\.\d+\.\d+)(?:-beta\.(?P<beta>\d+))?$"
)
_ARCH_ALIASES = {
    "amd64": "x86_64",
    "apple-silicon": "arm64",
    "arm64": "arm64",
    "armv8": "arm64",
    "armv8l": "arm64",
    "aarch64": "arm64",
    "intel": "x86_64",
    "x64": "x86_64",
    "x86-64": "x86_64",
    "x86_64": "x86_64",
}


@dataclass(frozen=True)
class ReleaseTarget:
    tag: str
    version: str
    base_version: str
    channel: str
    prerelease: bool


def parse_release_ref(ref: str) -> ReleaseTarget:
    """Parse ``v0.6.0`` or ``v0.6.0-beta.N`` into release metadata."""
    text = str(ref or "").strip()
    match = _RELEASE_TAG_RE.fullmatch(text)
    if match is None:
        raise ValueError(
            f"Unsupported release tag {text!r}; expected vX.Y.Z or vX.Y.Z-beta.N."
        )
    base = match.group("base")
    beta = match.group("beta")
    if beta is None:
        return ReleaseTarget(text, base, base, "stable", False)
    return ReleaseTarget(text, f"{base}-beta.{beta}", base, "beta", True)


def version_key(value: str):
    """Return a sortable key where stable releases sort after their prereleases."""
    text = str(value or "").strip()
    match = _VERSION_RE.fullmatch(text)
    if match is None:
        raise ValueError("Version must look like X.Y.Z or X.Y.Z-beta.N.")
    base = (int(match.group("major")), int(match.group("minor")), int(match.group("patch")))
    prerelease = match.group("prerelease")
    if prerelease is None:
        return (*base, 1, ())
    identifiers = tuple(
        (0, int(item)) if item.isdigit() else (1, item)
        for item in prerelease.split(".")
        if item
    )
    return (*base, 0, identifiers)


def normalize_arch(machine: str) -> str:
    """Normalize platform.machine() aliases to ``x86_64`` or ``arm64``."""
    value = str(machine or "").strip().lower()
    return _ARCH_ALIASES.get(value, value or "unknown")


def platform_tag(platform_name: str, machine: str) -> str:
    """Return a filename-safe ``<os>-<arch>`` tag."""
    value = str(platform_name or "").strip().lower()
    if value == "darwin" or value.startswith("mac"):
        os_name = "macos"
    elif value in ("win32", "windows") or value.startswith("win"):
        os_name = "windows"
    else:
        os_name = "linux"
    return f"{os_name}-{normalize_arch(machine)}"


def debian_arch(arch: str) -> str:
    """Return the Debian package architecture name for a normalized arch."""
    normalized = normalize_arch(arch)
    return {"x86_64": "amd64", "arm64": "arm64"}.get(normalized, normalized)
