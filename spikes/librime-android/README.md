# ClipVault librime Android PoC

> Historical scope: this directory preserves the original A/B experiment and
> its synthetic data. Route A has since been selected for production. Current
> implementation and distribution evidence live in `android/ime-app`,
> `android/rime-engine-android/RIME_PRODUCTION_LOCK.json`, and
> `shared-input/rime`; restrictions below apply to artifacts built from this
> historical PoC, not to the separately audited production entrypoint.

This directory starts the **isolated v2.1 build PoC** defined by
`docs/SLICES/V2-S004-librime-build-poc.md`.

It does not alter `android/settings.gradle.kts`, either production
`InputMethodService`, Room, sync/outbox, or the production APK dependency graph.
The current bootstrap freezes inputs and installs fail-closed validation
scaffolding before native integration begins.

## Tracks

- **A — custom librime JNI:** preferred single-APK route. Build a minimal
  `initialize / reset / key input / candidates / select / commit` boundary.
- **B — fcitx5-android external addon:** fallback route. Prove a separately
  installed addon can inject one synthetic candidate into the same candidate
  flow and receive its click callback.
- **Trime:** architecture/build reference only. Its GPL-3.0-or-later source is
  not copied into ClipVault.

## Current state

`POC_LOCK.json` pins current and previous stable releases for A and B, the Trime
reference revision, the toolchain, ABIs, and 16 KB emulator. The repository also
contains a project-authored table schema and four-entry synthetic dictionary,
locked by SHA-256.

The historical PoC data is ready for local/CI compilation only. Its redistribution license and
all transitive native obligations remain unapproved. Therefore:

- no production integration may consume these synthetic PoC artifacts;
- no APK, AAB, `.so`, or addon artifact may be uploaded;
- no user dictionary, clipboard item, typed text, Room row, or network input may
  enter the PoC;
- passing the static check is not evidence that either native route builds.

`session-contract/` is a standalone Java 17/JVM protocol PoC with no external
runtime dependencies. It adds an in-memory fake engine host, a client-created
session/start-sequence contract, and a JVM-testable Android IME client seam.
The client owns a strict applied-response ledger, a three-result editor-effect
abstraction suitable for a later `InputConnection` adapter, fail-closed session
retirement, host-restart recovery without key replay, and strongly separated
engine/ClipVault candidate surfaces. Password/incognito mode never invokes the
ClipVault candidate source. Locked synthetic vectors cover request ordering,
the content-free `FULL_SHAPE` option whitelist, paging, at-most-once commit,
UTF-16 boundaries, and cleanup. It is not included from
`android/settings.gradle.kts` and does not change either production APK or the
frozen KBD-1 contract. See
`session-contract/CONTRACT.md` for the exact invariants.

### Windows PowerShell verification

Prerequisites: JDK 17, the checked-in Gradle wrapper, its Gradle 8.10.2 cache
available for offline use, and a real CPython interpreter. Run from the
repository root. Prefer the repository virtual environment; if it lives in a
different checkout, replace the first path with that interpreter's absolute
path.

```powershell
$PythonExe = (Resolve-Path -LiteralPath '.\desktop\.venv\Scripts\python.exe' -ErrorAction Stop).Path
if ($PythonExe -like '*\WindowsApps\python*.exe') {
    throw 'Microsoft Store Python aliases are not valid PoC interpreters.'
}
& $PythonExe -c "import sys; assert sys.version_info >= (3, 10); print(sys.executable)"
if ($LASTEXITCODE -ne 0) { throw 'A working CPython 3.10+ interpreter is required.' }
& $PythonExe '.\spikes\librime-android\tools\validate_poc.py'
if ($LASTEXITCODE -ne 0) { throw 'PoC static validation failed.' }

.\android\gradlew.bat -p '.\spikes\librime-android\session-contract' `
    check --offline --no-daemon --console=plain
if ($LASTEXITCODE -ne 0) { throw 'Session-contract verification failed.' }
```

Do not treat the bare Windows `python` application-execution alias as a
successful prerequisite: it may only open the Microsoft Store and execute no
validator.

### POSIX verification

```bash
python spikes/librime-android/tools/validate_poc.py
./android/gradlew -p spikes/librime-android/session-contract \
  check --offline --no-daemon --console=plain
```

The standard Gradle `check` task depends on both `verifySessionContract` and
`verifyAndroidImeSlice`; neither custom task depends back on `check`, avoiding
a task cycle.

It checks exact upstream Git SHAs, rejects floating tags, verifies every pinned
data file byte-for-byte, and binds the synthetic vectors to those data hashes.

After native libraries exist, inspect every transitive `.so`:

```bash
python spikes/librime-android/tools/check_elf_alignment.py \
  path/to/libone.so path/to/libtwo.so
zipalign -v -c -P 16 4 path/to/spike.apk
adb shell getconf PAGE_SIZE  # must print 16384 on the locked emulator
```

## Next execution order

1. Complete the A-route transitive dependency inventory and approve the
   project-owned PoC data license for binary redistribution.
2. Build A in a standalone Gradle/NDK project and execute the synthetic vectors
   with fresh user-data for every case.
3. Resolve the exact fcitx5 Rime plugin/addon boundary and complete B's
   dependency/license inventory.
4. Build B as an external addon without adding fcitx5 code to the ClipVault
   production APK.
5. Run arm64-v8a and x86_64 builds, 16 KB runtime checks, two clean reproducible
   builds, size/time/patch/bootstrap measurements, and the fixed upgrade drill.
6. Preserve the measurements as historical evidence; ADR-0010 now records the
   production A-route decision.

The historical decision algorithm was: choose A if A passes; choose B only if A
fails and B passes. Route A is now the production decision.
