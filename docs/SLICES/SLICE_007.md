# Slice 007 — Personal Memory (Desktop)

> Architect: Claude Fable 5 | 2026-06-13 | 状态：规格冻结
> 对应版本：v0.4a。个人词库：词/短语/Prompt/命令/关键信息/路径的 CRUD、导入、提升。

## 1. 目标

实现 memory_items 的存取与管理（DB-1 已建表）：手动 CRUD、从高频 clip 一键提升、
从 Obsidian 标题导入、从名称列表（GitHub 仓库名等）导入；经 API-1 与 Web UI 暴露。
为 S010 Suggestion Engine 提供候选源。

## 2. 允许触碰的文件

```text
desktop/clipvault/store/memory_repo.py
desktop/clipvault/memory/{__init__,importers}.py
desktop/clipvault/api/handlers.py        # /api/memory CRUD + /api/clips/{id}/promote
desktop/clipvault/api/server.py          # 路由
desktop/clipvault/api/webui/{index.html,app.js,style.css}  # 词库标签页
desktop/tests/**
docs/{HANDOFF,CONTRACTS}.md
```

## 3. 实现要求

1. **memory_repo.py**：
   - `upsert(kind, text, label?, source?, pinned?)`：按 UNIQUE(kind,text) 幂等；已存在则
     更新 label/pinned，use_count 取 max（不回退）。
   - `list(kind?, query?, limit)`：deleted=0，按 pinned DESC, use_count DESC, last_used_at DESC。
   - `get/by_kind_text`、`soft_delete(id)`、`bump_use(id, when)`（use_count+1, last_used_at）。
   - kind ∈ term|phrase|prompt|command|key_info|path（非法 kind 报错）。
2. **importers.py**（纯函数，可注入数据，零网络）：
   - `from_obsidian_titles(vault_path) -> list[(kind,text)]`：扫描 vault 下 .md 文件名（去扩展名、
     去 OBS-1 的时间/ id 前后缀启发），产出 kind=path 的候选；空白/重复跳过。
   - `from_names(names) -> list[(kind,text)]`：把名称列表（如 GitHub 仓库名）转 kind=term。
   - `apply(repo, items, source)`：批量 upsert，返回新增数。重复执行幂等。
3. **promote**：`promote_clip(clip_id)`：取 clip → 依 content_type 映射 kind
   （prompt→prompt, command→command, 其余→phrase）→ upsert(text=clip.content[:200], source='derived')。
   密钥 clip 拒绝提升（返回 None）。
4. **API-1 端点**：
   - `GET /api/memory?kind=&q=` → 列表
   - `POST /api/memory` `{kind,text,label?,pinned?}` → upsert
   - `DELETE /api/memory/{id}` → soft delete
   - `POST /api/clips/{id}/promote` → promote
5. **Web UI**：新增"词库"标签页：列表（按 kind 分组或筛选）、添加、固定、删除；
   历史卡片加"加入词库"按钮。

## 4. 验收门禁

- E1. upsert 幂等：同 (kind,text) 两次 → 一行；use_count 不回退。
- E2. 非法 kind 被拒。
- E3. list 排序 pinned > use_count > last_used；kind/q 过滤生效。
- E4. bump_use 递增并更新 last_used_at。
- E5. soft_delete 后不在 list 中。
- E6. Obsidian 标题导入：给定若干 .md 文件 → 产出去重的 path 候选；再次导入不新增（幂等）。
- E7. promote：command clip → kind=command 的 memory；密钥 clip 拒绝。
- E8. API：CRUD + promote 全部返回正确码与体。
- E9. 日志不含正文。

## 5. 验证命令

```text
desktop> .venv\Scripts\python -m pytest -q
```
