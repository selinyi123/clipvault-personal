# ClipVault Version Sync Report

Date: 2026-06-25

## Current truth (verified from repository)

### Desktop
- runtime version: 1.5.16
- pyproject.toml: 1.5.16

### Android
- versionName: 1.5.16
- versionCode: 12

### Windows installer
- AppVersion: 1.5.16

## Conclusion

All visible release version metadata is currently aligned at **1.5.16**.

## Remaining non-version blockers (v1.5)

- CI status not confirmed from workflow outputs
- Manual QA not executed in this session

## Interpretation

Previous version drift is resolved. Remaining work is validation only.

## Single-source strategy (v1.6)

The version is duplicated across four files because each toolchain reads its own:
`desktop/clipvault/__init__.py`, `desktop/pyproject.toml`,
`android/app/build.gradle.kts`, and `installer/clipvault.iss`. They cannot share
one literal constant, so drift is prevented operationally instead:

- **Canonical source:** `desktop/clipvault/__init__.py` `__version__` — the value
  the running desktop reports via `/api/health` and `/api/status`.
- **Propagation:** `scripts/bump_version.py X.Y.Z` rewrites all four files in one
  command (and bumps the Android `versionCode` by one, or to `--code N`).
- **Enforcement:** `desktop/tests/test_release_alignment.py` fails CI if any file
  drifts from the canonical version; `python scripts/bump_version.py --check`
  gives the same check locally.
- **Visibility:** the running version is shown in the desktop Web UI status line.

To cut a release, run `scripts/bump_version.py` once and let CI confirm alignment;
never hand-edit a single version file.
