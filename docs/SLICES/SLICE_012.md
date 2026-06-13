# Slice 012 — 桌面加固 + 文档 (v1.0 desktop)

> Architect: Claude Fable 5 | 2026-06-13 | 状态：规格冻结
> 对应版本：v1.0（桌面侧）。让桌面端成为可长期运行、可恢复、有文档的自用 v1。

## 1. 目标

收尾桌面端：sync outbox 有界增长（裁剪已被对端确认的事件）、恢复演练复核、
全门禁复验、安装/配置/配对/自启文档。

## 2. 允许触碰的文件

```text
desktop/clipvault/store/outbox_repo.py   # prune_acked
desktop/clipvault/main.py                # 周期裁剪（并入 backup/sweep 循环）
desktop/tests/**
docs/INSTALL.md（新增）
README.md（指向 INSTALL）
docs/{HANDOFF,GATES}.md
```

## 3. 实现要求

1. **outbox_repo.prune_acked(min_acked)**：删除 seq ≤ min_acked 的事件（已被所有对端确认）。
   `min_acked` = 所有 peer 的 my_acked_seq 最小值；无 peer 时不裁剪（仍可能配对）。
2. **main.py**：在 sweep/backup 守护循环里周期调用裁剪（用 peers 的 min my_acked_seq）。
   异常不杀线程。
3. **docs/INSTALL.md**：环境要求（Python 3.11+，git）、获取/运行、config.toml 填写、
   GitHub 备份仓库准备（私有 + git init + remote）、配对步骤、开机自启（计划任务）、
   恢复演练（tools/restore.py）、隐私须知（建议全盘加密 / Tailscale）。
4. **GATES.md**：标注 v1.0 桌面门禁达成状态；7 天稳定性标记为运行期观察项。

## 4. 验收门禁

- I1. prune_acked 删除已确认事件、保留未确认事件；无 peer 时不裁剪。
- I2. 裁剪后 pull（since_seq 落在被裁剪区间）不报错且返回剩余事件（自用可接受裁剪窗口语义）。
- I3. 恢复演练复核：restore.py 从 JSONL 重建库，hash 集合与原库公开部分一致（沿用 C6）。
- I4. 全量 pytest 绿（全局门禁 G1–G8 + 各版本门禁回归）。
- I5. INSTALL.md 覆盖：依赖、运行、配置、备份仓库、配对、自启、恢复、隐私。

## 5. 验证命令

```text
desktop> .venv\Scripts\python -m pytest -q
```
