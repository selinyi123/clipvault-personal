# ClipVault Personal — HANDOFF（项目记忆）

本文件是 repo 记忆。不在本文件或所链接 docs 中记录的事 = 没发生。

## Current Project State

| Field | Value |
|---|---|
| Project | ClipVault Personal |
| Mode | Personal / non-commercial |
| Primary platform | Windows Desktop (Python 3.12) |
| Mobile platform | Android (Kotlin) |
| Knowledge base | Obsidian |
| Repo | github.com/selinyi123/clipvault-personal（public；源码仓库不含个人数据，运行时备份用独立 private 仓库） |
| Backup | GitHub private repo (JSONL only) |
| Realtime sync | LAN / Tailscale HTTP push-pull sync |
| Source of truth | SQLite local store |
| Current slice | v2.1 V2-S004 双 build PoC 规划收口（堆叠在 SG-1.3 + IME privacy 分支上）：NDK r28/16KB、全依赖许可、干净黄金向量、可复现规则、工程预算与 A/B 阻塞态已冻结；尚未接 production 引擎，不改版本号。 |
| Last updated | 2026-07-03 |

## Current development note - 2026-07-03 / v1.7 stability gates in progress

- Desktop sync pull now pages by both event count and response byte budget so a
  large history cannot force one oversized mobile response; the Android sync
  client also rejects oversized response bodies.
- The Android app still needs app-level `android.permission.INTERNET` for
  explicit LAN/Tailscale sync outside the IME.
- Because Android permissions are app-scoped, v1.7 now guards the IME boundary
  with an Android host-JVM test instead of pretending the manifest can deny
  Internet to only `InputMethodService`.
- `ImeSourceBoundaryTest` scans `android/app/src/main/kotlin/com/clipvault/app/ime`
  and fails if an IME source file imports project sync, WorkManager, network
  packages, socket/HTTP APIs, or Android logging.
- GitHub Actions workflow references are being moved to Node 24-compatible
  official action majors, with a desktop static test guarding the floor.
- `test_release_alignment.py` now also guards GitHub Actions `GITHUB_TOKEN`
  least-privilege boundaries: all workflows default to `contents: read`, CI and
  release-candidate dry runs cannot request write scopes, and the release
  workflow can only escalate for provenance attestation and draft release
  creation.
- `test_webui_security.py` now runs `node --check` when Node is available so the
  packaged Web UI cannot regress to syntactically invalid JavaScript unnoticed.
- This does not change IME runtime semantics: candidate loading and explicit
  save still go through the Runtime facade; sync work remains outside the IME.
- The desktop API/Web UI now sends `Cache-Control: no-store`, `Pragma: no-cache`,
  and `Expires: 0` from the shared security-header path so clipboard, memory,
  and paired-device responses are not intentionally stored in browser caches.
- This is a local privacy hardening only; it does not change API semantics,
  sync behavior, Android IME behavior, release metadata, or publication state.
- Release-candidate and release artifact upload steps now set
  `if-no-files-found: error`, with a desktop static test guarding the workflow
  contract so missing release evidence fails the job instead of becoming a
  warning-only upload step.
- The release-candidate dry-run `pull_request.paths` filter now includes every
  repository release script invoked by that workflow, including
  `scripts/verify_release_manifest.py`, so verifier changes cannot skip the
  packaging dry run by path-filter accident.
- Android pairing no longer writes a newly typed desktop host before `/api/pair`
  succeeds. `SyncClient.pairWithHost()` redeems the one-time code against a
  temporary host and then commits pairing state fail-closed (clear token, write
  host, write fresh token), so a failed re-pair attempt cannot make background
  sync send the old bearer token to the new host.
- The release artifact upload/path-filter changes are release evidence
  hardening only; they do not sign artifacts, publish a GitHub Release, close
  Issue #36, or change runtime product behavior.
- The workflow-permission gate is release-chain hardening only; it does not
  change workflow event types, signing behavior, artifact publication, version
  metadata, or app/runtime behavior.
