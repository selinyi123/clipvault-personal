# THIRD_PARTY_NATIVE — librime Android PoC

Status: **NOT APPROVED**. This file is a distribution gate, not a best-effort
notice list. No native/binary PoC artifact may be uploaded while any required
row is incomplete or unapproved.

## Track A — custom librime JNI

| Component | Role | Version/SHA | SPDX | Combination | Binary/data path | Obligations | Approval |
|---|---|---|---|---|---|---|---|
| rime/librime | Chinese input engine | 1.16.1 / `de4700e9f6b75b109910613df907965e3cbe0567` | BSD-3-Clause | planned native/JNI | unresolved | license text, copyright, transitive inventory | pending |
| librime submodules/dependencies | native transitive closure | unresolved | unresolved | unresolved | unresolved | source, notices, patches, relink/source duties as applicable | blocked |
| Rime schema | input schema | unresolved | unresolved | packaged data | unresolved | exact source/SHA/license/NOTICE | blocked |
| Rime dictionary | candidate data | unresolved | unresolved | packaged data | unresolved | exact source/SHA/license/NOTICE | blocked |
| ClipVault JNI wrapper | minimal adapter | not implemented | project license | JNI | planned | must be original implementation; do not copy GPL Trime code | pending |

## Track B — fcitx5 external addon

| Component | Role | Version/SHA | SPDX | Combination | Binary/data path | Obligations | Approval |
|---|---|---|---|---|---|---|---|
| fcitx5-android | separately installed IME framework | 0.1.3 / `048f581c652367567b8ee5c28c5163b805288895` | LGPL-2.1-only | external application | external APK(s) | verify source/relink/notices for distributed bundle | pending |
| fcitx5 Rime plugin/data | Rime engine integration | unresolved | unresolved | external plugin/data APK | unresolved | exact release/SHA/license/source/relink delivery | blocked |
| ClipVault test addon | inject one synthetic candidate | not implemented | project license plus linked-boundary review | external addon APK | planned | record Kotlin/C++/IPC boundary and linked dependencies | pending |
| addon transitive dependencies | native/Kotlin closure | unresolved | unresolved | unresolved | unresolved | complete source/notices/relink inventory | blocked |

## Reference-only projects

| Component | Version/SHA | SPDX | Allowed use |
|---|---|---|---|
| osfans/trime | v3.3.10 / `11440ffceb618b68deeddf4bdf7497b082cb87ae` | GPL-3.0-or-later | Study architecture and upstream interfaces only. Do not copy source, build scripts, or JNI code into ClipVault. |

## Approval record

- License reviewer: unresolved
- Track A approval: not approved
- Track B approval: not approved
- Binary artifact upload: prohibited
- Production integration: prohibited
