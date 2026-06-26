#!/usr/bin/env python3
"""Single-source version propagation for ClipVault.

The canonical version lives in ``desktop/clipvault/__init__.py`` (``__version__``)
— that is what the running desktop reports over the API. The other build/metadata
files (pyproject, Android Gradle, Inno Setup installer) each have their own copy
because their toolchains read their own file; they cannot literally share one
constant. This tool makes bumping a single command, and
``desktop/tests/test_release_alignment.py`` fails CI if any file ever drifts.

Usage:
    python scripts/bump_version.py 1.6.0            # set version, Android code +1
    python scripts/bump_version.py 1.6.0 --code 20  # set an explicit versionCode
    python scripts/bump_version.py --check          # verify all files are aligned
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

INIT = _ROOT / "desktop/clipvault/__init__.py"
PYPROJECT = _ROOT / "desktop/pyproject.toml"
GRADLE = _ROOT / "android/app/build.gradle.kts"
INSTALLER = _ROOT / "installer/clipvault.iss"

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _sub_once(pattern: str, repl: str, text: str, what: str) -> str:
    new, n = re.subn(pattern, repl, text)
    if n != 1:
        raise ValueError(f"expected exactly one {what}, found {n}")
    return new


def set_init_version(text: str, version: str) -> str:
    return _sub_once(r'(__version__\s*=\s*")[^"]+(")', rf'\g<1>{version}\g<2>', text, "__version__")


def set_pyproject_version(text: str, version: str) -> str:
    return _sub_once(r'(?m)^(version\s*=\s*")[^"]+(")', rf'\g<1>{version}\g<2>', text, "version= line")


def set_gradle_version(text: str, version: str, code: int | None) -> str:
    out = _sub_once(r'(versionName\s*=\s*")[^"]+(")', rf'\g<1>{version}\g<2>', text, "versionName")
    if code is not None:
        out = _sub_once(r'(versionCode\s*=\s*)\d+', rf'\g<1>{code}', out, "versionCode")
    return out


def set_installer_version(text: str, version: str) -> str:
    return _sub_once(r'(#define\s+AppVersion\s+")[^"]+(")', rf'\g<1>{version}\g<2>', text, "AppVersion")


def _grab(path: Path, pattern: str) -> str | None:
    m = re.search(pattern, path.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def read_versions() -> dict[str, str | None]:
    return {
        "init": _grab(INIT, r'__version__\s*=\s*"([^"]+)"'),
        "pyproject": _grab(PYPROJECT, r'(?m)^version\s*=\s*"([^"]+)"'),
        "gradle": _grab(GRADLE, r'versionName\s*=\s*"([^"]+)"'),
        "installer": _grab(INSTALLER, r'#define\s+AppVersion\s+"([^"]+)"'),
    }


def current_gradle_code() -> int:
    return int(_grab(GRADLE, r'versionCode\s*=\s*(\d+)') or 0)


def check() -> int:
    versions = read_versions()
    canonical = versions["init"]
    drift = {k: v for k, v in versions.items() if v != canonical}
    if drift:
        print(f"version drift from canonical {canonical!r}: {drift}", file=sys.stderr)
        return 1
    print(f"all version metadata aligned at {canonical}")
    return 0


def bump(version: str, code: int | None) -> None:
    if not _SEMVER.match(version):
        raise SystemExit(f"version must be X.Y.Z, got {version!r}")
    if code is None:
        code = current_gradle_code() + 1
    elif code <= current_gradle_code():
        raise SystemExit(f"--code {code} must exceed current versionCode {current_gradle_code()}")
    for path, setter in (
        (INIT, lambda t: set_init_version(t, version)),
        (PYPROJECT, lambda t: set_pyproject_version(t, version)),
        (GRADLE, lambda t: set_gradle_version(t, version, code)),
        (INSTALLER, lambda t: set_installer_version(t, version)),
    ):
        path.write_text(setter(path.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"set version {version} (Android versionCode {code}) across init/pyproject/gradle/installer")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Propagate the ClipVault version to every build file.")
    p.add_argument("version", nargs="?", help="new X.Y.Z version")
    p.add_argument("--code", type=int, default=None, help="explicit Android versionCode (default: current + 1)")
    p.add_argument("--check", action="store_true", help="verify all files match the canonical version")
    args = p.parse_args(argv)
    if args.check:
        return check()
    if not args.version:
        p.error("a version is required unless --check is given")
    bump(args.version, args.code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
