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
| Current slice | **S002 — Desktop Service（待 Architect 写规格）** |
| Last updated | 2026-06-13 (S001 完成并验收；Builder 角色由 Claude Fable 5 兼任，Owner 批准) |

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
| S001 Core Pipeline | （见 git log: feat: S001） | desktop/clipvault/{core,store,pipeline,obsidian}/** + tests + contracts/vectors/*.json + tools/gen_vectors.py | 32 passed / 0 failed (pytest) | **PASS**（A1–A10 全过） |

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
| D-001 | Builder | SG-ENTROPY 熵规则会误报 git hash / UUID / base64 图片头（恰为合同要求的负例，合同自相矛盾） | a) 提高熵阈值 b) 增加已知格式排除 | **RULED: MODIFY** — 采用 b，已写入 CONTRACTS §4.2 SG-1.1；阈值不动以保灵敏度 |
| D-002 | Builder | 环境无 uv | a) 安装 uv b) 改用 venv+pip | **RULED: ACCEPT b** — 验证命令改为 `.venv\Scripts\python -m pytest`；不为自用工具引入额外安装步骤 |
| D-003 | Builder | S001 白名单外新增 core/ulid.py 与 tools/gen_vectors.py | a) 引第三方 ULID 库 b) 自实现 26 行 ULID + 向量生成器入库 | **RULED: ACCEPT b** — 零运行时依赖；生成器含对实现的自校验，留库便于复现 |

## Raw Verification Results

| Date | Slice | Command/Test | Result | Notes |
|---|---|---|---|---|
| 2026-06-13 | S001 | `desktop> .venv\Scripts\python -m pytest -v` | **32 passed, 0 failed** (0.08s, Python 3.11.9, pytest 9.0.3) | 含 22 个 NORM、40 个 CLS、38 个 SG 向量用例；3 个 OBS-1 golden 逐字节比对；core 纯度静态检查 |
| 2026-06-13 | S001 | `python tools/gen_vectors.py`（含实现自校验） | 100 cases written, 0 mismatches | 向量为两端唯一仲裁，Kotlin 端（S005）须通过同一文件 |

## Architect Decisions Log

| Date | Decision |
|---|---|
| 2026-06-12 | 初始架构冻结：ADR-0001…0007；CONTRACTS v1；GATES 全版本；ROADMAP S001–S012 |
| 2026-06-12 | 偏离原 ChatGPT 方案的修正：①GitHub 备份去掉 Markdown 镜像只存 JSONL；②密钥排除出 FTS 索引；③同步明确为事件日志复制；④Android 采集以 Share Target 为主路径（平台限制）；⑤IME 推荐只查本地缓存；⑥新增配对鉴权；⑦原 Slice001 拆为 S001–S004 |

## Next Slice Candidate

S002 — Desktop Service：win32 剪切板监听 + 轮询降级、config 加载（CFG-1）、ingest 编排接线（含 Obsidian 写入消费 needs_obsidian）、单实例锁、日志（无正文）、main 入口与开机自启说明。
