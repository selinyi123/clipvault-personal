# ClipVault Personal — 合同（CONTRACTS）

> Status: **v1 FROZEN for Slice 001–004 scope**（2026-06-12, Architect: Claude Fable 5）
> 本文件中每个合同有编号（NORM-1, CLS-1, SG-1, OBS-1, GHB-1, SYNC-1, API-1, SUG-1, CFG-1, DB-1）。
> Builder 实现必须逐条对应；修改合同必须先在 HANDOFF.md 提出 disagreement，由 Architect 裁决。
> 两端（Python/Kotlin）共享的逻辑以 `contracts/vectors/*.json` 测试向量为唯一仲裁（§8）。

---

## 1. Clip 对象（DB-1 的载体，同步与备份的统一序列化形式）

```json
{
  "id": "01J9XKQ8ZJ3F4Y5B6C7D8E9F0G",
  "content": "the normalized text",
  "content_hash": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "content_type": "text",
  "is_secret": false,
  "secret_level": null,
  "secret_reasons": [],
  "source_device": "desktop-main",
  "source_app": "chrome.exe",
  "created_at": "2026-06-12T08:30:00Z",
  "last_seen_at": "2026-06-12T08:30:00Z",
  "times_seen": 1,
  "pinned": false,
  "favorite": false,
  "deleted": false
}
```

规则：
- `id`：ULID（26 字符 Crockford base32），生成于首次捕获的设备。
- `content_hash`：sha256 hex（小写）of UTF-8 bytes of normalized content（NORM-1）。
- `content_type` ∈ `text | url | path | command | code | error_log | prompt`。`secret` **不是** content_type——密钥性由 `is_secret` 独立表达，被隔离的 clip 仍保留其内容类型。
- `secret_level` ∈ `null | "hard" | "suspect"`；`secret_reasons` 为命中的规则 ID 数组（如 `["SG-PEM"]`）。
- 时间一律 UTC ISO8601，秒精度，`Z` 后缀。

## 2. 规范化与哈希（NORM-1）

输入：平台剪切板取到的 Unicode 文本。按顺序执行：

1. `CRLF` 与孤立 `CR` → `LF`
2. Unicode NFC 规范化
3. 去掉字符串**末尾**的空白（行内与行首空白全部保留——缩进和 Markdown 硬换行是内容）
4. 结果为空或纯空白 → **拒收**（不产生 clip）
5. UTF-8 编码长度 > `max_clip_bytes`（默认 1 MiB）→ **拒收** + 用户可见通知
6. `content_hash = sha256(utf8(result))`

去重：同 `content_hash` 已存在（含 deleted=1 的）→ 不新建，原 clip `times_seen += 1`、`last_seen_at` 更新；若原 clip `deleted=1` 则保持 deleted（用户删过的不复活）。

## 3. 规则分类器（CLS-1）

前置：Secret Guard 先行（SG-1），分类不因 is_secret 改变。
对 normalized content 按以下顺序首个命中即返回：

| 序 | 类型 | 规则 |
|---|---|---|
| 1 | `url` | 所有非空行都匹配 `^https?://\S+$`（≤10 行） |
| 2 | `path` | 单行且匹配 `^[A-Za-z]:\\` 或 `^\\\\` (UNC) 或 `^(/|~/)[^\s]*$` |
| 3 | `command` | 单行 ≤300 字符，且（以 `$ ` 或 `> ` 开头）或首词 ∈ {git, docker, docker-compose, kubectl, npm, pnpm, yarn, pip, pipx, uv, python, node, cargo, go, adb, gh, ssh, scp, curl, wget, powershell, pwsh, winget, choco} |
| 4 | `error_log` | 含 `Traceback (most recent call last)`，或 `\b(ERROR|FATAL|Exception)\b` 出现 ≥2 次，或 ` at .+\(.+:\d+\)` 栈帧行 ≥2 |
| 5 | `code` | ≥3 行，且命中以下之一：成对 `{`/`}`；行首 `def |class |import |from |function |const |let |var |#include|public |private `；含 ``` 围栏 |
| 6 | `prompt` | 以 {`你是`, `请你`, `你现在是`, `扮演`, `You are`, `Act as`, `Your task`} 之一开头，或含 `### Instruction` / `<system>` |
| 7 | `text` | 兜底 |

