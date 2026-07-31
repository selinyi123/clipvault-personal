# ClipVault librime Android PoC

This directory starts the **isolated v2.1 build PoC** defined by
`docs/SLICES/V2-S004-librime-build-poc.md`.

It does not alter `android/settings.gradle.kts`, either production
`InputMethodService`, Room, sync/outbox, or the production APK dependency graph.
P0 only freezes upstream inputs and installs fail-closed validation scaffolding.

## Tracks

- **A — custom librime JNI:** preferred single-APK route. Build a minimal
  `reset / key input / select / candidates` boundary around librime.
- **B — fcitx5-android external addon:** fallback route. Prove a separately
  installed addon can inject one synthetic candidate into the same candidate
  flow and receive its click callback.
- **Trime:** architecture/build reference only. Its GPL-3.0-or-later source is
  not copied into ClipVault.

## Current state

`POC_LOCK.json` pins the current stable and immediately previous stable releases
for A and B, plus the Trime reference revision. Schema and dictionary inputs are
deliberately unresolved. Therefore:

- no production integration is allowed;
- no APK, AAB, `.so`, or addon artifact may be uploaded;
- the synthetic vectors are inactive until schema and dictionary SHAs and
  licenses are approved;
- passing the static check is not evidence that either native route builds.

Run the local static guard:

```bash
python spikes/librime-android/tools/validate_poc.py
```

After native libraries exist, inspect every transitive `.so`:

```bash
python spikes/librime-android/tools/check_elf_alignment.py \
  path/to/libone.so path/to/libtwo.so
zipalign -v -c -P 16 4 path/to/spike.apk
adb shell getconf PAGE_SIZE  # must print 16384 on the locked emulator
```

## Next execution order

1. Pin one minimal, redistributable Rime schema and dictionary, including exact
   SHAs and licenses.
2. Complete `THIRD_PARTY_NATIVE.md` separately for A and B and obtain the
   required license approval.
3. Build A in a standalone Gradle/NDK project and execute the synthetic vectors
   with fresh user-data for every case.
4. Build B as an external addon without adding fcitx5 code to the ClipVault
   production APK.
5. Run arm64-v8a and x86_64 builds, 16 KB runtime checks, two clean reproducible
   builds, size/time/patch/bootstrap measurements, and the fixed upgrade drill.
6. Update ADR-0010 only after both routes have complete pass-or-fail evidence.

The final decision algorithm remains: choose A if A passes; choose B only if A
fails and B passes; otherwise remain blocked.
