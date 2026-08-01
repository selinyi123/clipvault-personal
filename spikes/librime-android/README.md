# ClipVault librime Android PoC

This directory implements the isolated **v2.1 Chinese-engine PoC** defined by
`docs/SLICES/V2-S004-librime-build-poc.md`.

It does not alter `android/settings.gradle.kts`, either production
`InputMethodService`, Room, sync/outbox, or the production APK dependency graph.
The work is evidence-gated: a host contract, source lock or syntax check must not
be described as a successful Android/native integration.

## Tracks

- **A — custom librime JNI:** preferred single-APK route. Build a minimal
  `initialize / reset / process key / candidates / select / commit` boundary.
- **B — fcitx5-android external addon:** fallback route. Prove a separately
  installed addon can inject one synthetic candidate into the same candidate
  flow and receive its click callback.
- **Trime:** architecture/build reference only. Its GPL-3.0-or-later source is
  not copied into ClipVault.

## Current state

The version lock was corrected on 2026-08-01 after fresh upstream verification:

- librime latest stable: `1.17.0` at
  `33e78140250125871856cdc5b42ddc6a5fcd3cd4`;
- librime previous stable: `1.16.1` at
  `de4700e9f6b75b109910613df907965e3cbe0567`;
- fcitx5-android latest/previous remain `0.1.3` and `0.1.2`.

`A_ROUTE_SOURCE_LOCK.json` records the currently identified A-route source
closure and minimal build policy. The closure includes OpenCC's vendored
RapidJSON headers and explicitly tracks TCLAP and darts-clone as intended
exclusions. Because OpenCC currently adds command-line tools and conversion data
to its build graph, the PoC includes a repository-owned library-only patch at
`patches/opencc-library-only.patch`. Its bytes are locked; CI is responsible for
proving clean application and exclusion of unwanted targets.

`bridge/` now contains two project-authored layers:

- a host-tested C++17 `Bridge` contract for lifecycle, input, candidates, commit
  and reset semantics;
- `LibrimeBackend`, an original PImpl implementation that calls only librime's
  public C API. CI fetches the exact locked `rime_api.h`, verifies its Git blob
  and syntax-checks this source against it.

Neither layer is linked to a native librime build yet.

The project-authored table schema and four-entry synthetic dictionary remain
locked by SHA-256. They are local/CI test inputs only. Their redistribution
license and all native distribution obligations remain unapproved. Therefore:

- no production integration is allowed;
- no APK, AAB, `.so`, addon or binary PoC artifact may be uploaded;
- no user dictionary, clipboard item, typed text, Room row or network input may
  enter the PoC;
- passing static, host-contract and syntax checks is not evidence that either
  native route builds or runs.

## Local checks

```bash
python spikes/librime-android/tools/validate_poc.py
cmake -S spikes/librime-android/bridge \
  -B build/librime-bridge -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build/librime-bridge
ctest --test-dir build/librime-bridge --output-on-failure
```

After native libraries exist, inspect every transitive `.so`:

```bash
python spikes/librime-android/tools/check_elf_alignment.py \
  path/to/libone.so path/to/libtwo.so
zipalign -v -c -P 16 4 path/to/spike.apk
adb shell getconf PAGE_SIZE  # must print 16384 on the locked emulator
```

## Next execution order

1. Complete the CI proof that the locked OpenCC patch applies to the exact
   source SHA and that TCLAP, darts-clone, tools and data targets are absent.
2. Add an isolated native CMake/Gradle shell that compiles the existing
   `LibrimeBackend` and links it with the locked static librime closure.
3. Build arm64-v8a and x86_64, deploy the locked schema, then run the synthetic
   candidate vectors with fresh user-data for every case.
4. Complete license/NOTICE/source delivery paths and obtain explicit approval;
   keep all binary upload disabled until then.
5. Resolve the exact fcitx5 Rime plugin/addon boundary and complete B's source,
   dependency and license inventory.
6. Build B externally without adding fcitx5 code to the ClipVault production
   APK.
7. Run 16 KB runtime checks, two clean reproducible builds, size/time/patch/
   bootstrap measurements and the fixed upgrade drill.
8. Update ADR-0010 only after both routes have complete pass-or-fail evidence.

The decision algorithm remains: choose A if A passes; choose B only if A fails
and B passes; otherwise remain blocked.