- The Android pairing fix is sync credential hardening only; it does not change
  token format, sync payload semantics, IME behavior, explicit-save behavior, or
  release state.
- The Web UI search debounce timer now uses module-local state instead of
  `window._t`, guarded by `test_webui_security.py`, so the local UI does not
  rely on clobberable global `window` properties.
- Artifact upload/download workflow steps are now guarded at the current
  Node 24-compatible official action majors (`upload-artifact@v7`,
  `download-artifact@v8`) so release-candidate and release jobs do not keep a
  stale artifact action runtime in the release chain.
- The PR template now includes release-gate issue hygiene: avoid GitHub
  auto-close keywords directly before release-gate issue references, and prefer
  wording such as `Issue #36 remains open` while signed artifacts/manual QA are
  still missing.
- Android sync requests now disable `HttpURLConnection` automatic redirects
  before adding `Authorization: Bearer ...`. ClipVault sync endpoints never
  redirect, so a 3xx response fails/retries instead of silently changing the
  authenticated request target.
- Android sync pull now validates `next_seq` before applying a returned page:
  a response with events or `has_more=true` must advance the cursor, while an
  empty terminal page may keep it unchanged. This prevents repeated-page loops
  without changing desktop outbox pruning semantics, which still need explicit
  durable peer acknowledgement before delivered cursors can be used for pruning.
- The draft GitHub Release job now checks out the repository with persisted git
  credentials disabled, downloads the signed release artifacts, and re-runs
  `scripts/verify_release_manifest.py` on the downloaded Windows and Android
  artifact directories before `gh release create`. This keeps downloaded
  artifact handoff verification hard-failing immediately before draft release
  asset upload.
- Draft release asset staging now fails if two downloaded artifact files would
  map to the same final GitHub Release asset name, preventing a local overwrite
  before `gh release create`.
- `scripts/verify_release_manifest.py --platform android --require-signed` now
  requires a non-empty `ANDROID_APKSIGNER_VERIFY.txt` alongside the APK, so the
  Android signed release evidence file is machine-checked rather than only a
  workflow convention.
- Android sync pairing now normalizes the user-entered desktop host before
  building `http://host:port/api` URLs. It allows plain LAN/DNS hostnames and
  bracketed IPv6, while rejecting scheme/path/query/fragment/userinfo/port-like
  strings so pairing and later sync stay scoped to an unambiguous host.
- Desktop pairing now validates the LAN-supplied Android `device_name` metadata
  before redeeming a one-time code: missing/blank names default to `device`,
  names are trimmed, and non-string, overlong, or control-character values are
  rejected without consuming the code.
- Release manifest generation and verification now reject nested artifact
  directories so every file that can reach release staging must be represented
  in `RELEASE_MANIFEST.json` and `SHA256SUMS.txt`.
- Release manifest generation and verification now reject hidden dotfile
  artifact names because GitHub's official artifact upload action excludes
  hidden files by default; v1.6 release evidence remains a flat visible file
  set unless the manifest/upload policy is explicitly redesigned.
- Desktop sync push now ignores duplicate event `seq` values within a single
  peer batch after applying the first occurrence, and treats local SQLite
  integrity conflicts from seq-valid malformed events as acknowledged no-ops.
  This preserves the existing fail-soft sync contract while preventing one
  request from applying multiple payloads for the same remote sequence number
  or wedging future sync retries on a permanently bad event.
- README now separates current source-tree progress from published release
  assets: source metadata remains `1.6.0`, but `v1.6.0` binaries are not
  published, latest downloadable binaries remain `v1.5.10`, and Issue #36 is
  still the release gate for signed artifacts, manual QA, and final
  publication. `test_release_alignment.py` guards against restoring stale fixed
  test-count claims or current-release signed-artifact wording.
- `docs/ARCHITECTURE.md` now describes the current stdlib `HTTPServer` +
  HTTP push/pull sync implementation and Android `HttpURLConnection` client
  rather than the retired FastAPI/WebSocket/syncserver/OkHttp-WebSocket plan.
  `test_release_alignment.py` guards those current runtime names.
