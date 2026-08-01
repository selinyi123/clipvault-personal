# THIRD_PARTY_NATIVE - Windows IME v2

Status: **production source/build tests pass; redistribution and stable release remain blocked**.

The selected implementation is project-authored C++ for the TSF DLL, candidate
window, framed Named Pipe client, and external Host. TypeDuck and libIME2 remain
reference projects only and are not compiled, linked, copied, or distributed.
The external Host can load the official librime 1.16.1 Windows x64 binary from
an external cache.

| Component | Immutable identity | License | Selected boundary | Current status |
|---|---|---|---|---|
| TypeDuck-HK/TypeDuck-Windows | `1ac3af3b44e7478a0f1c7c153bceabf6aa7efb3b` | MIT | architecture reference only | not in build |
| TypeDuck-HK/TypeDuck-Windows-backend | `af3636a40c9081a7862664e422a6e34ac69fafd6` | MIT | external-Host reference only | not in build |
| EasyIME/libIME2 | `717b1901a417667405399cfbf25b25664efcf0e4` | LGPL-2.1 as reported upstream | TypeDuck relationship reference only | not in build |
| rime/librime 1.16.1 | `de4700e9f6b75b109910613df907965e3cbe0567` | BSD-3-Clause | dynamically loaded by `ClipVaultImeHost.exe` only | local x64 build/test input |
| rime/rime-pinyin-simp | `0c6861ef7420ee780270ca6d993d18d4101049d0` | Apache-2.0 | dictionary-only external build input | hash-locked production candidate |

## Official Windows SDK lock

The only accepted binary SDK for this P1 is:

```text
asset:  rime-de4700e-Windows-msvc-x64.7z
source: https://github.com/rime/librime/releases/tag/1.16.1
sha256: e17c1bb4acc9934669e7a62003aef3f8b56d0afa89e5d893ed7dbf34546abb6e
size:   7,373,905 bytes
```

The machine-readable record is
`windows/ime/rime/RIME_SDK_LOCK.json`; `windows/ime/scripts/Prepare-RimeSdk.ps1`
downloads only that URL into a caller-selected external cache and verifies the
hash before extraction. No third-party source or DLL is committed here.

The recursively inspected source checkout has these immutable native
dependencies:

| Path | Commit | Declared license |
|---|---|---|
| `deps/glog` | `7b134a5c82c0c0b5698bb6bf7a835b230c5638e4` | BSD-3-Clause |
| `deps/googletest` | `f8d7d77c06936315286eb55f8de22cd23c188571` | BSD-3-Clause |
| `deps/leveldb` | `99b3c03b3284f5886f9ef9a4ef703d57373e61be` | BSD-3-Clause |
| `deps/leveldb/third_party/benchmark` | `bf585a2789e30585b4e3ce6baf11ef2750b54677` | Apache-2.0 |
| `deps/leveldb/third_party/googletest` | `c27acebba3b3c7d94209e0467b0a801db4af73ed` | BSD-3-Clause |
| `deps/marisa-trie` | `3e87d53b78e15f2f43783d5e376561a8c9722051` | BSD-2-Clause OR LGPL-2.1-or-later |
| `deps/opencc` | `556ed22496d650bd0b13b6c163be9814637970ae` | Apache-2.0 |
| `deps/yaml-cpp` | `2f86d13775d119edbb69af52e5f566fd65c6953b` | MIT |

The verified librime BSD text is retained at
`windows/ime/rime/LICENSE-librime.txt`. This does not by itself satisfy release
notices for all statically linked components.

## Boundaries and open release gates

- `ClipVaultTextService.dll` must not load librime, Python, SQLite, networking,
  synchronization, clipboard monitoring, or AI code.
- `rime.dll` is copied beside and loaded only by `ClipVaultImeHost.exe`.
- Production builds stage only the ClipVault-owned schemas/punctuation and
  `pinyin_simp.dict.yaml`, `LICENSE`, and `AUTHORS` whose hashes are frozen in
  `shared-input/rime/RIME_ASSET_LOCK.json`; upstream schema/prelude assets are
  not accepted.
- Production packaging still needs the complete transitive NOTICE/license
  closure, approved schema/dictionary/OpenCC provenance, signed DLL/EXE/MSI,
  x86/ARM64 coverage, and application compatibility evidence.
- Local build and the now-rolled-back mixed HKCU COM/HKLM TSF registration test
  do not authorize upload, distribution, stable-release claims, or leaving a
  system-wide profile installed.
