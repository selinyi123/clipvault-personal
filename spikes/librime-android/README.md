# ClipVault librime Android PoC

This directory contains the **isolated v2.1 Chinese-engine PoC** defined by
`docs/SLICES/V2-S004-librime-build-poc.md`.

It does not alter `android/settings.gradle.kts`, either production
`InputMethodService`, Room, sync/outbox, or the production APK dependency graph.
The PoC is deliberately split into evidence-gated slices so a static contract
cannot be mistaken for a successful Android native build.

## Tracks

- **A — custom librime JNI:** preferred single-APK route. The repository now
  contains an original, minimal `initialize / reset / key input / candidates /
  select / commit` adapter contract under `native/` and a thread-confined
  Kotlin facade under `android/`.
- **B — fcitx5-android external addon:** fallback route. It must still prove
  that a separately installed addon can inject one synthetic candidate into the
  live candidate flow and receive its click callback.
- **Trime:** architecture/build reference only. Its GPL-3.0-or-later source is
  not copied into ClipVault.

## Current state

`POC_LOCK.json` pins current and previous stable releases for A and B, the Trime
reference revision, the toolchain, ABIs, 16 KB emulator, and Track A's direct
native dependency inputs. The Track A transitive closure is explicitly marked
`INCOMPLETE_FAIL_CLOSED`; this is not a license approval or build result.

The project-authored table schema, four-entry synthetic dictionary, and default
configuration remain locked by SHA-256. Their redistribution license still
requires Owner approval. Therefore:

- no production integration is allowed;
- no APK, AAB, `.so`, or addon artifact may be uploaded;
- no user dictionary, clipboard item, typed text, Room row, or network input may
  enter the PoC;
- passing either static validator is not evidence that librime builds or runs.

## Track A native contract

The JNI boundary is intentionally narrow:

- `open / close`;
- `reset`;
- ASCII key processing;
- in-memory composition and candidate snapshots;
- current-page candidate selection;
- one-shot commit retrieval;
- engine version reporting.

The adapter uses only the pinned librime C API. It does not expose sync, user
configuration, networking, logging, or persistence APIs. One process may own
one PoC engine instance at a time so librime's process-global lifecycle cannot
be accidentally driven by two test sessions.

`native/CMakeLists.txt` is an import-only wrapper target. It does not clone,
download, or choose a librime binary. The caller must supply an ABI-matching,
pinned `rime_api.h` and `librime` shared library after the dependency build is
implemented. Both 16 KB linker page-size options are mandatory.

## Static validation

Run both local guards:

```bash
python spikes/librime-android/tools/validate_poc.py
python spikes/librime-android/tools/validate_native_contract.py
python -m py_compile \
  spikes/librime-android/tools/validate_poc.py \
  spikes/librime-android/tools/validate_native_contract.py \
  spikes/librime-android/tools/check_elf_alignment.py
```

The native-contract validator checks the exact JNI method set, the allowlisted
librime C API calls, thread/lifecycle guards, 16 KB linker flags, pinned direct
dependencies, and absence of network, clipboard, persistence, sync, logging,
or source-downloading APIs.

After native libraries exist, inspect every transitive `.so`:

```bash
python spikes/librime-android/tools/check_elf_alignment.py \
  path/to/libone.so path/to/libtwo.so
zipalign -v -c -P 16 4 path/to/spike.apk
adb shell getconf PAGE_SIZE  # must print 16384 on the locked emulator
```

## Next execution order

1. Implement the reproducible, no-floating-input Android dependency build for
   Track A and produce the actual ABI-specific shared-object manifest.
2. Complete nested dependency/license inventory, including OpenCC data and
   Boost linkage, and obtain Owner approval for project-owned PoC data.
3. Add a standalone Android test shell that copies only the pinned synthetic
   data into a fresh temporary user-data directory for each vector.
4. Build arm64-v8a and x86_64, then run candidate/reset/commit vectors offline.
5. Run all `.so` alignment, 16 KB emulator, two-clean-build reproducibility,
   size/time/patch/bootstrap, and fixed upgrade-drill gates.
6. Resolve and execute Track B's real external-addon candidate boundary.
7. Update ADR-0010 only after both routes have complete pass-or-fail evidence.

The final decision algorithm remains: choose A if A passes; choose B only if A
fails and B passes; otherwise remain blocked.