规则集版本：`CLS-1`。新增/调序必须升版本号并更新 `contracts/vectors/classifier.json`。

## 4. Secret Guard（SG-1）

### 4.1 硬规则（level=hard）

| 规则 ID | 模式 |
|---|---|
| SG-PEM | `-----BEGIN (RSA \|EC \|OPENSSH \|DSA \|PGP \|ENCRYPTED )?PRIVATE KEY-----` |
| SG-PUTTY | `PuTTY-User-Key-File` |
| SG-AWS-ID | `\bAKIA[0-9A-Z]{16}\b` |
| SG-AWS-SECRET | `(?i)aws.{0,20}(secret\|key).{0,20}['\"][0-9A-Za-z/+=]{40}['\"]` |
| SG-GH | `\bgh[pousr]_[A-Za-z0-9]{36,}\b` 或 `github_pat_[A-Za-z0-9_]{22,}` |
| SG-SLACK | `\bxox[baprs]-[A-Za-z0-9-]{10,}\b` |
| SG-OPENAI | `\bsk-(proj-\|ant-)?[A-Za-z0-9_-]{20,}\b` |
| SG-GOOGLE | `\bAIza[0-9A-Za-z_-]{35}\b` |
| SG-JWT | `\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b` |
| SG-ASSIGN | `(?i)\b(password\|passwd\|pwd\|secret\|token\|api[_-]?key\|access[_-]?key\|client[_-]?secret\|auth)\b\s*[:=]\s*\S{8,}` |
| SG-CONNSTR | `(?i)\b(postgres(ql)?\|mysql\|mongodb(\+srv)?\|redis\|amqp)://[^\s:@/]+:[^\s@]+@` |
| SG-ENV | ≥2 行匹配 `^[A-Z][A-Z0-9_]{2,}=\S+$` 且其中至少一行变量名含 `KEY\|TOKEN\|SECRET\|PASS\|PWD` |

### 4.2 熵启发（level=suspect）

整体内容为单 token（无空白），长度 ≥24，字符集 ⊆ `[A-Za-z0-9+/=_\-]`，Shannon 熵 ≥ 3.8 bits/char，且不命中 url/path 形态 → `SG-ENTROPY`。

**SG-1.1 修订（2026-06-13，裁决记录见 HANDOFF D-001）**：以下已知格式的非密钥内容从熵规则中排除（它们是常见剪切板内容，且仅凭形态即可证明不是凭据）：
- 纯十六进制且长度为 32/40/64（md5 / git sha1 / sha256 摘要）
- UUID 格式（8-4-4-4-12 hex）
- 以 `/` 或 `~` 开头的 token（unix 路径形态）
- 以已知图片 base64 魔数开头：`iVBORw0KGgo`（PNG）、`/9j/`（JPEG）、`R0lGOD`（GIF）

### 4.3 判定与隔离语义

`verdict = {is_secret, level, reasons[]}`。hard 与 suspect 同等隔离，UI 显示级别与原因。

is_secret=1 的 clip：

| 通道 | 行为 |
|---|---|
| FTS5 索引 | 不进入 |
| sync outbox | 入队处拒绝（闸门 B） |
| Obsidian | 不写入 |
| GitHub backup_queue | 不入队；backup worker 序列化前再扫一次（闸门 C），命中即丢弃并 ERROR 日志 |
| Personal Memory 派生 | 不参与 |
| UI 预览 | 脱敏：前 4 字符 + `••••`（长度不泄露） |
| 日志 | 永不打印正文 |

**释放（release）**：用户在 Web UI / App 显式点击"非密钥，释放"→ `is_secret=0, released=1, released_at` 落库 → 重新走 Obsidian/备份/同步管线。释放是唯一出口，且留审计字段。

## 5. 同步协议（SYNC-1）

传输：WebSocket，桌面端点 `ws://{host}:{port}/sync`。文本帧，每帧一个 JSON envelope：

```json
{"v": 1, "type": "auth", "msg_id": "01J9...", "device_id": "android-pixel", "ts": "2026-06-12T08:30:00Z", "payload": {}}
```

### 5.1 消息类型