- This README/status gate is documentation/test truthfulness only; it does not
  change product runtime behavior, sign artifacts, create a GitHub Release, or
  close Issue #36.
- Panel IME explicit clipboard saves are now guarded by a host-JVM source-shape
  test: `saveClipboard()` must check the active `ImePrivacySession` before
  reading the clipboard and must re-check inside the worker before calling
  `runtime.saveExplicit(...)`. This is an IME privacy regression guard only; it
  does not change runtime behavior, typed-text policy, sync behavior, release
  state, or the explicit user action boundary.
- Desktop sync pull now fails closed with a bounded HTTP 413
  `sync_event_too_large` error when the first sendable outbox event cannot fit
  within the configured pull response byte budget. It does not advance cursors,
  skip, delete, or acknowledge the oversized event; normal multi-event
  pagination still returns preceding events before the blocking oversized item.
  This is sync compatibility hardening only; it does not change normal payload
  semantics, Android IME behavior, typed-text policy, release state, or
  publication semantics.
- The local desktop status API/Web UI now surfaces a content-safe
  `sync.blocked_pull` diagnostic for that oversized-event condition. The summary
  intentionally includes only protocol/code/size/sequence/count metadata and
  excludes clip content, payload fields, bearer tokens, hostnames, and device
  identifiers.
- CI now compiles the residual Android `androidTest` IME QA scaffold with
  AndroidX Test dependencies. The tests remain `@Ignore` and are not a substitute
  for Owner/manual device QA in Issue #36; the gate only prevents the scaffold
  source set from silently rotting.
- The unsigned release-candidate dry run now runs automatically on pushes to
  `main`, while PR runs remain path-filtered. Static release-alignment tests
  guard that this current-main evidence path does not gain release secrets,
  write permissions, release environments, or `gh release` side effects.
- Secret Guard now treats Hugging Face user access tokens with the distinctive
  `hf_` prefix and 34-character token body as a hard provider-key match on both
  Python desktop and Kotlin Android core. Tests build sample strings by
  concatenation to avoid committing contiguous secret-shaped fixtures. This is
  v1.7 capture-layer privacy hardening only; it performs no live token
  verification, adds no network call, changes no release state, and does not
  affect IME typed-text policy.
- Android sync now treats authenticated 401/403 responses from desktop
  push/pull endpoints as a permanent local pairing/auth failure. The worker
  clears the stored bearer token and returns `Result.success()` so WorkManager
  does not keep retrying a known-bad token; network failures, 413 oversized
  sync responses, rate limits, and 5xx responses stay on the existing retry path.
  This is v1.7 sync reliability hardening only and does not change sync payloads,
  IME behavior, typed-text policy, or release state.
- The Android IME source-boundary host-JVM test now also forbids direct
  persistence/capture/core imports and calls from IME services. IME frontends
  must stay thin and go through the Runtime facade instead of reaching Room,
  `ClipVaultApp`, `Capture`, `SecretGuard`, SharedPreferences, file IO, sync,
  network, or logging paths directly. This is v1.7 IME privacy regression
  coverage only; runtime behavior and Owner/manual QA requirements are unchanged.
- Panel IME and Full Keyboard candidate workers now re-check the captured
  `ImePrivacySession` token before calling `runtime.listCandidates(...)`.
  Existing UI-application guards still remain after the Runtime read; the new
  guard avoids unnecessary local candidate reads when Android switches from an
  ordinary editor to a sensitive editor while a worker is in flight.
- `test_android_privacy_manifest.py` now also guards the manifest shape for the
  two ClipVault IME services: both must remain exported system IME services
  protected by `android.permission.BIND_INPUT_METHOD`, expose only
  `android.view.InputMethod`, and keep `android.view.im` metadata pointed at the
  expected IME config XML. This is a static exposure regression gate only; it
  does not change IME runtime behavior or satisfy Owner/manual device QA.

