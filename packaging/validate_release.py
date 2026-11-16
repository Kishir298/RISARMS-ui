"""Validate a release tag against project metadata and emit CI outputs."""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from risarms_ui import __version__  # noqa: E402
from risarms_ui.release import ReleaseTarget, parse_release_ref  # noqa: E402

_VERSION_LINE = re.compile(r'(?m)^version\s*=\s*"([^"]+)"\s*$')


def project_version(root: Path = ROOT) -> str:
    text = (Path(root) / "pyproject.toml").read_text(encoding="utf-8")
    match = _VERSION_LINE.search(text)
    if match is None:
        raise SystemExit("pyproject.toml does not contain a static project version.")
    return match.group(1)


def build_reads_package_version(root: Path = ROOT) -> bool:
    source = (Path(root) / "packaging" / "build.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports_version = False
    reads_version = False
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "risarms_ui":
            imports_version = imports_version or any(
                alias.name == "__version__" for alias in node.names
            )
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "VERSION":
                    reads_version = isinstance(node.value, ast.Name) and node.value.id == "__version__"
    return imports_version and reads_version


def check_versions(root: Path, expected: str) -> list[str]:
    errors = []
    declared = project_version(root)
    if declared != __version__:
        errors.append(
            f"pyproject.toml version {declared} does not match risarms_ui.__version__ {__version__}."
        )
    if declared != expected:
        errors.append(f"Release version {expected} does not match project version {declared}.")
    if not build_reads_package_version(root):
        errors.append("packaging/build.py must assign VERSION from risarms_ui.__version__.")
    return errors


def write_github_output(path: Path, target: ReleaseTarget, release: bool) -> None:
    min_version = (
        target.base_version
        if target.channel == "stable"
        else f"{target.base_version}-beta.0"
    )
    values = {
        "version": target.version,
        "base_version": target.base_version,
        "min_version": min_version,
        "channel": target.channel,
        "prerelease": str(target.prerelease).lower(),
        "release": str(release).lower(),
        "tag": target.tag if release else "",
    }
    with Path(path).open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", help="Git tag such as v0.6.0 or v0.6.0-beta.1")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    release = bool(args.ref)
    if release:
        try:
            target = parse_release_ref(args.ref)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        expected = target.base_version
    else:
        target = parse_release_ref(f"v{__version__}")
        expected = __version__

    if args.check:
        errors = check_versions(ROOT, expected)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1

    if args.github_output:
        write_github_output(args.github_output, target, release)
    print(
        f"version={target.version} channel={target.channel} "
        f"prerelease={str(target.prerelease).lower()} release={str(release).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