| type | 方向 | payload |
|---|---|---|
| `auth` | C→S | `{token, device_name, proto: 1}` |
| `auth_ok` | S→C | `{server_device_id, your_last_acked_seq}` |
| `auth_fail` | S→C | `{reason}`，随后服务端关闭连接 |
| `push` | 双向 | `{events: [Event, ...]}`，单批 ≤100 |
| `ack` | 双向 | `{upto_seq}`（对端 outbox 的最高连续已应用 seq） |
| `pull` | 双向 | `{since_seq}` |
| `events` | 双向 | `{events: [...], next_seq, has_more}`（对 pull 的响应，has_more=true 时继续 pull） |
| `ping` / `pong` | 双向 | `{}`；30s 间隔，90s 无响应断连重连 |
| `error` | 双向 | `{code, message}` |

### 5.2 Event

```json
{
  "origin_device": "android-pixel",
  "seq": 42,
  "kind": "clip_new",
  "ts": "2026-06-12T08:30:00Z",
  "data": { }
}
```

| kind | data | 语义 |
|---|---|---|
| `clip_new` | 完整 Clip 对象（§1） | 幂等插入：先按 (origin_device,seq) 去重，再按 content_hash 去重合并 |
| `clip_meta` | `{content_hash, patch: {pinned?|favorite?|deleted?}, ts}` | 字段级 LWW（按 ts）；同 ts 时 deleted=true 赢 |
| `memory_upsert` | 完整 MemoryItem（v0.4 启用） | 按 (kind,text) 幂等 upsert，use_count 取 max |
| `memory_delete` | `{kind, text, ts}` | LWW |

不变量：
- is_secret=1 的 clip **永不**出现在 events 中；接收端若收到（对端实现 bug），必须本地隔离并记 ERROR。
- 接收端持久化 `peer_cursor[origin_device] = max contiguous seq applied`，ack 该值；发送端只清除已 ack 的 outbox 条目。

### 5.3 配对（PAIR-1）

1. 桌面 Web UI 点击"配对新设备" → 生成 8 位一次性码（TTL 5 分钟，单次有效）。
2. Android 调 `POST /api/pair` `{code, device_id, device_name}` → 返回 `{token}`（32 字节 base64url 随机）。
3. 桌面持久化 `sha256(token)`；明文 token 只存 Android Keystore 加密存储。
4. 解除配对：Web UI 删除设备 → 该 token 失效，对应 peer cursor 与 outbox 游标删除。

## 6. Obsidian Markdown 格式（OBS-1）

路径：`{vault_path}/{type_dir}/{YYYYMMDD}-{HHMMSS}_{slug}_{id6}.md`

- `type_dir` 由 config 的类型→目录映射决定，默认：

```text
text      → 00_Inbox/Clipboard
prompt    → 01_Prompt
code      → 02_Code
error_log → 03_Error_Log
url       → 04_Web_Link
command   → 05_Command
path      → 00_Inbox/Clipboard
```

- `slug`：首个非空行 → 去除 `\/:*?"<>|#^[]` 与首尾空白 → 空格转 `-` → 截 24 字符；为空则用 `clip`。
- `id6`：ULID 后 6 位。时间用 clip.created_at 转本地时区。

文件内容：

```markdown
---
clipvault_id: 01J9XKQ8ZJ3F4Y5B6C7D8E9F0G
created: 2026-06-12T08:30:00Z
source_device: desktop-main
source_app: chrome.exe
type: code
content_hash: sha256:9f86d081...
tags:
  - clipvault
  - clipvault/code
---

```python
<content>
```
```

- `code` 类型：正文用围栏代码块包裹，语言为启发式猜测（猜不出用空标识）；其余类型正文为原文。
- 若正文自身含 ``` 围栏，外层围栏加长为 ```` 直至不冲突。
- 写入：先写 `{final_path}.tmp` 再 `os.replace`；目标已存在则文件名追加 `-1`、`-2`。
- 成功后才写 `clips.obsidian_path`；已有 obsidian_path 的 clip 永不再写（用户删除 = 策展，不复活）。

## 7. GitHub 备份（GHB-1）

私有仓库布局（本地工作副本路径 = `config.backup.repo_path`，须已 `git init` 并配好 remote）：

```text
clips/
  2026/06/2026-06-12.jsonl     # 每行一个 §1 Clip 对象 JSON，按 backup 时间追加
meta/
  device.json                  # {device_id, schema: 1}
