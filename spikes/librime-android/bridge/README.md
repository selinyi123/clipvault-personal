# ClipVault Rime bridge contract

This directory contains the project-authored C++ boundary that the Android JNI
adapter will use. It still does **not** link librime.

The separation is intentional:

- `Bridge` owns lifecycle validation, partial-initialization cleanup, reset
  invariants and fail-closed argument checks;
- `LibrimeBackend` is an original implementation against librime's public
  `rime_api.h` C function table; it is isolated behind PImpl so callers do not
  import Rime types;
- CI fetches the exact locked librime commit, verifies the Git blob of
  `src/rime_api.h`, and syntax-checks `librime_backend.cpp` against that header;
- an unhandled `process_key` result is returned to the caller instead of being
  converted into an engine failure, because Android must be able to fall back
  to normal key handling;
- reset clears composition and drains unread commit text so a selection cannot
  cross into the next vector or editor field;
- no network, Room/outbox, clipboard, typed-text persistence or user dictionary
  is present in this boundary.

The fake backend host test proves the intended contract flow:

```text
initialize -> n i h a o -> 你好 candidate -> select -> 你好 commit -> reset
```

It also verifies that a partially failed initialization is shut down and that
reset rejects stale composition, candidates or commit data.

Run the host contract locally:

```bash
cmake -S spikes/librime-android/bridge \
  -B build/librime-bridge -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build/librime-bridge
ctest --test-dir build/librime-bridge --output-on-failure
```

Passing the host test and exact-header syntax check proves only the
ClipVault-owned contract and source-level API compatibility. It is not evidence
that librime links, initializes with the Android NDK, deploys the schema,
produces candidates, supports both ABIs, or runs on 16 KB pages. Those claims
remain prohibited until the native route is built and measured.
