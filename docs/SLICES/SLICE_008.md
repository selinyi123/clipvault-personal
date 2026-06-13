# Slice 008 — Memory 同步到 Android

> Architect: Claude Fable 5 | 2026-06-13 | 状态：规格冻结
> 对应版本：v0.4b。Personal Memory 经事件日志同步到手机，供 IME 面板展示。

## 1. 目标

桌面 memory 变更（新增/更新/删除）emit memory_upsert / memory_delete 到 outbox；
Android pull 后写入本地 memory 缓存，IME 面板展示 词库/Prompt/命令。

## 2. 允许触碰的文件

```text
desktop/clipvault/sync/engine.py         # emit_memory_upsert/delete + apply 两种 memory 事件
desktop/clipvault/api/handlers.py        # create_memory/promote/delete_memory emit
desktop/clipvault/memory/importers.py    # apply 可选 emit
desktop/tests/**
android/app/src/main/kotlin/com/clipvault/app/data/Db.kt   # memory 表 + DAO
android/app/.../sync/Sync.kt             # 应用 memory_* 事件
android/app/.../ime/ClipVaultKeyboardService.kt            # 词库面板
docs/{HANDOFF,CONTRACTS}.md
```

## 3. 实现要求

1. **engine**：
   - `emit_memory_upsert(conn, item, when)`：payload = MemoryItem dict（kind/text/label/pinned/use_count/source）。
   - `emit_memory_delete(conn, kind, text, ts, when)`：payload = {kind, text, ts}。
   - `apply_push` 增加 kind `memory_upsert`（按 (kind,text) 幂等 upsert，use_count 取 max）
     与 `memory_delete`（按 (kind,text) 软删）。
2. **handlers**：create_memory / promote_clip 成功后 emit_memory_upsert；delete_memory emit_memory_delete。
3. **importers.apply**：对新增项 emit_memory_upsert（传入 conn）。
4. **Android**：Room `memory` 表 + DAO；SyncApply 处理 memory_upsert/delete；IME 面板加
   「词库 / Prompt / 命令」分区，点按一键 commitText。

## 4. 验收门禁（桌面侧可测；Android 侧源码交付）

- K1. 新增 memory → outbox 出现 memory_upsert，payload 含 kind/text。
- K2. 删除 memory → outbox 出现 memory_delete。
- K3. promote clip → 该 memory 的 memory_upsert 入 outbox。
- K4. apply memory_upsert（模拟对端）→ 本地 memory upsert（幂等、use_count 取 max）。
- K5. apply memory_delete → 本地软删。
- K6. pull 返回 memory_* 事件（since 游标）。
- K7. Android：memory 表/DAO/apply/IME 面板源码完整（审阅级）。
- K8. 日志不含正文。

## 5. 验证命令

```text
desktop> .venv\Scripts\python -m pytest -q
```
