"""Emit or merge minimal CycloneDX SBOMs for release artifacts."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path


def installed_components() -> list[dict]:
    components = []
    for dist in importlib.metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        version = (dist.version or "").strip()
        if not name or not version:
            continue
        components.append({
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:pypi/{name.lower().replace('_', '-')}@{version}",
        })
    return components


def component_key(item: dict) -> tuple[str, str, str]:
    return (
        str(item.get("name", "")).lower(),
        str(item.get("version", "")),
        str(item.get("purl", "")),
    )


def build_payload(components: list[dict], version: str | None = None) -> dict:
    components = sorted(components, key=lambda item: (item["name"].lower(), item["version"]))
    component = {"type": "application", "name": "RISARMS-UI"}
    if version:
        component["version"] = version
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {"component": component},
        "components": components,
    }


def merge_payload(paths: list[Path], version: str | None = None) -> dict:
    merged: dict[tuple[str, str, str], dict] = {}
    for path in paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        for item in payload.get("components", []):
            if isinstance(item, dict):
                merged.setdefault(component_key(item), item)
    return build_payload(list(merged.values()), version)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist/sbom.cdx.json"))
    parser.add_argument("--merge", nargs="+", type=Path)
    parser.add_argument("--version")
    args = parser.parse_args()
    if args.merge:
        payload = merge_payload(args.merge, args.version)
    else:
        payload = build_payload(installed_components(), args.version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {args.output} ({len(payload['components'])} components)")


if __name__ == "__main__":
    main()