## Recent completed note - 2026-07-03 / Web UI and sync API hardening

Branch `codex/webui-sync-hardening` was a small v1.x hardening patch. It did not change product scope, version metadata, release state, or Android IME behavior.

Implemented scope:
- Desktop `/api/pair` validates URL-safe bounded `device_id` before redeeming a one-time pairing code.
- Desktop sync push rejects non-array `events` and batches above the Android client batch size (100) before entering the sync engine.
- Desktop HTTP server sends first-party CSP, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and `Referrer-Policy: no-referrer`.
- Web UI renders API data with DOM APIs (`textContent`, `dataset`, `append`) instead of parsing clipboard/memory/device fields through HTML strings.

Verification recorded for that branch:
- `node --check desktop/clipvault/api/webui/app.js` passed.
- `cd desktop; .\.venv\Scripts\python.exe -m pytest -q tests\test_webui_security.py tests\test_api.py tests\test_sync.py` -> 53 passed.
- `cd desktop; .\.venv\Scripts\python.exe -m pytest -q` -> 216 passed.
- `cd android; .\gradlew :core:test :app:testDebugUnitTest --no-daemon` -> BUILD SUCCESSFUL.
- `cd android; .\gradlew :app:assembleDebug --no-daemon` -> BUILD SUCCESSFUL.
- GitHub Actions for PR #32 run 28634261928 passed desktop tests and Android
  unit/debug build.

Still not claimed:
- Device/manual IME QA.

## Recent completed note - 2026-07-03 / Windows clipboard exclusion

Branch `codex/v17-clipboard-exclusion` was a v1.7 capture-layer privacy patch.
It keeps Android IME behavior, version metadata, release state, sync protocol,
and runtime dependencies unchanged.

Implemented scope:
- Desktop watcher checks producer-set Windows clipboard privacy formats before
  reading `CF_UNICODETEXT`.
- `ExcludeClipboardContentFromMonitorProcessing` skips capture.
- `Clipboard Viewer Ignore` skips capture for compatibility with existing
  password-manager/clipboard-manager conventions.
- `CanIncludeInClipboardHistory=0` skips capture.
- `CanUploadToCloudClipboard=0` also skips capture because ClipVault public clips
  may later sync to another device and there is no per-clip no-sync metadata.
- Sequence numbers still advance on skipped items, so sensitive clipboard items
  are not repeatedly re-read.

Verification so far on this branch:
- `cd desktop; .\.venv\Scripts\python.exe -m pytest -q tests\test_watcher.py`
  -> 6 passed.
- `cd desktop; .\.venv\Scripts\python.exe -m pytest -q`
  -> 219 passed.
- `cd android; .\gradlew :core:test :app:testDebugUnitTest --no-daemon`
  -> BUILD SUCCESSFUL.
- `cd android; .\gradlew :app:assembleDebug --no-daemon`
  -> BUILD SUCCESSFUL.
- GitHub Actions for PR #34 run 28634878221 passed desktop tests and Android
  unit/debug build.

Not claimed yet:
- Real Windows clipboard manual QA with a source app that sets the registered
  exclusion formats.

## Current development note - 2026-07-03 / Pairing failure-window recovery

Branch `codex/pairing-success-reset` is a small desktop pairing/auth patch.
It keeps the pair-code format, token storage, sync protocol, Android IME behavior,
version metadata, release state, and runtime dependencies unchanged.

Planned/implemented scope:
- Keep `/api/pair` rate limiting for repeated bad one-time codes.
- Do not clear failures when minting a new one-time code.
- Clear the short failure window only after a valid one-time code is redeemed
  successfully, matching "consecutive failed attempts" semantics and reducing
  legitimate-user lockout after earlier typos.

Verification so far on this branch:
- `cd desktop; .\.venv\Scripts\python.exe -m pytest tests/test_sync.py -q`
  -> 28 passed.
