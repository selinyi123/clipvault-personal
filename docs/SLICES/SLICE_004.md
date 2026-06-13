# Slice 004 — Local API + Web UI

> Architect: Claude Fable 5 | 2026-06-13 | 状态：规格冻结
> 对应版本：v0.1d。完成后桌面端有本地 Web UI：看历史、搜索、固定/收藏/删除、管理隔离区、看状态。

## 1. 决策修订 D-006

API-1 / ADR-0005 原定 FastAPI。**改用 Python 标准库 `http.server`**：
- 单用户 localhost 工具，不需要校验框架/异步/OpenAPI；
- 保持桌面端零运行时依赖（与 D-004 一致），规避本机 pip 代理不稳定；
- API-1 的端点与语义不变，仅实现框架改变。
鉴权：S004 仅绑定 127.0.0.1、本机免鉴权；Bearer token 与配对随 S006 落地。

## 2. 允许触碰的文件

```text
desktop/clipvault/api/{__init__,server,handlers}.py
desktop/clipvault/api/webui/{index.html,app.js,style.css}
desktop/clipvault/store/clips_repo.py    # list/search 过滤、set_flags、release、FTS 删除维护
desktop/clipvault/service.py             # release 后重走 Obsidian/backup
desktop/clipvault/main.py                # 启动 API 线程
desktop/tests/**
docs/{HANDOFF,CONTRACTS}.md
```

## 3. 实现要求（API-1 子集，本片范围）

REST（base `/api`，绑定 127.0.0.1）：

- `GET /api/health` → `{status, version, db_ok}`
- `GET /api/clips?q=&type=&secret=&limit=&before_id=` → 列表/FTS 搜索；密钥项仅在 `secret=1`
  时返回且 content 脱敏（前 4 + ••••），其余字段正常
- `POST /api/clips` `{content, source_app?}` → 走完整 ingest 管线（含 Obsidian 写入）
- `PATCH /api/clips/{id}` `{pinned?|favorite?|deleted?}` → 更新标志；deleted=1 时从 FTS 移除
  （clip_meta 事件入 outbox 推迟到 S006，本片只改 clips 表）
- `POST /api/clips/{id}/release` → 释放隔离（is_secret=0, released=1, released_at），重走
  Obsidian + backup 管线
- `GET /api/status` → 队列深度（backup pending）、最后备份时间、clip 总数、隔离数

Web UI（`/`，单页）：历史列表（含类型标签、时间）、搜索框（FTS）、pin/收藏/删除按钮、
隔离区分页（脱敏显示 + 释放按钮）、状态条。原生 JS，无前端框架。

## 4. 验收门禁

- D1. `/api/health` 返回 db_ok=true。
- D2. `/api/clips` 列表按 last_seen 倒序；`q=` 走 FTS 命中；`type=` 过滤生效。
- D3. 密钥 clip：默认列表不返回；`secret=1` 返回且 content 脱敏、不泄露长度以外信息。
- D4. `POST /api/clips` 文本 → 落库 + 若公开则 Obsidian 文件出现 + backup 入队。
- D5. `PATCH` pin/favorite 生效；`deleted=1` 后该 clip 不再出现在 FTS 搜索结果。
- D6. `release` 一个隔离 clip → is_secret=0、released=1、重走管线后进 FTS + backup_queue + Obsidian。
- D7. `/api/status` 数字与库内一致。
- D8. 非 127.0.0.1 来源被拒（构造 Host/在 handler 层校验 client_address）。
- D9. 日志不含 clip 正文。
- D10. 所有端点有测试（用 http.client 或 handler 直测）。

## 5. 验证命令

```text
desktop> .venv\Scripts\python -m pytest -q
desktop> .venv\Scripts\python -m clipvault.main --config config.toml   # 浏览器开 http://127.0.0.1:8787/
```
