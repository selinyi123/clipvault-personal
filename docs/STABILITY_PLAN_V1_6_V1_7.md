# ClipVault Personal v1.6/v1.7 Stability Plan

Date: 2026-07-03

This plan is the execution map for turning the current source tree into stable
v1.6 and then a stable v1.7 line. It follows `AGENTS.md`: do not start feature
work that bypasses the current release gate, keep the Android IME local-first,
do not log typed text, do not add analytics, keep network work outside the IME
service, and require explicit user action for saving content.

## Current release posture

| Area | Current evidence | Status |
|---|---|---|
| Source metadata | `docs/VERSION_SYNC.md`, `desktop/tests/test_release_alignment.py` | Aligned at `1.6.0` / Android `versionCode=13` |
| CI | Main CI for the current main commit | Automated gate is available |
| Packaging dry run | `Release candidate dry run` workflow | Unsigned packaging evidence; PR path-filtered and main-push automated |
| Signed Android APK | `Release artifact build` workflow + `release` environment | Blocked until Owner configures environment secrets |
| GitHub Release | `v1.6.0` release asset publication | Blocked until Owner approves release creation/publication |
| Manual QA | `docs/MANUAL_QA_V1_6_0.md` | Blocked until Owner/device evidence is recorded |

## v1.6 stable definition

v1.6 stable means the `1.6.0` source tree can be safely published as signed
artifacts without weakening the local-first/privacy boundaries.

Required evidence before closing Issue #36:

1. Current-main CI success is recorded.
2. Current-main release-candidate dry run success is recorded.
3. Owner-created `release` GitHub environment exists with the intended approval
   policy.
4. Android signing values are stored as `release` environment secrets.
5. `Release artifact build` runs with `version=v1.6.0` and
   `create_draft_release=false`.
6. The signed Android artifact contains `ANDROID_APKSIGNER_VERIFY.txt`,
   `SHA256SUMS.txt`, and `RELEASE_MANIFEST.json` with `signed=true`.
7. Manual Android device QA, IME privacy QA, sync QA, and Windows clipboard
   privacy QA are recorded on Issue #36.
8. Only after Owner approval, a draft GitHub Release may be created and reviewed.

Agent-executable work while the Owner gate remains blocked:

- Keep release/runbook docs free of stale commit IDs and stale run URLs.
- Keep README and architecture docs honest about the difference between current
  source-tree hardening and published/signed release artifacts.
- Keep workflow security least-privilege guards in tests.
- Keep packaging dry-run green on current main. The `Release candidate dry run`
  workflow should run automatically on every push to `main`, while PR runs may
  stay path-filtered to control cost.
- Fix only release, CI, documentation, or verified safety defects that do not
  change product semantics.

Agent must not claim:

- signed release completion without the signed APK and `apksigner` evidence;
- device/manual QA completion without Owner/device evidence;
- release publication without a real GitHub Release or draft release URL.

## v1.7 stable design

v1.7 should be a stability line, not a scope expansion. Start it only after the
v1.6 release gate has a clear Owner decision, or as isolated planning/test work
that does not alter runtime behavior.

Recommended v1.7 themes:

1. **IME privacy closure**
   - Extend the current sensitive-field suppression model to every explicit save
     path exposed by the IME.
   - Acceptance: host-JVM tests prove sensitive-field save suppression, and
     manual QA confirms no typed text is written to Room, logs, sync, or desktop.

2. **Manual QA automation**
   - Convert repeatable #36 IME smoke checks into `androidTest` coverage where
     emulator/device automation is reliable.
   - Acceptance: instrumentation tests cover candidate visibility/commit and
     sensitive-field suppression; real-device Owner QA remains the release gate.

3. **Release supply-chain hardening**
   - Keep `GITHUB_TOKEN` permissions minimal.
   - Keep checkout credential persistence disabled for jobs that do not perform
     authenticated git writes.
   - Consider action SHA pinning or a documented exception policy only after a
     separate maintainability review.

4. **Capture-layer privacy evidence**
   - Keep Windows registered clipboard exclusion handling covered by unit tests.
   - Add a small Windows manual/source-app QA note or harness if Owner wants
     repeatable evidence for producer-set privacy formats.

5. **Local-first sync reliability**
   - Improve observability and bounded failure reporting without adding cloud
     storage, telemetry, or network work inside `ime/`.
   - Acceptance: sync tests remain deterministic, secrets never enter outbox,
     and logs never include clip bodies.

6. **Documentation-as-release-evidence**
   - Keep user-facing README status, architecture topology, and release runbooks
     aligned with current implementation and GitHub release state.
   - Acceptance: static tests fail if README claims unpublished v1.6 binaries,
     stale fixed test counts, or signed artifacts before Issue #36 evidence
     exists; architecture docs describe HTTP push/pull sync rather than the
     retired WebSocket/FastAPI plan.

7. **Current-main packaging evidence**
   - Keep unsigned release-candidate packaging evidence tied to the exact main
     commit instead of only to PR heads or manual dispatches.
   - Acceptance: the release-candidate workflow has a `push` trigger for `main`
     without release environment/secrets/write permissions, and static tests
     fail if this dry-run path gains release side effects.

## v1.7 stable exit criteria