- `cd desktop; .\.venv\Scripts\python.exe -m pytest -q`
  -> 220 passed.
- `cd android; .\gradlew :core:test :app:testDebugUnitTest --no-daemon`
  -> BUILD SUCCESSFUL.
- `cd android; .\gradlew :app:assembleDebug --no-daemon`
  -> BUILD SUCCESSFUL.
- GitHub Actions for PR #35 run 28640831174 passed desktop tests and Android
  unit/debug build.

Not claimed yet:
- Device/manual pairing QA.

## Current release gate note - 2026-07-03 / v1.6.0 metadata evidence

Issue #36 is the current `v1.6.0` release gate. The source tree version metadata
is aligned at desktop runtime/package `1.6.0`, Android `versionName=1.6.0`,
Android `versionCode=13`, and Windows installer `AppVersion=1.6.0`.

Guardrail updates:
- `desktop/tests/test_release_alignment.py` treats Android `versionCode >= 13`
  as the v1.6.0 floor.
- `docs/VERSION_SYNC.md` records the current 1.6.0 truth instead of stale
  v1.5.16 metadata.
- `docs/MANUAL_QA_V1_6_0.md` is the current manual/artifact checklist for
  Issue #36.

Still not claimed:
- Signed release artifacts.
- GitHub Release publication.
- Manual Android device QA.
- Manual Windows clipboard privacy QA.

Follow-on planning:
- `docs/STABILITY_PLAN_V1_6_V1_7.md` is the current stability execution plan for
  closing the v1.6 release gate and sequencing v1.7 without crossing the IME
  privacy/local-first boundaries.

## Product Constraints（全部 Active）

| Constraint | Status |
|---|---|
| Desktop is primary node | Active |
| Android is capture + keyboard entry; no background clipboard read | Active |
| Obsidian is primary knowledge base | Active |
| GitHub is backup (JSONL only), not realtime sync | Active |
| Keyboard is companion IME; never logs ordinary typing | Active |
| Secrets never enter Obsidian/GitHub/sync/FTS/memory | Active |
| Suggestions are deterministic in v1 | Active |
| Self-use comfort beats commercial completeness | Active |

## Current Version Status

源码树（main HEAD）的版本元数据全部对齐在 **1.6.0**：

- Desktop runtime version: 1.6.0
- Desktop package metadata: 1.6.0
- Android versionName: 1.6.0
- Android versionCode: 13
- Windows installer AppVersion: 1.6.0
- Panel IME service uses PanelCandidateTabs.filter with PANEL_CANDIDATE_POOL_LIMIT

**版本号 vs 发布状态**：

- v1.6–v1.8 加固支线（PRs #4–#15）已并入 main。Owner 裁定（2026-06-28）把 `__version__`
  从 1.5.16 **bump 到 1.6.0**（一次 minor，反映自 1.5.x 以来的累计加固；"v1.6/1.7/1.8" 原是路线图里程碑标签）。
  经 `scripts/bump_version.py 1.6.0` 一处改、四文件对齐（versionCode 12→13），`test_release_alignment.py` 守。
- **不发版**：本次只在仓内 bump，**未**切 GitHub Release。最新**已发布**二进制仍是 **v1.5.10**
  （2026-06-23，桌面 134 测试）。即 main HEAD（1.6.0，166 项 Linux 跑通 + 4 项 Windows-only）**领先于**最新发布二进制。
- 是否切 1.6.0 二进制 Release 仍待 Owner 显式决定（对外动作；签名 exe/APK 仅 CI 产出）。

## Hardening Support Line Snapshot（v1.6–v1.8，已并入 main）

> 这是 **支线**：可在 Linux/桌面验证的安全/同步/隐私加固，**从属于** keyboard 主线
> （[ROADMAP_V2_KEYBOARD.md](ROADMAP_V2_KEYBOARD.md) = 北极星：做完整中文输入法）。
> 研究记录与本支线路线见 [RESEARCH_AND_ROADMAP.md](RESEARCH_AND_ROADMAP.md)。

