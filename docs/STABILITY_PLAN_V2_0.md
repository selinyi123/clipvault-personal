# ClipVault Personal v2.0 Stability Plan

Date: 2026-07-04

This plan defines what "v2.0 stable" means for the keyboard mainline. It does
not change the current release authority: Issue #36 remains the v1.6.0 release
gate, and `docs/STABILITY_PLAN_V1_6_V1_7.md` remains the v1.7 stability gate.

## Scope lock

The authoritative v2.0 scope is `ROADMAP_V2_KEYBOARD.md` and `GATES.md`:

- v2.0 means the same APK exposes two IME entrypoints:
  - ClipVault Panel IME.
  - ClipVault Keyboard Lab with a basic English QWERTY keyboard and ClipVault
    toolbar.
- v2.0 does not mean the v2.1 librime/fcitx5 production engine.
- v2.0 does not mean the optional LAN TLS transport-hardening branch. That
  remains a support-line candidate unless a future ADR and Owner decision
  deliberately rename the release line.

## Prerequisites before v2.0 can be called stable

Do not claim v2.0 stable until all prerequisites are true and recorded in
`docs/HANDOFF.md` plus a dedicated v2.0 release-gate issue:

1. Issue #36 / v1.6.0 is closed with current-main CI, current-main
   release-candidate dry run, Owner-controlled final Windows artifacts, signed
   Android artifacts, manual QA evidence, and Owner-approved GitHub Release
   publication.
2. The v1.7 stable exit criteria in
   `docs/STABILITY_PLAN_V1_6_V1_7.md` are satisfied, or Owner explicitly
   defers a listed v1.7 row in the v2.0 release-gate issue.
3. A dedicated v2.0 release-gate issue exists and lists automated, CI, and
   Owner/manual evidence rows.
4. Version metadata and public release notes are aligned with the actual
   published assets. A planning label or source-tree version is not release
   evidence.

## v2.0 stable exit criteria

| Area | Required automated evidence | Required CI evidence | Required Owner/manual evidence | Stable exit decision |
|---|---|---|---|---|
| Dual IME registration | Static tests confirm both IME services remain declared as system IMEs, protected by `android.permission.BIND_INPUT_METHOD`, and mapped to the expected config XML files. | Android unit/debug-unit tests and debug APK build pass for the target main SHA. | Real device shows both "ClipVault Panel" and "ClipVault Keyboard Lab" can be enabled from Android input-method settings. | Not stable if either IME cannot be selected or if a non-IME component is exposed as an IME. |
| Keyboard Lab baseline controls | Host-JVM tests cover basic QWERTY, one-shot shift, symbol layer, space, enter, backspace, toolbar refresh, and switch-back decision logic. | Android app tests pass for the target main SHA. | Real device confirms the Keyboard Lab can type, delete, switch layers, insert a recent clip through the toolbar, and return to the previous IME. | Not stable if the Lab keyboard is only source-present but not usable as an input method. |
| Panel IME baseline controls | Host-JVM tests cover panel candidate filtering, tab behavior, explicit save routing, and sensitive-session invalidation. | Android app tests and residual androidTest source compilation pass. | Real device confirms panel launch, tab switching, candidate commit, explicit save button behavior, and switch-back behavior. | Not stable if manual evidence relies only on ignored androidTest scaffolds. |
| IME privacy boundary | Host-JVM/static tests prove no IME source imports sync/network/capture/persistence/logging paths directly, no typed-text logging is introduced, sensitive fields suppress candidates, and explicit save is blocked in sensitive sessions. | Android privacy/source-shape tests pass for the target main SHA. | Real device verifies ordinary typing leaves no Room row, sync event, desktop record, backup record, or log payload; password/incognito fields hide candidates and block explicit save. | Not stable if L0/L1 typed text is persisted, learned, logged, synced, or saved without explicit user action. |
| Local-first runtime compatibility | Existing desktop sync, Secret Guard, memory, and web/API tests pass without schema or sync-payload drift unless covered by a separate ADR. | Current-main CI and release-candidate dry run pass for the target SHA. | Owner confirms LAN/Tailscale sync smoke still works with both IME entrypoints installed. | Not stable if v2.0 changes runtime contract, sync payload shape, or local-first architecture without a reviewed migration path. |
| Documentation and release truth | Static tests guard this plan, `AGENTS.md`, `AGENT_WORKFLOWS.md`, `ROADMAP_V2_KEYBOARD.md`, and `HANDOFF.md` against claiming v2.0 stable from planning docs alone. | CI includes those static tests for every PR/main commit. | Owner confirms public release notes match actual v2.0 assets and known limitations. | Not stable if docs imply v2.0 is released, signed, or complete before the v2.0 gate issue is closed. |

## Agent workflow to v2.0

Use this order. Do not start a later lane to avoid evidence missing in an earlier
lane.

1. **Release-gate lane:** keep Issue #36 and v1.7 exit evidence current. This
   lane blocks any stable v2.0 claim but does not block planning.
2. **V2.0 audit lane:** compare current Panel IME and Keyboard Lab behavior
   against `GATES.md`, `CONTRACTS_KEYBOARD.md`, and `KEYBOARD_PRIVACY.md`.
3. **Static guard lane:** add tests for any claim that can be proven without a
   device, especially IME service exposure, no typed-text logging, no IME network
   path, and documentation truthfulness.
4. **Device-evidence lane:** prepare Owner-run manual QA scripts/checklists for
   Android input-method selection, Keyboard Lab typing, Panel operations,
   sensitive-field privacy, and LAN sync smoke.
5. **Release lane:** only after the v2.0 release-gate issue has Owner approval,
   build/sign/publish artifacts under the release runbook for the selected
   version.

## Explicit non-goals for v2.0

- Do not wire librime/fcitx5 into the production IME. That is v2.1 and remains
  gated by `docs/SLICES/V2-S004-librime-build-poc.md`.
- Do not start v2.2 CandidateMixer until the v2.1 engine choice is proven and
  approved.
- Do not add typed-text learning, analytics, behavior profiling, cloud AI, or
  automatic saving of committed text.
- Do not add network work inside any IME service.
- Do not treat optional LAN TLS, discovery, or cloud relay work as v2.0 stable
  evidence unless a future ADR explicitly changes the roadmap.

## Research notes used for this plan

- Android documents IMEs as system-selected input methods, so v2.0 requires
  real input-method selection evidence, not only source registration.
- OWASP MASVS separates mobile storage, crypto, auth, network, and platform
  controls; v2.0 evidence is therefore split across storage/privacy, network
  boundary, platform IME exposure, and Owner/manual checks.
- SemVer's public API/release semantics reinforce that a version label needs a
  declared compatibility surface and immutable released contents. ClipVault
  treats docs/contracts plus published artifacts as that evidence surface.
- GitHub task lists remain useful as release-gate checklists only when each row
  maps to inspectable evidence.