Do not call v1.7 stable until every row below has evidence recorded in
`docs/HANDOFF.md` and, if a release is planned, a dedicated release-gate issue.
The table separates automated, CI, and Owner/manual evidence so scaffolds do not
get mistaken for completed device validation.

| Area | Required automated evidence | Required CI evidence | Required Owner/manual evidence | Stable exit decision |
|---|---|---|---|---|
| IME privacy boundary | Host-JVM tests cover sensitive-field suppression, explicit-save suppression, in-flight candidate invalidation, IME source-boundary imports, IME manifest shape, and Android production log source-shape privacy. | Android unit/debug-unit tests and `:app:compileDebugAndroidTestKotlin` pass for the target main commit. | Real device confirms no ordinary typed text is written to Room, logs, sync payloads, desktop storage, or backup; sensitive editors hide candidates and block explicit save. | Not stable if any typed-text logging, implicit save, Android production log payload interpolation, or IME-local network path exists. |
| Manual QA automation | Residual `androidTest` checks either remain clearly `@Ignore`-d with backlog wording, or are promoted to real assertions with the `@Ignore` annotations removed. | If promoted, `connectedDebugAndroidTest` or an equivalent device/emulator workflow is recorded for the target commit. | Owner records the device/emulator model, Android version, active IME setup, and pass/fail evidence. | Not stable if compile-only scaffolds are claimed as executed QA. |
| Release supply-chain | Static tests guard workflow token permissions, checkout credential persistence, artifact upload failure on missing files, manifest verification, action major floors, and signed Android evidence requirements. | Current-main CI and release-candidate dry run both pass on the same main SHA. | Owner-controlled signing/release approval remains outside autonomous agent scope unless explicitly authorized. | Not stable if unsigned dry-run artifacts are described as signed release artifacts. |
| Capture-layer privacy | Unit tests cover Windows registered clipboard exclusion formats and Secret Guard provider parity on both desktop and Android core. | Desktop full suite and Android core/app unit tests pass for the target commit. | Windows clipboard privacy QA records a source app or harness that sets the registered privacy formats. | Not stable if secrets or producer-marked private clipboard items can enter Obsidian, GitHub backup, sync, FTS, or memory candidates. |
| Local-first sync reliability | Deterministic tests cover auth-failure token clearing, bounded pull responses, oversized-event diagnostics, duplicate event sequence handling, redirect refusal with bearer tokens, and host normalization. | Android sync unit/debug-unit tests and desktop sync tests pass for the target commit. | Owner confirms LAN/Tailscale pair, restart, and bidirectional sync smoke checks without cloud relay or telemetry. | Not stable if sync introduces cloud storage, analytics, IME-network work, or unbounded response/log exposure. |
| Documentation-as-release-evidence | Static tests guard README release truthfulness, architecture runtime topology, live-evidence runbook commands, and avoidance of stale fixed test counts. | CI includes those static tests for every PR/main commit. | Owner confirms public-facing release notes match the actual published assets before publication. | Not stable if docs imply unpublished v1.6/v1.7 signed binaries or stale test-count evidence. |
| Current-main packaging evidence | Static tests guard the release-candidate main-push path and forbid release secrets/environments/write permissions there. | Current-main dry run uploads unsigned Windows/Android candidate artifacts, `SHA256SUMS.txt`, and `RELEASE_MANIFEST.json` for the exact target SHA. | Owner separately approves any signed release workflow or draft GitHub Release creation. | Not stable if packaging evidence cannot be tied to the target main SHA. |

Stable exit rules:

- Keep `v1.7` as a planning/stability label until a dedicated release issue and
  Owner approval exist; do not publish `v1.7.0` from this plan alone.
- Treat blocked Owner/manual rows as incomplete, not as failed automation.
- If a proposed v1.7 fix changes runtime dependencies, schema semantics,
  privacy policy, sync payload shape, or release authority, pause and write an
  ADR before implementation.

## Version and branch policy

- Do not bump beyond `1.6.0` just to signal progress.
- Do not create or publish `v1.7.0` artifacts without a new release issue and
  Owner approval.
- Keep each PR tied to one stability concern: release gate, privacy gate, QA
  automation, or CI hardening.
- If a v1.7 candidate requires new runtime dependencies, schema semantics, or a
  privacy policy change, stop and write an ADR first.

## Agent workflow for the next nodes

1. **Release Gate agent:** keep #36 evidence current, compare main SHA to CI and
   dry-run SHA, and identify only owner/manual blockers.
2. **Security/CI agent:** audit workflows for token scope, secret exposure,
   artifact integrity, and checkout credential persistence.
3. **Android Privacy agent:** review `ime/`, `runtime/`, Room, and sync exits for
   typed-text leakage or sensitive-field bypasses.
4. **Patch agent:** make small, verified patches only after the audit produces a
   concrete file/line finding.
5. **Owner gate:** sign artifacts, run device/manual QA, approve any release
   creation, and decide when Issue #36 can close.

## Stop conditions

Pause and ask Owner if any proposed change would:

- add typed-text collection or analytics;
- move network work into an IME service;
- make saving implicit instead of user-triggered;
- publish or sign artifacts;
- create a GitHub Release;
- change database/schema semantics;
- introduce a runtime dependency not already covered by an ADR.