| PR | 主题 | 落点 |
|---|---|---|
| #4 | code-review diff 修复（首轮审查发现项） | desktop |
| #5 | sync-meta 测试加固 | desktop tests |
| #6 | 自动化 release-QA gate（test_release_alignment.py） | desktop tests |
| #7 | 版本单一源（scripts/bump_version.py + --check） | desktop tools |
| #8 | suggest 来源上限（origin source-cap）+ Android CandidateMixer 对齐 | desktop + android |
| #9 | /api/status + Web UI 暴露 sync/peer 状态 | desktop api/webui |
| #10 | 设备解绑（PeersRepo list/unpair + /api/peers GET/DELETE，仅 loopback） | desktop |
| #12 | 安全/隐私加固（配对限流、DNS-rebind Host 守卫、IME incognito）+ 研究路线 | desktop + android + docs |
| #13 | 拓宽 Secret Guard（高置信 provider key 规则，两端 + 向量） | desktop + android + contracts |
| #14 | clip_meta 同步改 per-field LWW（migration 0004） | desktop |
| #15 | DNS-rebind 守卫加第二层 Referer 检查 | desktop |

## Completed Slices Snapshot

| Slice | Result |
|---|---|
| S001 Core Pipeline | PASS: normalization, classification, secret guard, Obsidian golden tests, vectors generated |
| S002 Desktop Service | PASS: config, service, watcher, lock, real clipboard duplicate path |
| S003 GitHub Backup Worker | PASS: backup queue, local bare repo push, restore drill |
| S004 Local API + Web UI | PASS: stdlib HTTP API/UI, SQLite threading fix validated by live smoke |
| S007 Personal Memory | PASS: memory repo/importers/API/web UI/promote path |
| S010 Suggestion Engine | PASS: deterministic suggestions, pinned hard-priority behavior |
| S011 Context Action Engine | PASS: pure rule actions and promote-kind flow |
| S006 Desktop Sync Server | PASS: pair, push, pull, bearer auth, no-echo behavior |
| S012 Desktop Hardening | PASS: outbox pruning, peer ack tracking, docs/gates |
| S005 Android Capture + Kotlin Core | Source complete; Kotlin core vector tests historically passed; device validation remains owner-run |
| S008 Memory to Android Sync | Desktop side tested; Android mirror/source complete |
| S009 Keyboard Personal | IME source complete; Panel/Full Keyboard gates remain manual QA (device-only) |

## Current Contracts

| Contract | Location | Frozen? |
|---|---|---|
| Clip object | CONTRACTS §1 | Yes (v1) |
| Normalization NORM-1 | CONTRACTS §2 | Yes (v1) |
| Classifier CLS-1 | CONTRACTS §3 | Yes (v1) |
| Secret Guard SG-1 | CONTRACTS §4 | Yes (v1) |
| Sync | CONTRACTS §5 | Yes (v1) |
| Obsidian OBS-1 | CONTRACTS §6 | Yes (v1) |
| GitHub backup GHB-1 | CONTRACTS §7 | Yes (v1) |
| Test vectors VEC-1 | CONTRACTS §8 + contracts/vectors/ | Yes (v1) |
| SQLite DB-1 | CONTRACTS §9 | Yes (v1) |
| REST API-1 | CONTRACTS §10 | Yes (v1) |
| Suggest SUG-1 | CONTRACTS §11 | Yes (v1) |
| Config CFG-1 | CONTRACTS §12 | Yes (v1) |

## Decision Snapshot

| ID | Decision |
|---|---|
| D-001 | Secret entropy false positives handled with known-format exclusions rather than raising entropy threshold |
| D-002 | Use venv/pip validation path instead of requiring uv |
| D-003 | Keep self-contained ULID/vector tooling rather than adding runtime dependency |
| D-004 | Use ctypes polling watcher rather than pywin32 message window |
| D-005 | Read config with UTF-8 BOM tolerance for Windows tools |
| D-006 | Use stdlib HTTP server for local API/UI |
| D-007 | Use HTTP push/pull sync rather than WebSocket server |
| D-008 | Treat pinned suggestions as hard priority layer |

