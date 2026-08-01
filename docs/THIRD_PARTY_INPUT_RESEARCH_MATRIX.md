# Third-party input research matrix

> Status: active discovery ledger (2026-08-01). A row is not an adoption decision. Production use needs
> pinned-source, build, license, data and upgrade evidence.

## Method

Discovery targets at least 20 projects in each group: Android keyboards; engines/cross-platform
frameworks; Windows TSF clients; OTP capture/autofill; pairing/ephemeral transport; ASR/correction.
Only projects that can change a decision advance to source review or build PoC.

The table below is the currently verified **build-level shortlist**, not the requested six-group
20+-per-group discovery matrix. The broader 120+ pool discussed during planning remains a research backlog;
it must be populated with canonical URLs and live maintenance/license checks before this file may claim
that coverage. Names from chat notes alone are unverified leads, not repository evidence.

Required fields for every shortlisted project:

| Field | Requirement |
|---|---|
| Identity | canonical upstream URL, tag and immutable commit |
| License | root, files, submodules, generated code, data/schema/dictionary/model and installer |
| Maintenance | latest release/commit and supported OS/architectures |
| Boundary | engine/UI/platform/transport ownership and process/permission model |
| Reuse mode | dependency, adapter, companion, thin fork, PoC-only, reference or excluded |
| Evidence | clean build, tests, installed behavior and exact toolchain |
| Cost | modified paths, patch count, bootstrap time and fixed-version upgrade drill |

## Build-level shortlist

| Group | Project and pin | License snapshot | Intended use | Current status |
|---|---|---|---|---|
| Android | librime 1.16.1 `de4700e9f6b75b109910613df907965e3cbe0567` | BSD-3-Clause; transitive/data unresolved | shared Chinese engine | isolated A-route bootstrap |
| Android | fcitx5-android 0.1.3 `048f581c652367567b8ee5c28c5163b805288895` | LGPL-2.1; plugin/data review required | B route and shell/addon boundary | build evidence pending |
| Android | Trime v3.3.10 `11440ffceb618b68deeddf4bdf7497b082cb87ae` | GPL-3.0-or-later | behavior/build reference only | no code copying |
| Windows | TypeDuck-HK/TypeDuck-Windows `1ac3af3b44e7478a0f1c7c153bceabf6aa7efb3b` | MIT root; submodules/data unresolved | TSF/launcher/process PoC | source/build review pending |
| Windows | TypeDuck-HK/TypeDuck-Windows-backend `af3636a40c9081a7862664e422a6e34ac69fafd6` | MIT root; runtime assets unresolved | external Go/librime Host PoC | source/build review pending |
| Windows | EasyIME/libIME2 `717b1901a417667405399cfbf25b25664efcf0e4` | LGPL-2.1 | TSF wrapper used by PoC candidate | relink/patch review pending |
| Windows | librime 1.16.1, same stable pin as Android | BSD-3-Clause; transitive/data unresolved | Windows engine core | build evidence pending |
| OTP | Android Inline Autofill platform API | platform API | protected suggestion presentation | design/compatibility PoC |
| OTP | CompanionDeviceManager platform API | platform API | pairing/background capability reference | not SMS permission or transport |
| OTP/transport | KDE Connect | license/subprojects require review | pairing/plugin threat-model reference | reference only |

Pins above were verified against canonical upstream metadata or the existing locked Android PoC on
2026-08-01. They are immutable research inputs, not permission to vendor or distribute binaries.

## Decision rules

- Android shell selection remains under ADR-0010/V2-S004 until both A and B have complete pass/fail
  evidence. Feature count, stars or screenshots do not override the fixed algorithm.
- Windows TypeDuck/libIME2 is the first implementation PoC, not a production verdict. Failure triggers a
  documented fallback evaluation, not unbounded project hopping.
- GPL projects may be independently used or studied under their terms; source is not copied into an
  incompatible module.
- Engine license never proves dictionary, schema, model, image or installer redistributability.
- Floating branches and “latest” downloads are forbidden in reproducible PoCs.
