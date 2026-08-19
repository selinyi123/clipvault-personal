# Native A JNI slice

This is a local-build-only Android NDK wrapper around the exact librime source
locked in `../POC_LOCK.json`. It is not included by `android/settings.gradle.kts`
and is not packaged into the production APK while the transitive license/data
gate remains open.

Required CMake cache values:

- `CLIPVAULT_LIBRIME_SOURCE`: checkout at the locked librime SHA;
- `CLIPVAULT_LIBRIME_BUILD`: same-ABI source build containing `lib/librime.a`;
- `CLIPVAULT_PREBUILT_ROOT`: local audited dependency snapshot containing the
  ABI-specific yaml-cpp, leveldb, OpenCC, and marisa static archives.

The wrapper exposes initialize/create/process/snapshot/commit/select/clear/end
operations only. It writes no key or commit logs, sets Rime's minimum log level
to fatal, and uses no network or ClipVault Runtime dependency. Both source-built
librime and the final JNI shared object must be built for `arm64-v8a` and
`x86_64`; every final ELF LOAD segment must have alignment of at least 16 KiB.

The dependency snapshot is evidence scaffolding, not redistribution approval.
Do not copy its archives into `android/app/src/main/jniLibs`, upload its output,
or enable this JNI in a release build before `THIRD_PARTY_NATIVE.md` is complete.
