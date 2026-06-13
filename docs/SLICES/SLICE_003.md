# Slice 003 — GitHub Backup Worker

> Architect: Claude Fable 5 | 2026-06-13 | 状态：规格冻结
> 对应版本：v0.1c。完成后公开 clip 会被批量 JSONL 备份到本地 git 工作副本并 push。

## 1. 目标

实现 GHB-1：把 backup_queue 中的公开 clip 序列化为按日 JSONL，批量 commit + push
到 GitHub 私有仓库；闸门 C 复扫密钥；push 失败退避重试；提供恢复工具。

## 2. 允许触碰的文件

```text
desktop/clipvault/backup/{__init__,github_backup,jsonl_store,git_repo}.py
desktop/clipvault/store/backup_queue_repo.py   # claim/mark_done/mark_dropped/record_attempt/state_of
desktop/clipvault/store/clips_repo.py          # set_backed_up_at / all_clips
desktop/clipvault/main.py                       # 接线 backup worker 线程
tools/restore.py                                # JSONL -> SQLite 重建
desktop/tests/**
docs/{HANDOFF,CONTRACTS}.md
```

## 3. 实现要求

1. **jsonl_store.py**：serialize/deserialize（单行 JSON，§1 字段稳定顺序）+ daily_relpath +
   原子追加 + iter_jsonl。
2. **git_repo.py**：subprocess 包装 init(-b main)/is_clean/head_commit/current_branch/
   add_commit/push（显式推当前分支）；带超时；**不提供 pull/force/rebase/amend**（ADR-0003, G3）。
3. **github_backup.py**：`BackupWorker.run_once()`：pending → 闸门 C 复扫 → 通过的写 JSONL +
   回写 backed_up_at + mark_done → add_commit → push；push 失败本地已 commit、退避
   1→2→4…→30min；enabled=false 不启动。
4. **backup_queue_repo.py 扩展**：claim_pending/mark_done/mark_dropped/record_attempt/state_of。
5. **restore.py**：读全部 JSONL → 按 id 去重 → 重建新 SQLite（不覆盖现有库）。
6. **main.py**：enabled 时起 backup-worker 守护线程；异常不杀线程。

## 4. 验收门禁（本地裸仓库作 push 目标，绝不碰真实 GitHub）

- C1. 公开 clip → run_once → JSONL 出现、字段符合 §1、backed_up_at 回写、队列 done。
- C2. 闸门 C：密钥 clip 被强塞队列 → 丢弃、不写 JSONL、ERROR。
- C3. add_commit 产生提交、push 到裸仓库成功、裸仓库可见提交。
- C4. push 失败不抛顶层、数据已本地 commit、退避递增、remote 修复后成功。
- C5. 同一 clip 不重复备份。
- C6. 恢复演练：备份→restore 重建→clip 数与 hash 集合与原库公开部分一致。
- C7. git_repo 不提供 pull/force/rebase/amend（静态检查）。
- C8. 日志不含正文。

## 5. 验证命令

```text
desktop> .venv\Scripts\python -m pytest -q
```
