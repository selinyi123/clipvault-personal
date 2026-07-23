#!/usr/bin/env python3
"""Repack the pure-Python pystray wheel with recipient-modified sources.

This helper intentionally uses only the Python standard library. It avoids
pystray's historical setup-time documentation dependencies while preserving
the metadata and license files from the locked release wheel.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


WHEEL_NAME = "pystray-0.19.5-py2.py3-none-any.whl"
DIST_INFO = "pystray-0.19.5.dist-info"
REQUIRED_SOURCE_FILES = ("__init__.py", "_base.py", "_win32.py")


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"unsafe wheel member: {name!r}")
    return path


def _wheel_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
    return "sha256=" + digest.rstrip(b"=").decode("ascii")


def _validate_source_tree(source: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise ValueError("pystray source must be a regular directory")
    for name in REQUIRED_SOURCE_FILES:
        candidate = source / name
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError(f"required pystray source file is missing: {name}")
    for candidate in source.rglob("*"):
        if candidate.is_symlink():
            raise ValueError("pystray source tree must not contain symlinks")


def _extract_locked_wheel(wheel: Path, destination: Path) -> None:
    if wheel.name != WHEEL_NAME:
        raise ValueError(f"base wheel must be named {WHEEL_NAME}")
    with zipfile.ZipFile(wheel) as archive:
        members = archive.infolist()
        names = [member.filename for member in members]
        if len(names) != len(set(names)):
            raise ValueError("base wheel contains duplicate members")
        for member in members:
            _safe_member(member.filename)
            unix_mode = (member.external_attr >> 16) & 0o170000
            if unix_mode == 0o120000:
                raise ValueError("base wheel must not contain symbolic links")
        archive.extractall(destination)

    record = destination / DIST_INFO / "RECORD"
    if not record.is_file():
        raise ValueError("base wheel RECORD is missing")
    for license_name in ("COPYING", "COPYING.LGPL"):
        if not (destination / DIST_INFO / license_name).is_file():
            raise ValueError(f"base wheel license is missing: {license_name}")


def _replace_sources(stage: Path, source: Path) -> None:
    package = stage / "pystray"
    if package.exists():
        shutil.rmtree(package)
    shutil.copytree(source, package)


def _write_record(stage: Path) -> None:
    record_path = stage / DIST_INFO / "RECORD"
    rows: list[tuple[str, str, str]] = []
    for path in sorted(stage.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(stage).as_posix()
        if relative == f"{DIST_INFO}/RECORD":
            continue
        data = path.read_bytes()
        rows.append((relative, _wheel_hash(data), str(len(data))))
    rows.append((f"{DIST_INFO}/RECORD", "", ""))

    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    record_path.write_text(output.getvalue(), encoding="utf-8", newline="")


def _write_wheel(stage: Path, output: Path) -> None:
    if output.name != WHEEL_NAME:
        raise ValueError(f"output wheel must be named {WHEEL_NAME}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"output wheel already exists: {output}")

    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(stage.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(stage).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)


def repack(base_wheel: Path, source: Path, output: Path) -> None:
    base_wheel = base_wheel.resolve(strict=True)
    source = source.resolve(strict=True)
    output = output.resolve(strict=False)
    _validate_source_tree(source)

    with tempfile.TemporaryDirectory(prefix="clipvault-pystray-repack-") as raw:
        stage = Path(raw)
        _extract_locked_wheel(base_wheel, stage)
        _replace_sources(stage, source)
        _write_record(stage)
        _write_wheel(stage, output)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-wheel", required=True, type=Path)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output-wheel", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repack(args.base_wheel, args.source_dir, args.output_wheel)
    print(f"Created {args.output_wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
