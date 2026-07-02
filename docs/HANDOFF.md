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
| Current slice | SG-1.3 Memory 出入口加固：阻止 secret-shaped Personal Memory 进入持久化、同步与 Android IME 候选；不改 schema/版本号。Issue #3（v1.5 gate）历史上已于 2026-06-26 按 A+B 签收关闭。 |
| Last updated | 2026-07-02 |

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