```

- worker 周期（默认 15 分钟）：取 backup_queue 中 state=pending → 闸门 C 复扫 Secret Guard（用当前规则集对 content 再判，命中→丢弃+ERROR）→ 追加当日 JSONL → `git add -A && git commit -m "backup: {n} clips {iso_ts}"` → `git push`。
- push 失败：commit 保留在本地，queue 条目标记 done（数据已在本地 git），下轮重试 push；退避 1m→2m→4m→…→30m 封顶。
- 同一 clip 终生只备份一次（按 id）；clip_meta 变化不重备（备份是灾难恢复，不是镜像）。
- 禁止：pull、force push、rebase、amend。唯一例外见 docs/RUNBOOK_PURGE.md。
- 恢复合同：`tools/restore.py` 读全部 JSONL → 重建 SQLite → 可选重建 Markdown。v1.0 门禁要求演练通过。

## 8. 跨平台测试向量（VEC-1）

仓库根 `contracts/vectors/`：

| 文件 | 内容 | 必须通过方 |
|---|---|---|
| `normalization.json` | `[{raw, normalized, hash} ...]` ≥20 例（CRLF、NFC、尾空白、emoji、中文） | Python + Kotlin |
| `classifier.json` | `[{content, expected_type} ...]` 每类型 ≥5 例 + 边界例 | Python + Kotlin |
| `secret_guard.json` | `[{content, is_secret, level, reasons} ...]` 每条硬规则 ≥2 正例，≥10 个易误报负例（普通 URL、git hash、base64 图片头、UUID） | Python + Kotlin |

两端各写一个加载向量并断言的测试。向量文件由 Architect 维护语义、Builder 可提交新增用例（不得删改既有用例）。

## 9. SQLite Schema（DB-1，桌面端 v1）

```sql
PRAGMA journal_mode=WAL;

CREATE TABLE schema_meta (version INTEGER NOT NULL);

CREATE TABLE clips (
  id            TEXT PRIMARY KEY,
  content       TEXT NOT NULL,
  content_hash  TEXT NOT NULL UNIQUE,
  content_type  TEXT NOT NULL DEFAULT 'text',
  is_secret     INTEGER NOT NULL DEFAULT 0,
  secret_level  TEXT,
  secret_reasons TEXT,                -- JSON array
  released      INTEGER NOT NULL DEFAULT 0,
  released_at   TEXT,
  source_device TEXT NOT NULL,
  source_app    TEXT,
  created_at    TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  times_seen    INTEGER NOT NULL DEFAULT 1,
  pinned        INTEGER NOT NULL DEFAULT 0,
  favorite      INTEGER NOT NULL DEFAULT 0,
  deleted       INTEGER NOT NULL DEFAULT 0,
  obsidian_path TEXT,
  backed_up_at  TEXT
);
CREATE INDEX idx_clips_created ON clips(created_at DESC);
CREATE INDEX idx_clips_type    ON clips(content_type);

-- FTS 由 store 层代码维护（不用触发器）；不变量：is_secret=1 或 deleted=1 的行不得存在于此表
CREATE VIRTUAL TABLE clips_fts USING fts5(id UNINDEXED, content);

CREATE TABLE memory_items (
  id           TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,          -- term|phrase|prompt|command|key_info|path
  text         TEXT NOT NULL,
  label        TEXT,
  pinned       INTEGER NOT NULL DEFAULT 0,
  use_count    INTEGER NOT NULL DEFAULT 0,
  last_used_at TEXT,
  source       TEXT NOT NULL DEFAULT 'manual',  -- manual|derived|obsidian_import|github_import
  created_at   TEXT NOT NULL,
  deleted      INTEGER NOT NULL DEFAULT 0,
  UNIQUE(kind, text)
);

CREATE TABLE sync_outbox (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  kind       TEXT NOT NULL,
  payload    TEXT NOT NULL,            -- Event.data 的 JSON
  created_at TEXT NOT NULL
);

CREATE TABLE sync_peers (
  device_id    TEXT PRIMARY KEY,
  device_name  TEXT,
  token_hash   TEXT NOT NULL,
  my_acked_seq INTEGER NOT NULL DEFAULT 0,   -- 对端已确认我 outbox 到哪
  peer_cursor  INTEGER NOT NULL DEFAULT 0,   -- 我已应用对端到哪
  paired_at    TEXT NOT NULL,
  last_seen_at TEXT
);

