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
| Current slice | v1.5.16 release metadata aligned；Panel IME 已接入 PanelCandidateTabs；剩余 gate 为 CI 可见性与 MANUAL_QA_V1_5_16 手动验证。 |
| Last updated | 2026-06-25 |

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

## Current v1.5.16 Status

- Desktop runtime version: 1.5.16
- Desktop package metadata: 1.5.16
- Android versionName: 1.5.16
- Android versionCode: 12
- Windows installer AppVersion: 1.5.16
- Panel IME service uses PanelCandidateTabs.filter with PANEL_CANDIDATE_POOL_LIMIT
- Remaining blockers: CI result visibility and manual QA evidence

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
| S009 Keyboard Personal | IME source complete; current v1.5.16 Panel/Full Keyboard gates remain manual QA |

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

Historical desktop verification reached 128 passing pytest cases across core pipeline, service, backup, API/UI, memory, suggestion, context-action, sync, and hardening slices. Android Kotlin core vectors historically passed 100/100. Current v1.5.16 still requires fresh CI visibility or local command evidence plus manual QA before Issue #3 can close.

## v1.5 Release Gate

Issue #3 may close only when:

- desktop tests pass;
- Android unit tests pass;
- Android debug build passes;
- Full Keyboard manual checks pass;
- Panel IME manual checks pass;
- visible version metadata is aligned;
- no v1.5 blocker remains open.

## v1.6 Entry Gate

Do not start v1.6 until Issue #3 is closed.

Candidate v1.6 tracks after closure:

- candidate source caps and tab weighting;
- source toggles in keyboard UI;
- query-aware transient candidate filtering;
- improved release-state display;
- safer version metadata single-source strategy.

Typed text learning, behavioral profiling, cloud keyboard intelligence, and analytics remain out of scope unless a separate privacy design is approved first.
