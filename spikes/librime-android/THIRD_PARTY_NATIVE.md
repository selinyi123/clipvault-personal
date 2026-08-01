# THIRD_PARTY_NATIVE — librime Android PoC

Status: **NOT APPROVED**. This file is a distribution gate, not a best-effort
notice list. No native/binary PoC artifact may be uploaded while any required
row is incomplete or unapproved.

## Track A — custom librime JNI

The exact direct inputs below are locked for investigation. A checked license
identifier is not the same as approval of the final combined binary. Nested
components, generated data, actual packaged paths, notices, and source/relink
obligations remain to be resolved from a completed build manifest.

| Component | Role | Exact input | SPDX | Planned inclusion | Distribution status |
|---|---|---|---|---|---|
| rime/librime | Chinese input engine | 1.16.1 / `de4700e9f6b75b109910613df907965e3cbe0567` | BSD-3-Clause | shared engine | pending: binary path, license text, copyright and closure |
| Boost | headers / Boost.Regex | 1.89.0 archive, SHA-256 `9de758db755e8330a01d995b0a24d09798048400ac25c03fc5ea9be364b13c93` | BSL-1.0 | yes | pending: exact built components and binary linkage manifest |
| google/glog | optional logging backend | `7b134a5c82c0c0b5698bb6bf7a835b230c5638e4` | BSD-3-Clause | no | excluded by `ENABLE_LOGGING=OFF`; build must prove absence |
| google/leveldb | database backend | `99b3c03b3284f5886f9ef9a4ef703d57373e61be` | BSD-3-Clause | yes | pending: packaged path and notices |
| jbeder/yaml-cpp | configuration parser | `2f86d13775d119edbb69af52e5f566fd65c6953b` | MIT | yes | pending: packaged path and notices |
| google/googletest | upstream tests | `e2239ee6043f73722e7aa812a459f54a28552929` | BSD-3-Clause | no | excluded by `BUILD_TEST=OFF`; build must prove absence |
| s-yata/marisa-trie | compact trie backend | `0d4e8ab58eec355facf8f65ff11ef811b330e373` | BSD-2-Clause OR LGPL-2.1-or-later | yes | pending: chosen license path, packaged path and obligations |
| BYVoid/OpenCC | script-conversion backend | `556ed22496d650bd0b13b6c163be9814637970ae` | Apache-2.0 | yes | pending: nested/generated data, NOTICE and packaged paths |
| ClipVault JNI wrapper | minimal original adapter | repository source | project license unresolved | yes | no Trime source/build/JNI code may be copied |

Track A closure status: **INCOMPLETE_FAIL_CLOSED**.

Unresolved items include:

- OpenCC nested build inputs, generated dictionaries/configuration and data
  redistribution obligations;
- exact Boost components and whether Boost.Regex enters a shared object;
- ABI-specific `.so` dependency closure and entry paths in the spike APK;
- complete license/NOTICE delivery layout;
- reproducible source/build manifest and all replayable patches.

## Project-owned deterministic PoC data

These files are pinned and may be used for local/CI compilation and text-only
reports. Their final redistribution license still requires Owner approval, so
they do not authorize uploading APK, AAB, `.so`, or addon artifacts.

| Component | Content SHA-256 | License | Path | Approval |
|---|---|---|---|---|
| Rime schema | `ec39c3c59da62f7c8e6d6b81a6043a8534a0586b5e070fb65a3cb4e7139416f0` | LicenseRef-ClipVault-Owner-Approval-Pending | `data/clipvault_poc.schema.yaml` | local-build only |
| Synthetic dictionary | `e7147b4d96d271fe358a634149fbc61c319fb7541ca7bea4433f7f7c5951141d` | LicenseRef-ClipVault-Owner-Approval-Pending | `data/clipvault_poc.dict.yaml` | local-build only |
| Default configuration | `edcc5cf7ba1e384d5b4ffa83459c1ed423fd1c0b5aa643cdf60e6a4d5c81fed4` | LicenseRef-ClipVault-Owner-Approval-Pending | `data/default.yaml` | local-build only |

The dictionary contains four synthetic mappings only. It is deliberately not a
production lexicon and contains no user data.

## Track B — fcitx5 external addon

| Component | Role | Version/SHA | SPDX | Combination | Binary/data path | Obligations | Approval |
|---|---|---|---|---|---|---|---|
| fcitx5-android | separately installed IME framework | 0.1.3 / `048f581c652367567b8ee5c28c5163b805288895` | LGPL-2.1-only | external application | external APK(s) | verify source/relink/notices for distributed bundle | pending |
| fcitx5 Rime plugin | Rime engine integration | unresolved | unresolved | external plugin APK | unresolved | exact release/SHA/license/source/relink delivery | blocked |
| Rime schema/data APKs | plugin runtime data | unresolved | per repository/data | external APK/data | unresolved | exact data sources, hashes and redistribution terms | blocked |
| ClipVault test addon | inject one synthetic candidate | not implemented | project license plus linked-boundary review | external addon APK | planned | record Kotlin/C++/IPC boundary and linked dependencies | pending |
| addon transitive dependencies | native/Kotlin closure | unresolved | unresolved | unresolved | unresolved | complete source/notices/relink inventory | blocked |

The official fcitx5-android repository contains a framework, prebuilt native
bundle, Rime plugin and multiple Rime data submodules. That larger closure is
one reason Track B remains a separately measured fallback rather than an
assumed lower-cost replacement.

## Reference-only projects

| Component | Version/SHA | SPDX | Allowed use |
|---|---|---|---|
| osfans/trime | v3.3.10 / `11440ffceb618b68deeddf4bdf7497b082cb87ae` | GPL-3.0-or-later | Study architecture and upstream interfaces only. Do not copy source, build scripts, or JNI code into ClipVault. |

## Approval record

- License reviewer: unresolved
- Project-owned PoC data redistribution license: pending Owner decision
- Track A approval: not approved
- Track B approval: not approved
- Binary artifact upload: prohibited
- Production integration: prohibited
