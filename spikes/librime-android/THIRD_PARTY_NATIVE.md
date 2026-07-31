# THIRD_PARTY_NATIVE — librime Android PoC

Status: **NOT APPROVED**. This file is a distribution gate, not a best-effort
notice list. No native/binary PoC artifact may be uploaded while any required
row is incomplete or unapproved.

## Track A — custom librime JNI

| Component | Role | Version/SHA | SPDX | Combination | Binary/data path | Obligations | Approval |
|---|---|---|---|---|---|---|---|
| rime/librime | Chinese input engine | 1.16.1 / `de4700e9f6b75b109910613df907965e3cbe0567` | BSD-3-Clause | planned native/JNI | unresolved | license text, copyright, transitive inventory | pending |
| librime submodules/dependencies | native transitive closure | unresolved | unresolved | unresolved | unresolved | source, notices, patches, relink/source duties as applicable | blocked |
| ClipVault JNI wrapper | minimal adapter | not implemented | project license unresolved | JNI | planned | must be original implementation; do not copy GPL Trime code | pending |

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
| ClipVault test addon | inject one synthetic candidate | not implemented | project license plus linked-boundary review | external addon APK | planned | record Kotlin/C++/IPC boundary and linked dependencies | pending |
| addon transitive dependencies | native/Kotlin closure | unresolved | unresolved | unresolved | unresolved | complete source/notices/relink inventory | blocked |

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