CREATE TABLE backup_queue (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  clip_id    TEXT NOT NULL UNIQUE,
  state      TEXT NOT NULL DEFAULT 'pending',  -- pending|done|dropped_secret
  attempts   INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TEXT NOT NULL,
  done_at    TEXT
);
```

Android Room 为其子集：clips（缓存）、memory_items（缓存）、sync_outbox、自己的 cursor 存储。字段名与语义必须一致。

## 10. 桌面 REST API（API-1）

Base：`http://{host}:{port}/api`，仅本机与配对设备使用。除 `/pair` 与 `/health` 外，所有请求要求 `Authorization: Bearer {token}`（本机 Web UI 用 localhost 豁免：仅当连接源为 127.0.0.1）。

| Method | Path | 说明 |
|---|---|---|
| GET | `/health` | `{status, version, db_ok}` |
| POST | `/pair` | §5.3 |
| GET | `/clips?q=&type=&secret=&limit=50&before_id=` | 列表/FTS 搜索（q 走 FTS，密钥项只在 secret=1 时返回且内容脱敏） |
| POST | `/clips` | `{content, source_app?}` 手动添加，走完整 ingest 管线 |
| PATCH | `/clips/{id}` | `{pinned?|favorite?|deleted?}` → 产生 clip_meta 事件 |
| POST | `/clips/{id}/release` | 释放隔离（§4.3） |
| GET | `/memory?kind=&q=` | memory 列表 |
| POST | `/memory` | upsert MemoryItem |
| DELETE | `/memory/{id}` | 软删 |
| GET | `/suggest?prefix=&app=&limit=10` | SUG-1 评分结果 |
| GET | `/status` | 队列深度、最后备份/同步时间、设备列表 |

错误统一 `{error: {code, message}}`，HTTP 语义化状态码。

## 11. Suggestion 评分（SUG-1）

候选集：memory_items（deleted=0）∪ 最近 30 天非密钥非删除 clips（仅 favorite 或 times_seen≥3 的）。

```text
score = 3.0 * pinned
      + match:  prefix(text 或 label, query, 忽略大小写) → 1.5
                否则 substring → 0.6
                否则 0（剔除候选）
      + 1.0 * ln(1 + use_count) * exp(-days_since_last_use / half_life_days)
      + 0.5 * (source_app == 当前 app 提示词 ? 1 : 0)     # v0.6 起
```

- query 为空时：match 项记 0，仅按 pinned + 频率/衰减出"最近常用"。
- 排序：**SUG-1.1（2026-06-13，D-008）**：pinned 为硬置顶层（PRODUCT_SPEC "pinned 永远置顶"），
  排序键 = (pinned, score, last_used_at) 全部 DESC。pinned 不再只是 +3.0 加权——极高频项也不得越过
  pinned 项（ADR-0007"可预期即舒适"）。默认 half_life_days=14，权重全部可在 config 覆盖。
- 实现：SQL 预筛（LIKE prefix / FTS prefix）→ 取 ≤200 候选 → 内存重排。**IME 端只查本地 Room，不发网络。**

## 12. 桌面配置（CFG-1，config.toml）

```toml
[device]
device_id   = ""            # 留空首次启动自动生成并回写
device_name = "desktop-main"

[storage]
db_path        = "data/clipvault.db"
max_clip_bytes = 1048576

[watcher]
poll_fallback_ms = 500

[obsidian]
vault_path = "D:/Obsidian/Vault"
[obsidian.type_dirs]
text      = "00_Inbox/Clipboard"
prompt    = "01_Prompt"
code      = "02_Code"
error_log = "03_Error_Log"
url       = "04_Web_Link"
command   = "05_Command"
path      = "00_Inbox/Clipboard"

[backup]
repo_path        = "D:/clipvault-backup"
interval_minutes = 15
enabled          = true

[server]
host = "0.0.0.0"
port = 8787

[suggest]
half_life_days = 14
w_pinned = 3.0
w_prefix = 1.5
w_substr = 0.6
w_freq   = 1.0
w_app    = 0.5

[log]
dir = "logs"
retention_days = 14
```

缺失文件 → 生成默认并提示填 vault_path；非法值 → 启动失败并给出具体字段错误（fail fast，不带病运行）。
