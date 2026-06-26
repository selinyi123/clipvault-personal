# v1.5.16 Release Audit

Date: 2026-06-25

This file records the repository evidence for v1.5.16.

## Version files

- `desktop/clipvault/__init__.py`: 1.5.16
- `desktop/pyproject.toml`: 1.5.16
- `android/app/build.gradle.kts`: versionName 1.5.16, versionCode 12
- `installer/clipvault.iss`: 1.5.16
- `docs/VERSION_SYNC.md`: aligned at 1.5.16
- `docs/HANDOFF.md`: current slice is v1.5.16

## Functional files

- `android/app/src/main/kotlin/com/clipvault/app/runtime/ClipVaultFacade.kt`
- `android/app/src/main/kotlin/com/clipvault/app/ime/PanelCandidateTabs.kt`
- `android/app/src/main/kotlin/com/clipvault/app/ime/ClipVaultPanelImeService.kt`
- `android/app/src/main/kotlin/com/clipvault/app/ime/ClipVaultFullKeyboardService.kt`
- `android/app/src/main/kotlin/com/clipvault/app/data/Db.kt`
- `android/app/src/main/kotlin/com/clipvault/app/sync/Sync.kt`

## Test files

- `android/app/src/test/kotlin/com/clipvault/app/runtime/CandidateMixerTest.kt`
- `android/app/src/test/kotlin/com/clipvault/app/ime/PrivacyAwareFilterTest.kt`
- `android/app/src/test/kotlin/com/clipvault/app/ime/PanelCandidateTabsTest.kt`
- `desktop/tests/test_api.py`
- `desktop/tests/test_config.py`
- `desktop/tests/test_sync.py`

## Validation files

- `.github/workflows/ci.yml`
- `docs/MANUAL_QA_V1_5_16.md`
- `docs/AGENT_WORKFLOWS.md`

## Review fixes after diff audit

- Android dependency declarations are present in `android/app/build.gradle.kts`.
- HANDOFF keeps current v1.5.16 state and restores a compact project-memory snapshot.
- The old manual QA file was replaced by `docs/MANUAL_QA_V1_5_16.md`.

## Risk fixes after broader review

- Panel tabs now request source/kind-specific candidates from Runtime.
- Android pull now mirrors clip pinned/favorite metadata.
- Android sync worker logs only exception classes.
- Android pull ignores unknown or malformed individual events instead of failing the entire batch.
- Desktop API query parameter validation rejects malformed or negative values while preserving high-value clamping.
- Desktop server request handling has explicit size guards.
- New desktop config templates bind to loopback by default.
- Desktop HTTP server version follows package metadata.
- Release endpoint remains bodyless for compatibility.

## Post-review hardening (PR #4)

A follow-up review of the v1.5.16 diff produced these fixes:

- Android pull now skips only events that fail JSON parsing (permanently bad
  payloads). Other failures propagate so `SyncWorker` retries instead of
  advancing the sync cursor past an event it did not actually apply. This
  refines the earlier "ignore malformed events" change, which could otherwise
  drop a valid event on a transient DB error.
- Desktop JSON body cap raised above `max_clip_bytes` so a maximum-size clip
  still round-trips once wrapped in JSON and escaped (was a spurious 413).
- Desktop bodyless routes (`/release`, `/memory/{id}/use`, `DELETE /memory`)
  drain any unread request body, so a stray body cannot desync a reused
  connection if HTTP keep-alive is ever enabled.
- `test_d8_release_endpoint_remains_bodyless` was rewritten to drive the real
  server over a socket with a per-thread connection; it previously failed on a
  clean checkout with a cross-thread SQLite error.
- The loopback-bind default is now discoverable: `/api/pair/code` and
  `/api/status` report `lan_reachable`, the Web UI shows a warning, and the
  Android pairing-failure message points at `server.host`.

## Verification workflow status

- CI workflow supports `workflow_dispatch` for manual verification on main.
- Manual QA checklist includes CI trigger instructions.
- Agent workflow treats manual CI dispatch evidence as acceptable CI evidence.

## Remaining review risks

- CI is green on PR #4 head (Actions run 28230052875): "Desktop tests" and
  "Android tests and debug build" both pass, which covers desktop tests, Android
  unit tests, and the Android debug build.
- Manual QA (Full Keyboard / Panel IME on a device) is still human-pending and
  is the only remaining v1.5 close-criterion.

## User-facing release note: default bind is now loopback

v1.5.16 changes the default `[server] host` from `0.0.0.0` to `127.0.0.1`
(secure by default). Impact and guidance:

- Existing `config.toml` files are unaffected: the loopback default only applies
  to newly written templates and configs that omit the `host` key.
- With the loopback default, a paired Android device on the LAN cannot reach
  `/api/pair` or `/api/sync/*` — pairing and sync will fail with a connection
  error until the user opts in.
- To re-enable LAN sync, set `[server] host = "0.0.0.0"` (only on a trusted
  LAN/Tailscale network) and restart ClipVault. The management API stays
  loopback-only regardless, enforced by the per-route handler guard.
- Discoverability: the desktop Web UI pairing code now shows a warning when the
  server is bound to loopback (`lan_reachable` / `hint` fields on
  `/api/pair/code` and `lan_reachable` on `/api/status`), and the Android
  pairing-failure message points the user at the `server.host` setting.

## Result

v1.5.16 is not only a version-number bump. The repository contains version metadata, runtime candidate logic, panel tab filtering, input frontend wiring, tests, CI definition, and manual QA gate for this release state.

## Remaining gate

CI status is now recorded (Actions run 28230052875, green). Per the workflow the
QA role does not close the release issue alone, so Issue 3 should remain open
until the manual Full Keyboard / Panel IME checks are recorded by a human.
