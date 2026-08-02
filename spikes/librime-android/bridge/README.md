# ClipVault Rime bridge contract

This directory contains the project-authored boundary for the preferred
single-APK librime route. It still does **not** link librime.

The layers are intentionally separated:

- `Bridge` owns lifecycle validation, partial-initialization cleanup, reset
  invariants and fail-closed argument checks;
- `LibrimeBackend` is an original implementation against librime's public
  `rime_api.h` C function table, hidden behind PImpl;
- `jni_bridge.cpp` owns opaque native handles, Java exception translation and
  candidate snapshot transfer;
- `NativeRimeBridge.java` owns the Java lifecycle and decodes the temporary
  string-array wire format into immutable snapshots;
- `utf8.cpp` performs strict UTF-8/UTF-16 conversion instead of JNI modified
  UTF-8, so supplementary-plane text such as emoji is not corrupted.

The JNI snapshot array is internal to the PoC:

```text
[handled: "0"|"1", composition, commit, candidateText, candidateComment, ...]
```

Java callers never receive raw native pointers. Handles are registry IDs,
`close()` is idempotent, concurrent calls retain shared session ownership, and
invalid handles fail closed. Java input containing U+0000 is rejected because
librime paths and schema identifiers are passed through C strings.

CI now:

- builds and runs the fake-backend bridge test;
- tests valid Chinese, supplementary-plane Unicode and malformed encodings;
- fetches the exact locked librime commit and verifies the Git blob of
  `src/rime_api.h`;
- compiles the Java class with `javac -h`;
- syntax-checks the JNI source against the generated header and the backend
  against the exact librime public header.

The host flow remains:

```text
initialize -> n i h a o -> 你好 candidate -> select -> 你好 commit -> reset
```

Run the host tests locally:

```bash
cmake -S spikes/librime-android/bridge \
  -B build/librime-bridge -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build/librime-bridge
ctest --test-dir build/librime-bridge --output-on-failure
```

These checks prove the ClipVault-owned contract, Unicode handling and
source-level JNI/API compatibility only. They do not prove that librime links,
initializes with the Android NDK, deploys the schema, produces real candidates,
supports both ABIs, or runs on 16 KB pages.
