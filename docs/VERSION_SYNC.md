# ClipVault Version Sync Report

Date: 2026-07-03

## Current truth (verified from repository)

### Desktop

- runtime version: 1.6.0
- pyproject.toml: 1.6.0

### Android

- versionName: 1.6.0
- versionCode: 13

### Windows installer

- AppVersion: 1.6.0

## Conclusion

All visible source-tree release version metadata is currently aligned at **1.6.0**.

## Remaining release blockers for Issue #36

- GitHub Actions CI result must be recorded for the target commit.
- Desktop portable exe, desktop installer, signed Android APK, and checksums
  must be built before publishing `v1.6.0`.
- Manual Android device QA and Windows clipboard privacy QA remain owner-run
  gates and are not claimed by this document.
- Final `v1.6.0` GitHub Release publication remains blocked until Owner
  approval records the signed artifacts and manual QA evidence on Issue #36.

## Single-source strategy

The version is duplicated across four files because each toolchain reads its own:
`desktop/clipvault/__init__.py`, `desktop/pyproject.toml`,
`android/app/build.gradle.kts`, and `installer/clipvault.iss`. They cannot share
one literal constant, so drift is prevented operationally instead:

- **Canonical source:** `desktop/clipvault/__init__.py` `__version__`, the value
  the running desktop reports via `/api/health` and `/api/status`.
- **Propagation:** `scripts/bump_version.py X.Y.Z` rewrites all four files in one
  command and bumps the Android `versionCode` by one, or to `--code N`.
- **Enforcement:** `desktop/tests/test_release_alignment.py` fails CI if any file
  drifts from the canonical version; `python scripts/bump_version.py --check`
  gives the same check locally.
- **Visibility:** the running version is shown in the desktop Web UI status line.

To cut a release, run `scripts/bump_version.py` once and let CI confirm alignment;
never hand-edit a single version file.