## Verification Snapshot

当前 SG-1.3 分支桌面全套：**204 passed**（Windows，
`desktop/.venv/Scripts/python -m pytest -q`，2026-07-02）。新增覆盖：Memory `text`/`label` 写入拒绝、
导入跳过、API 422、历史行候选隐藏/分页补位、outbox 出口复扫、远端 secret memory 安全 no-op、
历史 pull 过滤且游标推进；Web UI 被拒绝时保留输入并显示通用错误。
Android 增加纯 JVM `MemoryPrivacyTest`；本机无 Android SDK，单测/构建由 GitHub Actions 验证。
CI 首轮暴露 `:core` Java 21 与 `:app` Java 17 的运行时不兼容；本分支把共享 core toolchain 对齐到 17，
Actions run **28571554399** 随后验证 Desktop tests 与 Android tests/debug build 均通过（2026-07-02）。

历史 main 快照：

main HEAD desktop suite: **184 passed** on Linux/CI-portable runners
(`python -m pytest -q --ignore=tests/test_watcher.py --ignore=tests/test_instance_lock.py`,
verified 2026-06-28；含 #19 SG-1.2、#21 SUG-1.3、CJK-FTS 7 例、GHB-1.1 备份-删除 4 例、memory 导入不复活 1 例）。
另有 **4** 项 Windows-only 用例（`test_watcher.py` 3 + `test_instance_lock.py` 1，依赖 `ctypes.WinDLL`，仅
windows-latest CI 可跑）→ 桌面总计 **188** 项。schema 版本 = **5**（migration 0005：clips_fts 改 trigram，修中文搜索）。
最近修复：GHB-1.1（clip deleted 重新入队备份，restore 不复活已删 clip）；memory 导入重跑不复活用户已删词条。
（历史参考：v1.0 时为 128，v1.5.10 发布时 134。）Android Kotlin core 向量历史 100/100。
Issue #3 已于 2026-06-26 关闭（A+B 签收，CI 绿；Actions 28230052875 / 28231364251 / 28238873009）。

## v1.5 Release Gate — ✅ CLOSED

Issue #3 closed 2026-06-26（state_reason: completed，closed_by: selinyi123，A+B 签收）。
关闭判据处置（见 Issue #3 正文）：

- desktop tests pass — ✅ CI 绿；
- Android unit tests pass — ✅ CI 绿；
- Android debug build passes — ✅ CI 绿；
- Full Keyboard manual checks — 决策逻辑已单测；UI render/tap 顺延到 instrumented 任务（B，见 docs/INSTRUMENTED_QA_BACKLOG.md）；
- Panel IME manual checks — 决策逻辑已单测；UI switch/tap 顺延到 instrumented 任务（B）；
- visible version metadata aligned — ✅ 全对齐 1.5.16；
- no v1.5 blocker open — ✅。

## v1.6 Entry Gate — ✅ 已满足

Issue #3 已关闭 → v1.6 may now proceed。门后实际交付的，是上方 **Hardening Support Line Snapshot**
（PRs #4–#15，安全/同步/隐私加固）。这条支线**从属于** keyboard 主线，主线见
[ROADMAP_V2_KEYBOARD.md](ROADMAP_V2_KEYBOARD.md)（北极星 = 完整中文输入法），其 v2.1 起接 librime/fcitx5
底座，需 Android/原生设备或 CI 验证（本地无法编译 Kotlin/native）。

> 注：上述 v1.6 候选轨（source caps/tab weighting、source toggles、query-aware filtering、
> release-state display、version single-source）多数已在 #7–#9 落地；其余并入 RESEARCH_AND_ROADMAP 支线路线。

Typed text learning, behavioral profiling, cloud keyboard intelligence, and analytics remain out of scope unless a separate privacy design is approved first.
