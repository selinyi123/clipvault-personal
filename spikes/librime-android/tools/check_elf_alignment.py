#!/usr/bin/env python3
"""Check ELF LOAD alignment for Android 16 KB page-size compatibility.

The script invokes llvm-objdump for every supplied shared library and rejects
missing/ambiguous LOAD alignment or any LOAD segment below 2**14.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

LOAD_RE = re.compile(r"^\s*LOAD\b.*?\balign\s+2\*\*(\d+)\s*$", re.IGNORECASE)


def check_library(path: Path, llvm_objdump: str) -> list[str]:
    if not path.is_file():
        return [f"{path}: not a regular file"]
    completed = subprocess.run(
        [llvm_objdump, "-p", str(path)],
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        return [f"{path}: llvm-objdump failed: {detail}"]

    exponents = [
        int(match.group(1))
        for line in completed.stdout.splitlines()
        if (match := LOAD_RE.match(line))
    ]
    if not exponents:
        return [f"{path}: no parseable LOAD alignment entries"]
    failures = [value for value in exponents if value < 14]
    if failures:
        return [f"{path}: LOAD alignment exponents below 14: {failures}"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("libraries", nargs="+", type=Path)
    parser.add_argument("--llvm-objdump", default="llvm-objdump")
    args = parser.parse_args()

    errors: list[str] = []
    for library in args.libraries:
        errors.extend(check_library(library, args.llvm_objdump))

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"16 KB ELF alignment passed for {len(args.libraries)} libraries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
