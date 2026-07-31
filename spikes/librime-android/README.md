# ClipVault librime Android PoC

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

The data is ready for local/CI compilation only. Its redistribution license and
all transitive native obligations remain unapproved. Therefore:

- no production integration is allowed;
- no APK, AAB, `.so`, or addon artifact may be uploaded;
- no user dictionary, clipboard item, typed text, Room row, or network input may
  enter the PoC;
- passing the static check is not evidence that either native route builds.

Run the local static guard:

```bash
python spikes/librime-android/tools/validate_poc.py
```

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
6. Update ADR-0010 only after both routes have complete pass-or-fail evidence.

The final decision algorithm remains: choose A if A passes; choose B only if A
fails and B passes; otherwise remain blocked.
