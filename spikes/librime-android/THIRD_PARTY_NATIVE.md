# THIRD_PARTY_NATIVE — librime Android PoC

Status: **NOT APPROVED**. This file is a distribution gate, not a best-effort
notice list. No native/binary PoC artifact may be uploaded while any required
row is incomplete or unapproved.

## Track A — custom librime JNI

The source identities below are frozen in `A_ROUTE_SOURCE_LOCK.json`. License
families have been verified where an upstream license was directly available,
but that verification is not distribution approval. Actual package paths,
notices, source delivery and modification records still have to be completed
from the built artifact closure.

| Component | Role | Version/SHA | SPDX | Planned combination | Runtime | Required distribution work | Approval |
|---|---|---|---|---|---|---|---|
| rime/librime | Chinese input engine | 1.17.0 / `33e78140250125871856cdc5b42ddc6a5fcd3cd4` | BSD-3-Clause | static into project JNI `.so` | yes | license/copyright, source identity, patches | pending |
| Boost | regex and headers | 1.89.0 / `ef7fea34711a189472893b88205b1dd3c275677b` | BSL-1.0 | static/header use | yes | retain required license material and source identity | pending |
| google/glog | optional logging | `7b134a5c82c0c0b5698bb6bf7a835b230c5638e4` | BSD-3-Clause | excluded with `ENABLE_LOGGING=OFF` | no | prove absent from final closure | pending |
| google/leveldb | Rime storage | `99b3c03b3284f5886f9ef9a4ef703d57373e61be` | BSD-3-Clause | static | yes | license/copyright and source identity | pending |
| yaml-cpp | configuration parser | `2f86d13775d119edbb69af52e5f566fd65c6953b` | MIT | static | yes | license/copyright and source identity | pending |
| googletest | upstream tests | `f8d7d77c06936315286eb55f8de22cd23c188571` | BSD-3-Clause | excluded with `BUILD_TEST=OFF` | no | prove absent from final closure | pending |
| marisa-trie | trie implementation | `0d4e8ab58eec355facf8f65ff11ef811b330e373` | BSD-2-Clause OR LGPL-2.1-or-later | static; BSD-2-Clause selected | yes | BSD notice/copyright and source identity | pending |
| OpenCC | script conversion | `556ed22496d650bd0b13b6c163be9814637970ae` | Apache-2.0 | static; reuse locked marisa | yes | Apache license, NOTICE if present, modification notice | pending |
| OpenCC vendored RapidJSON | OpenCC JSON configuration headers | 1.1.0 inside OpenCC SHA `556ed224…` | MIT | headers compiled into OpenCC | yes | retain RapidJSON license/copyright and record vendored path | pending |
| OpenCC vendored TCLAP | OpenCC command-line tools | 1.2.5 inside OpenCC SHA `556ed224…` | NOASSERTION | must be excluded by an auditable library-only patch | no | classify license and prove tools/TCLAP absent | blocked |
| OpenCC vendored darts-clone | optional Darts dictionary | 0.32 inside OpenCC SHA `556ed224…` | NOASSERTION | excluded with `ENABLE_DARTS=OFF` | no | classify license and prove Darts sources/objects absent | blocked |
| ClipVault bridge/JNI | original adapter | host contract implemented; native backend pending | project license unresolved | JNI shared library | planned | original implementation; do not copy GPL Trime code | pending |

The planned closure disables glog, upstream tests, timestamps, Darts, benchmark,
Python bindings and accidental Snappy discovery. It also reuses the locked
marisa library. OpenCC's current CMake graph still adds its command-line tools
unconditionally; therefore the Android PoC requires a small, repository-owned,
replayable **library-only patch** before any native build result can be accepted.
The patch must skip `src/tools` rather than merely delete produced executables.
Until the patched source graph and final object/ELF closure are inspected,
TCLAP and darts-clone remain unresolved even though they are intended to be
excluded.

This is a build policy, not evidence that the resulting Android binary has the
claimed closure. The final `.so` inventory and build trace must prove it.

## Project-owned deterministic PoC data

These files are pinned and may be used for local/CI compilation and text-only
reports. Their final redistribution license still requires Owner approval, so
they do not authorize uploading APK, AAB, `.so` or addon artifacts.

| Component | Content SHA-256 | License | Path | Approval |
|---|---|---|---|---|
| Rime schema | `ec39c3c59da62f7c8e6d6b81a6043a8534a0586b5e070fb65a3cb4e7139416f0` | LicenseRef-ClipVault-Owner-Approval-Pending | `data/clipvault_poc.schema.yaml` | local-build only |
| Synthetic dictionary | `e7147b4d96d271fe358a634149fbc61c319fb7541ca7bea4433f7f7c5951141d` | LicenseRef-ClipVault-Owner-Approval-Pending | `data/clipvault_poc.dict.yaml` | local-build only |
| Default configuration | `edcc5cf7ba1e384d5b4ffa83459c1ed423fd1c0b5aa643cdf60e6a4d5c81fed4` | LicenseRef-ClipVault-Owner-Approval-Pending | `data/default.yaml` | local-build only |

The dictionary contains four synthetic mappings only. It is not a production
lexicon and contains no user data.

## Track B — fcitx5 external addon

| Component | Role | Version/SHA | SPDX | Combination | Binary/data path | Obligations | Approval |
|---|---|---|---|---|---|---|---|
| fcitx5-android | separately installed IME framework | 0.1.3 / `048f581c652367567b8ee5c28c5163b805288895` | LGPL-2.1-only | external application | external APK(s) | verify source/relink/notices for distributed bundle | pending |
| fcitx5 Rime plugin | Rime engine integration | unresolved | unresolved | external plugin APK | unresolved | exact release/SHA/license/source/relink delivery | blocked |
| ClipVault test addon | inject one synthetic candidate | not implemented | project license plus linked-boundary review | external addon APK | planned | record Kotlin/C++/IPC boundary and linked dependencies | pending |
| addon transitive dependencies | native/Kotlin closure | unresolved | unresolved | unresolved | unresolved | complete source/notices/relink inventory | blocked |

## Reference-only projects

| Component | Version/SHA | SPDX | Allowed use |
|---|---|---|---|
| osfans/trime | v3.3.10 / `11440ffceb618b68deeddf4bdf7497b082cb87ae` | GPL-3.0-or-later | Study architecture and upstream interfaces only. Do not copy source, build scripts or JNI code into ClipVault. |

## Approval record

- License reviewer: unresolved
- Project-owned PoC data redistribution license: pending Owner decision
- OpenCC library-only patch: required, not implemented
- TCLAP and darts-clone license classification: unresolved
- Track A approval: not approved
- Track B approval: not approved
- Binary artifact upload: prohibited
- Production integration: prohibited
