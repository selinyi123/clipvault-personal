# Slice 001 — Core Pipeline（纯逻辑 + 存储 + Obsidian Writer）

> Architect: Claude Fable 5 | 2026-06-12 | 状态：规格冻结，可开工
> 对应版本：v0.1a。本片完成后，桌面端核心管线在测试中端到端可证，但还没有剪切板监听、网络与 UI（那是 S002–S004）。

## 1. 目标

建立桌面端最小可验证核心：一段文本进入 ingest 管线后，能被规范化、去重、密钥判定、分类、落库（FTS 正确排除密钥）、生成 Obsidian Markdown、进入备份队列——全程有测试证明。

## 2. 允许触碰的文件（白名单）

```text
desktop/pyproject.toml
desktop/clipvault/core/{__init__,models,normalize,classifier,secret_guard}.py
desktop/clipvault/store/{__init__,db}.py
desktop/clipvault/store/migrations/0001_init.sql
desktop/clipvault/store/{clips_repo,backup_queue_repo}.py
desktop/clipvault/pipeline/ingest.py
desktop/clipvault/obsidian/writer.py
desktop/tests/**
contracts/vectors/{normalization,classifier,secret_guard}.json
docs/HANDOFF.md（按规则更新）
```

白名单之外的任何文件出现在 diff 中 = 范围违规。

## 3. 明确不做（Out of Scope）

- 剪切板监听（S002）、config 加载（S002）、git/GitHub 实际操作（S003：本片只入队，不消费队列）
- FastAPI / Web UI / WebSocket / Android / 任何网络代码
- memory_items、suggest（建表即可，不写逻辑）
- 打包、安装脚本、CI 配置

## 4. 实现要求

1. **合同对应**：NORM-1（normalize.py）、CLS-1（classifier.py）、SG-1（secret_guard.py）、DB-1（0001_init.sql 全量建表，含 memory/sync 表）、OBS-1（writer.py）。逐条实现，不增不减。
2. **core/ 零 IO**：core 包内禁止 import sqlite3/requests/httpx/pathlib 写操作；writer.py 把"生成内容+路径"（纯函数）与"写文件"分离成两个函数。
3. **测试向量**：创建三个 vectors JSON（数量要求见 CONTRACTS §8），并写加载-断言测试。向量内密钥样本必须伪造（如 `AKIAIOSFODNN7EXAMPLE`）。
4. **ingest.py** 编排顺序：normalize → 拒收判定 → dedup → secret guard（闸门 A）→ classify → 落库（含 FTS 维护）→（非密钥）backup_queue 入队 + 返回"待写 Obsidian"指示。
5. ULID：可用 `python-ulid` 库。时间注入采用可传入 clock 的方式（测试可控）。

## 5. 验收门禁（结果以 pytest 原始输出为准）

- A1. 迁移从零执行成功，schema_meta=1。
- A2. ingest 一条普通文本 → clips 行字段完整（ULID、UTC、hash 与向量一致）。
- A3. 重复 ingest 同内容 → 不新建行，times_seen=2；对 deleted=1 的 clip 重复 ingest 不复活。
- A4. classifier 对 vectors/classifier.json 100% 通过。
- A5. secret_guard 对 vectors/secret_guard.json 100% 通过。
- A6. 密钥 clip：clips_fts 查不到、backup_queue 无记录、writer 拒绝生成。
- A7. 非密钥 clip 的 Markdown 与 golden file 逐字节一致（≥3 个类型各一个 golden）；重复调用 writer 不产生第二个文件；同名冲突自动 `-1`。
- A8. 围栏冲突：正文含 ``` 的 code clip，外层围栏正确加长。
- A9. CRLF/NFC/尾空白/空内容/超长内容 各有测试且行为符合 NORM-1。
- A10. 全部测试通过且 core/ 无 IO import（用一个静态检查测试断言）。

## 6. 验证命令

```bash
cd desktop && uv sync && uv run pytest -v
```

## 7. 交付物

- 通过 A1–A10 的代码与测试
- 更新 docs/HANDOFF.md：Completed Slices 行、Raw Verification Results（粘贴 pytest 汇总行）、Open Disagreements（如有）

---

## Builder Paste Block

```text
/goal: Execute Slice 001 of ClipVault Personal.

Read first, in order:
  docs/HANDOFF.md
  docs/PRODUCT_SPEC.md
  docs/ARCHITECTURE.md
  docs/CONTRACTS.md   (implement NORM-1, CLS-1, SG-1, DB-1, OBS-1 exactly)
  docs/GATES.md       (global gates G1-G8 apply)
  docs/SLICES/SLICE_001.md  (this slice: file whitelist, out-of-scope, gates A1-A10)
  docs/PROMPTS/BUILDER_CODEX_GOAL.md  (your operating rules)

Then follow BUILDER_CODEX_GOAL.md Phase 0: post your plan, disagreements,
and ambiguities BEFORE writing code. Silent compliance is failure.

Hard constraints for this slice:
  - Touch only whitelisted files.
  - core/ has zero IO imports.
  - No network code, no clipboard listener, no UI.
  - Secret clips: never in FTS, never in backup_queue, writer must refuse.
  - All three contracts/vectors/*.json created with required coverage;
    fake secrets only (e.g. AKIAIOSFODNN7EXAMPLE).
  - Verification: cd desktop && uv sync && uv run pytest -v
  - Do not self-grade. Report raw pytest output in HANDOFF.md.
```
