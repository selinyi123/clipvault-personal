# Slice 006 — 双端同步服务端 (Desktop, HTTP event-log sync)

> Architect: Claude Fable 5 | 2026-06-13 | 状态：规格冻结
> 对应版本：v0.3（桌面侧）。事件日志复制的服务端 + 配对鉴权。Android 客户端在 S005/S008 消费。

## 1. 决策 D-007（裁决：HTTP push/pull）

SYNC-1 原定 WebSocket；stdlib 无 WS 服务端。**改用 HTTP**：复用 http.server、零依赖；
自用双端短轮询秒级延迟可接受。**事件日志语义不变**（seq 游标续传、(origin,seq)+hash 双幂等、
clip_meta 字段级 LWW、闸门 B 密钥不进 outbox）。记为 SYNC-2，覆盖 SYNC-1 的传输层。

## 2. 允许触碰的文件

```text
desktop/clipvault/store/{outbox_repo,peers_repo}.py
desktop/clipvault/sync/{__init__,pairing,engine}.py
desktop/clipvault/pipeline/ingest.py     # 新建非密钥 clip 时 emit clip_new 到 outbox
desktop/clipvault/api/handlers.py        # /api/pair, /api/sync/push, /api/sync/pull, bearer 鉴权
desktop/clipvault/api/server.py          # 路由 + Authorization 解析
desktop/clipvault/service.py             # patch 标志变更时 emit clip_meta（经 api）
desktop/tests/**
docs/{HANDOFF,CONTRACTS}.md
```

## 3. 协议（SYNC-2，基于 CONTRACTS §5 语义）

鉴权：除本机 127.0.0.1（Web UI 豁免）外，`/api/sync/*` 要求 `Authorization: Bearer <token>`。

- `POST /api/pair` `{code, device_id, device_name, outbox_base_seq?}` →
  `{token, server_device, outbox_base_seq?}`（PAIR-1：一次性码 TTL 5min，token 32B
  base64url；桌面只存 sha256）。新版 Android 从同一 SQLite 快照发送最早保留的
  outbox seq，桌面原子设置 `peer_cursor=outbox_base_seq-1` 并精确回显；缺少字段时
  保持旧客户端 cursor 行为。
- `POST /api/sync/push` `{device_id, events:[Event...]}` → `{acked_upto}`
  - 仅应用 seq > peer_cursor[device] 的事件，按 seq 升序；clip_new 闸门 A/B 复扫（密钥→本地隔离，
    不再外传）、按 content_hash 幂等插入；clip_meta 按 content_hash 找 clip、字段级 LWW（ts 比较，
    平 ts 时 delete 赢）。应用后推进 peer_cursor，返回最高连续应用 seq。
- `GET /api/sync/pull?device_id=&since_seq=` → `{events:[...], next_seq, has_more}`
  - 返回桌面 outbox 中 seq > since_seq 的事件（≤100/批），更新 my_acked_seq=since_seq。

Event：`{origin_device, seq, kind, ts, data}`，kind ∈ clip_new|clip_meta（memory_* 属 S008）。

## 4. 实现要求

1. **outbox_repo**：append(kind, payload_dict)→seq；list_since(seq, limit)；max_seq()。
2. **peers_repo**：upsert_pair(device_id,name,token_hash)；by_token_hash(hash)；
   get(device_id)；set_peer_cursor；set_my_acked；touch_last_seen。
3. **pairing**：mint_code()（内存字典，TTL 5min，单次）；redeem(code,device_id,name)→token；
   token sha256 存 peers。
4. **ingest 改造**：新建**非密钥**clip 后 emit clip_new（payload=§1 dict, origin=本机 device_id）到 outbox。
   密钥 clip 不 emit（闸门 B）。远程应用路径**不**经 ingest（用 engine.apply，避免回声）。
5. **engine**：
   - `emit_clip_new(conn, clip, device_id)` / `emit_clip_meta(conn, content_hash, patch, ts, device_id)`。
   - `apply_push(conn, device_id, events)`：幂等应用 + 闸门 + LWW + 游标，返回 acked_upto。
   - `apply_remote_clip_new`：secret_guard 复扫；命中→隔离插入（is_secret=1，不进 FTS/不 emit）；
     否则插入（dedup by hash）+ 写 Obsidian + 入 backup_queue（与本地新建一致，但不再 emit outbox）。
6. **API/鉴权**：server 解析 Authorization；handlers 校验 token sha256 命中 peers；
   pair/sync 端点接线。clip_meta：patch 端点变更 pin/favorite/deleted 时 emit。

## 5. 验收门禁（用 http.client 模拟 Android peer）

- H1. 配对：mint code → redeem 得 token；错误/过期 code 拒绝；token 以 sha256 存储
  （明文不落库）。新版 Android 的正整数 `outbox_base_seq` 必须在兑换前严格校验、
  原值回显，并把 peer cursor 精确设为 `base-1`；旧客户端缺少字段时仍兼容。
- H1a. outbox 高水位：Android 队列已清空但 `AUTOINCREMENT` 高水位大于 0 时，重新
  配对后的下一条显式保存仍可被连续 ack；较低的新基线不得被旧 cursor 吞掉。
- H2. 鉴权：无/错 token 访问 /api/sync/* → 401；正确 token → 200。
- H3. push clip_new：桌面落库 + 写 Obsidian + 入 backup_queue；acked_upto 正确。
- H4. push 幂等：重复推同一批（同 seq）→ 不重复落库、不重复 Obsidian、acked_upto 不变。
- H5. push 密钥事件（peer 实现 bug）：本地隔离、不进 FTS、不入 backup、不回 emit。
- H6. pull：桌面捕获 N 条非密钥 → peer pull since=0 得 N 个 clip_new；密钥 clip 不在其中。
- H7. clip_meta：peer push delete → 本地 clip deleted=1 且移出 FTS；LWW：旧 ts 不覆盖新状态。
- H8. 游标续传：pull 分批（has_more），二次 pull from next_seq 不重不漏。
- H9. 本地新建 clip 进 outbox（非密钥）；密钥不进 outbox。
- H10. 日志不含正文。

## 6. 验证命令

```text
desktop> .venv\Scripts\python -m pytest -q
```
