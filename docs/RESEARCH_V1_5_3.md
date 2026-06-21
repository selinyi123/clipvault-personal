# ClipVault Personal v1.5.3 — CI validation workflow

Date: 2026-06-21

Scope:

> add a repository-level validation workflow so desktop and Android regressions are visible after every push or pull request.

## 1. Research inputs

This round avoided repeating privacy keyboard, Keystore, ULID, clipboard manager, and CandidateMixer ranking research. The focus was CI reliability for a mixed Python + Android repository.

### 1.1 Keep CI simple first

Recent GitHub Actions workflow reliability research notes that larger and more complex workflows are associated with higher failure and maintenance risk.

Decision:

- Add one minimal `CI` workflow.
- Split desktop and Android into separate jobs.
- Do not add release signing, deployment, or matrix builds yet.

### 1.2 Python setup

`actions/setup-python` supports explicit Python versions and pip dependency caching using `pyproject.toml` as a cache key.

Decision:

- Use Python 3.11 because the desktop package requires `>=3.11`.
- Cache pip using `desktop/pyproject.toml`.
- Install only `pytest` for the current dependency-free desktop package.

### 1.3 Android / Gradle setup

`actions/setup-java` supports installing a requested JDK and caching Gradle dependencies.

Decision:

- Use Temurin JDK 17 to match the Android module JVM target.
- Run Gradle from the `android` directory.
- Run `:core:test`, `:app:testDebugUnitTest`, and `:app:assembleDebug`.
- Do not add signing or release APK generation.

## 2. Implementation decisions

### D-028 CI workflow

Added `.github/workflows/ci.yml` with two jobs:

- `desktop`
  - checkout
  - setup Python 3.11
  - install pytest
  - run `python -m pytest -q`

- `android`
  - checkout
  - setup JDK 17
  - enable Gradle dependency cache
  - run Android unit tests
  - assemble debug APK

### D-029 No release build yet

The CI deliberately avoids release signing and release build paths. The goal is regression detection, not distribution.

## 3. Status

Done:

- CI workflow added.
- Desktop version bumped to `1.5.3`.

Still partial:

- Android app version still needs a version-only patch.
- Panel IME still needs migration to `runtime.listCandidates()`.
- CI run result is not available in this response; GitHub status must be checked after Actions starts.

## 4. Next node

### v1.5.4

- Read the first CI failure logs.
- Fix Android or desktop build failures exposed by CI.
- Apply Android version-only patch if possible.
- Migrate Panel IME using a narrow patch strategy.

### v1.6

- Per-source candidate toggles.
- Candidate source caps.
- Query-aware filtering without persisting typed text.
