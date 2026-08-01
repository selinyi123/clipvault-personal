# ClipVault Rime bridge contract

This directory contains the project-authored C++ boundary that the Android JNI
adapter will use. It deliberately does **not** link librime yet.

The separation is intentional:

- `Bridge` owns lifecycle validation, partial-initialization cleanup, reset
  invariants and fail-closed argument checks;
- `Backend` is the narrow implementation boundary for a future
  `LibrimeBackend` built only from librime's public C API;
- an unhandled `process_key` result is returned to the caller instead of being
  converted into an engine failure, because Android must be able to fall back
  to normal key handling;
- no logging, network, Room/outbox, clipboard, typed-text persistence or user
  dictionary is present in this host contract.

The fake backend test proves the intended flow:

```text
initialize -> n i h a o -> 你好 candidate -> select -> 你好 commit -> reset
```

It also verifies that a partially failed initialization is shut down and that
reset rejects stale composition, candidates or commit data.

Run locally:

```bash
cmake -S spikes/librime-android/bridge \
  -B build/librime-bridge -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build/librime-bridge
ctest --test-dir build/librime-bridge --output-on-failure
```

Passing this test is evidence for the ClipVault-owned boundary only. It is not
evidence that librime, JNI, Android ABIs, 16 KB pages, deployment or candidate
vectors work. Those claims remain prohibited until the native route is built
and measured.
