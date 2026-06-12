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
| Backup | GitHub private repo (JSONL only) |
| Realtime sync | LAN / Tailscale WebSocket (SYNC-1) |
| Source of truth | SQLite local store (DB-1) |
| Current slice | **S001 — Core Pipeline（规格见 docs/SLICES/SLICE_001.md，可开工）** |
| Last updated | 2026-06-12 (Architect: Claude Fable 5, 初始架构定稿) |

## Product Constraints（全部 Active）

| Constraint | Status |
|---|---|
| Desktop is primary node | Active (ADR-0001) |
| Android is capture + keyboard entry; no background clipboard read | Active |
| Obsidian is primary knowledge base | Active |
| GitHub is backup (JSONL only), not realtime sync | Active (ADR-0003) |
| Keyboard is companion IME; never logs ordinary typing | Active (ADR-0004) |
| Secrets never enter Obsidian/GitHub/sync/FTS/memory | Active (ADR-0006) |
| Suggestions are deterministic in v1 | Active (ADR-0007) |
| Self-use comfort beats commercial completeness | Active |

## Completed Slices

| Slice | Commit | Files changed | Tests | Result |
|---|---|---|---|---|
| （尚无） | — | — | — | — |

## Current Contracts

| Contract | Location | Frozen? |
|---|---|---|
| Clip object | CONTRACTS §1 | **Yes (v1)** |
| Normalization NORM-1 | CONTRACTS §2 | **Yes (v1)** |
| Classifier CLS-1 | CONTRACTS §3 | **Yes (v1)** |
| Secret Guard SG-1 | CONTRACTS §4 | **Yes (v1)** |
| Sync SYNC-1 / PAIR-1 | CONTRACTS §5 | Yes (v1)，S006 开工前允许 Builder 提修订 |
| Obsidian OBS-1 | CONTRACTS §6 | **Yes (v1)** |
| GitHub backup GHB-1 | CONTRACTS §7 | **Yes (v1)** |
| Test vectors VEC-1 | CONTRACTS §8 + contracts/vectors/ | 框架冻结；向量文件由 S001 创建 |
| SQLite DB-1 | CONTRACTS §9 | **Yes (v1)** |
| REST API-1 | CONTRACTS §10 | Yes (v1)，S004 开工前允许提修订 |
| Suggest SUG-1 | CONTRACTS §11 | Yes (v1)，S010 开工前允许提修订 |
| Config CFG-1 | CONTRACTS §12 | **Yes (v1)** |

## Open Disagreements

| ID | Raised by | Topic | Options | Status |
|---|---|---|---|---|
| （尚无） | — | — | — | — |

## Raw Verification Results

| Date | Slice | Command/Test | Result | Notes |
|---|---|---|---|---|
| （尚无） | — | — | — | — |

## Architect Decisions Log

| Date | Decision |
|---|---|
| 2026-06-12 | 初始架构冻结：ADR-0001…0007；CONTRACTS v1；GATES 全版本；ROADMAP S001–S012 |
| 2026-06-12 | 偏离原 ChatGPT 方案的修正：①GitHub 备份去掉 Markdown 镜像只存 JSONL；②密钥排除出 FTS 索引；③同步明确为事件日志复制；④Android 采集以 Share Target 为主路径（平台限制）；⑤IME 推荐只查本地缓存；⑥新增配对鉴权；⑦原 Slice001 拆为 S001–S004 |

## Next Slice Candidate

S001（规格已就绪：docs/SLICES/SLICE_001.md）。Builder 从该文件的 Paste Block 开始。
