from __future__ import annotations

import base64
import csv
import hashlib
import io
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "desktop" / "packaging" / "repack_pystray_wheel.py"
WHEEL_NAME = "pystray-0.19.5-py2.py3-none-any.whl"
DIST_INFO = "pystray-0.19.5.dist-info"


def _hash(data: bytes) -> str:
    value = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
    return "sha256=" + value.rstrip(b"=").decode("ascii")


def _base_wheel(path: Path) -> None:
    files = {
        "pystray/__init__.py": b"ORIGINAL = True\n",
        "pystray/_base.py": b"",
        "pystray/_win32.py": b"",
        f"{DIST_INFO}/COPYING": b"GPL text",
        f"{DIST_INFO}/COPYING.LGPL": b"LGPL text",
        f"{DIST_INFO}/METADATA": b"Name: pystray\nVersion: 0.19.5\n",
        f"{DIST_INFO}/WHEEL": b"Root-Is-Purelib: true\nTag: py2-none-any\n",
    }
    rows = [
        (name, _hash(data), str(len(data))) for name, data in sorted(files.items())
    ]
    rows.append((f"{DIST_INFO}/RECORD", "", ""))
    record = io.StringIO(newline="")
    csv.writer(record, lineterminator="\n").writerows(rows)
    files[f"{DIST_INFO}/RECORD"] = record.getvalue().encode()

    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)


def test_repack_replaces_sources_and_regenerates_record(tmp_path: Path):
    base = tmp_path / WHEEL_NAME
    _base_wheel(base)
    source = tmp_path / "source"
    source.mkdir()
    (source / "__init__.py").write_bytes(b"RELINK_MARKER = 'changed'\n")
    (source / "_base.py").write_bytes(b"BASE = 'changed'\n")
    (source / "_win32.py").write_bytes(b"WIN32 = 'changed'\n")
    output = tmp_path / "modified" / WHEEL_NAME

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--base-wheel",
            str(base),
            "--source-dir",
            str(source),
            "--output-wheel",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert archive.read("pystray/__init__.py") == b"RELINK_MARKER = 'changed'\n"
        assert archive.read(f"{DIST_INFO}/COPYING") == b"GPL text"
        assert archive.read(f"{DIST_INFO}/COPYING.LGPL") == b"LGPL text"
        rows = {
            row[0]: row[1:]
            for row in csv.reader(
                io.StringIO(archive.read(f"{DIST_INFO}/RECORD").decode())
            )
        }
        data = archive.read("pystray/__init__.py")
        assert rows["pystray/__init__.py"] == [_hash(data), str(len(data))]
        assert rows[f"{DIST_INFO}/RECORD"] == ["", ""]


def test_repack_rejects_source_symlink(tmp_path: Path):
    if not hasattr(Path, "symlink_to"):
        return
    base = tmp_path / WHEEL_NAME
    _base_wheel(base)
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target.py"
    target.write_text("x = 1\n")
    for name in ("__init__.py", "_base.py"):
        (source / name).write_text("x = 1\n")
    try:
        (source / "_win32.py").symlink_to(target)
    except OSError:
        return

    output = tmp_path / "modified" / WHEEL_NAME
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--base-wheel",
            str(base),
            "--source-dir",
            str(source),
            "--output-wheel",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not output.exists()
