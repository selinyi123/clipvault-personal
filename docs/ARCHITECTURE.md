# ClipVault Personal — 系统架构（ARCHITECTURE）

> 状态：v1.0 冻结（2026-06-12，Architect: Claude Fable 5）
> 所有数据结构、协议、格式的精确定义在 CONTRACTS.md；本文件回答"系统长什么样、为什么这么分、出错时怎么办"。

## 1. 系统拓扑

```text
┌─────────────────────────── Windows Desktop（主节点）────────────────────────────┐
│                                                                                  │
│  Clipboard Watcher ──► Ingest Pipeline ──► SQLite (WAL, 事实源)                  │
│   (win32 listener        │                   │ clips / memory / outbox / queue   │
│    + 轮询降级)            │                   │ FTS5（仅非密钥）                   │
│                          ▼                   │                                   │
│                 ┌─ Normalizer/Hash           ├──► Obsidian Writer ──► Vault      │
│                 ├─ Secret Guard (闸门A)       ├──► Backup Worker ──► GitHub 私库  │
│                 └─ Rule Classifier           └──► Sync Server (闸门B 在出口)      │
│                                                       ▲                          │
│  stdlib HTTPServer: REST + Web UI + HTTP sync ─────────┘                          │
└──────────────────────────────────┬───────────────────────────────────────────────┘
                                   │ LAN / Tailscale（WireGuard 加密）
                                   │ HTTP push/pull + 配对 token
┌──────────────────────────────────▼───────────────────────────────────────────────┐
│                              Android（采集端 + 消费端）                            │
│                                                                                  │
│  采集: Share Target / 手动保存 / QS Tile / 通知动作 / IME 面板内保存               │
│        │                                                                         │
│        ▼                                                                         │
│  Capture Pipeline（Normalizer/Hash + Secret Guard 闸门A'）──► Room (本地缓存+outbox)│
│        │                                                       ▲                 │
│        └──────────────► Sync Client（HTTP + WorkManager 兜底）──┘                 │
│                                                                                  │
│  ClipVault Keyboard Personal (IME)                                               │
│   - 只读 Room 缓存出推荐与面板（绝不发网络请求/绝不记录按键流）                       │
│   - 显式动作才写入：一键保存剪切板 / 一键入库                                       │
└──────────────────────────────────────────────────────────────────────────────────┘
```

职责铁律：

| 节点 | 做 | 不做 |
|---|---|---|
| Desktop | 监听、分类、入库 Obsidian、备份 GitHub、同步服务端、Memory 主存 | — |
| Android App | 采集（分享/手动）、本地缓存、同步客户端 | 后台监听剪切板（系统不允许）、直接写 Obsidian、直接推 GitHub |
| Keyboard IME | 展示面板、一键粘贴、显式保存 | 记录按键流、自动上传、网络请求 |
| GitHub | 灾难恢复备份 | 同步、实时通道 |

## 2. 核心架构决策（详见 docs/ADR/）

| ADR | 决策 | 一句话理由 |
|---|---|---|
| 0001 | 桌面是主节点 | 只有桌面能稳定后台监听剪切板 + 直接访问 Vault 与 git |
| 0002 | 事件日志式同步，不做状态同步 | clip 是追加型事实，按设备单调序号复制即可，避免 CRDT/冲突复杂度 |
| 0003 | GitHub 备份只存 JSONL，不存 Markdown 镜像 | 单一可恢复事实源，避免双写漂移；Vault 想备份就自己做 git 仓库 |
| 0004 | Keyboard 是伴随式 IME | 不做拼音就当不了默认键盘；按需切入、用完切回是唯一现实路径 |
| 0005 | Desktop=Python，Android=Kotlin | 自用规模性能足够，Codex 产出质量最高，用户可维护 |
| 0006 | Secret Guard 三道闸门 | 捕获、出口、备份序列化三处独立拦截，单点失效不漏 |
| 0007 | v1 推荐引擎纯确定性 | 可解释、可测、零延迟焦虑；AI 是 P2 且仅显式触发 |

## 3. 桌面端模块分解

```text
desktop/
  clipvault/
    core/            # 纯逻辑，零 IO，全部可单测
      models.py      #   Clip / MemoryItem / SecretVerdict 等数据类
      normalize.py   #   规范化 + sha256（合同 NORM-1）
      classifier.py  #   规则分类（合同 CLS-1）
      secret_guard.py#   密钥检测（合同 SG-1）
      suggest.py     #   评分函数（合同 SUG-1）
    store/           # SQLite 访问层
      db.py          #   连接、WAL、迁移执行
      migrations/    #   0001_init.sql ...
      clips_repo.py  #   插入/去重/检索/FTS 维护（FTS 只进非密钥）
      memory_repo.py
      outbox_repo.py
      backup_queue_repo.py
    pipeline/
      ingest.py      # 编排：normalize → dedup → secret → classify → store → 派发
    watcher/
      win_clipboard.py  # AddClipboardFormatListener 消息窗 + 500ms 轮询降级
    obsidian/
      writer.py      # Markdown 生成（合同 OBS-1）+ 原子写 + 幂等
    backup/
      github_backup.py  # JSONL 追加 + git commit/push + 退避重试（合同 GHB-1）
    sync/
      engine.py      # HTTP push/pull 事件日志同步（合同 SYNC-2）
      pairing.py     # 一次性配对码 → 长期 token（哈希存储）
    api/
      server.py      # stdlib HTTPServer：REST（合同 API-1）+ 静态 Web UI + sync routes
      handlers.py    # endpoint 逻辑，直接单测
      webui/         # 单页极简 UI（原生 JS/htmx，不引前端框架）
    runtime/
      obsidian_worker.py # 专用 Vault IO worker：durable queue + wake/周期兜底
    config.py        # config.toml 加载（合同 CFG-1）
    main.py          # 进程入口：单实例锁、线程编排、优雅退出
  tests/
```

**进程模型**：单进程多线程。
- 线程1：win32 消息泵（剪切板监听）
- 线程2：stdlib HTTPServer（REST + Web UI + HTTP push/pull 同步）
- 线程3：专用 Obsidian worker（唯一执行 Vault 文件 IO）
- 线程4：DB-only maintenance（同步 outbox 清理）与可选 GitHub 备份定时器
- 前台通过 SQLite durable queue 交接，并用进程内 wake signal 降低延迟；signal
  可以丢失而任务不能丢失。SQLite 开 WAL，每个线程拥有自己的 connection。

**core/ 与 IO 严格分层**是给 Codex 的硬要求：core 内不允许 import sqlite3 / pathlib 写文件 / 网络库，保证合同级逻辑 100% 可单测。

## 4. Android 端模块分解

```text
android/
  app/        # Jetpack Compose：历史、搜索、配对、设置、同步状态
  core/       # Room schema、Clip 模型、normalize/hash、SecretGuard（Kotlin 移植）
  sync/       # HttpURLConnection 客户端、outbox 排空、WorkManager 周期兜底
  ime/        # ClipVaultKeyboardService (InputMethodService) + 面板 UI
```

- **跨平台一致性**：normalize/hash、Secret Guard、分类规则在两端各有实现，但必须共同通过 `contracts/vectors/*.json` 测试向量（CONTRACTS §8）。向量文件是唯一仲裁。
- **同步触发**：App 前台可显式 HTTP 同步；后台靠 WorkManager（15min 周期 + 充电/WiFi 约束可调）排空 outbox。不做常驻前台服务（自用手机省电优先）。
- **IME 进程边界**：IME 只依赖 core 的只读 DAO + 显式保存接口。代码评审门禁：ime/ 模块内不得出现网络依赖、不得出现按键内容持久化路径。

## 5. 关键数据流

### 5.1 电脑复制 → 手机粘贴

```text
Ctrl+C → Watcher 捕获 → normalize/hash → 去重（命中则 times_seen+1 结束）
→ Secret Guard（命中→隔离，流程终止于本地）
→ 分类 → 同一 SQLite 事务提交 clips + FTS + sync outbox + backup_queue + obsidian_queue
→ 立即返回 watcher；wake 专用 Obsidian worker
→ worker 按类型目录原子写 .md，并以 lease 所有权提交 obsidian_path
→ backup worker 定时 JSONL+push
→ Android 在线：HTTP push/pull 同步；离线：下次连上 pull
→ IME 面板"电脑同步"页可见 → 一键粘贴
```

### 5.2 手机采集 → Obsidian

```text
分享/手动保存 → Android Capture Pipeline（normalize + Secret Guard）
→ Room 落库 + outbox → HTTP push 到桌面（离线则排队）
→ 桌面 ingest：去重 → 再过 Secret Guard（桌面规则可能更新）→ 分类
→ 原子提交 Obsidian/backup intents（同 5.1 尾部），文件 IO 由 worker 异步执行
→ ack 回 Android，标记已同步
```

### 5.3 输入续词（全程本地）

```text
用户在 IME 输入前缀 → suggest 查 Room（memory 缓存 + 最近 clips）
→ 评分排序（SUG-1）→ Suggestion Bar 展示 → 点击 commitText
→ 本地 use_count+1 → 作为 memory_usage 事件随下次同步回桌面
```

## 6. 同步设计要点（协议细节见 CONTRACTS §5）

- **事件日志复制**：每台设备维护自增 `seq` 的 outbox；对端记录"我已应用到对方的哪个 seq"。重连后通过 HTTP push/pull 从游标续传。天然幂等：按 `(origin_device, seq)` 去重，clip_new 再按 content_hash 去重。
- **冲突**：只有元数据标志（pin/favorite/delete）可能冲突 → 字段级 LWW（按事件时间戳），相同时间戳 delete 赢。内容本身永不冲突（追加型 + 哈希去重）。
- **拓扑**：星型，桌面是 hub。v1 只有一台 Android，协议按多设备设计（device_id 区分）但不实现多端转发。
- **安全**：配对 = 桌面 Web UI 生成一次性 8 位码（5 分钟有效）→ Android 提交换取 32 字节长期 token → 桌面只存 token 的 sha256。传输加密依赖 Tailscale（WireGuard）；纯 LAN 模式明文是已接受的残余风险（THREAT_MODEL §5），P2 提供自签 TLS + 证书钉扎。
- **密钥不进同步**：is_secret=1 的 clip 在 outbox 入队处被闸门 B 拒绝（两端同规则）。

## 7. Obsidian 写入要点（格式见 CONTRACTS §6）

- 一条 clip = 一个 .md 文件，文件名含时间戳 + 首行 slug + id 后缀，天然防撞。
- **原子写**：tmp 文件 + `os.replace`，避免 Obsidian/同步盘读到半截文件。
- **幂等**：写成功才记 `obsidian_path`；已有 path 的 clip 永不重写。**用户删了笔记 = 用户的策展决定，绝不复活。**
- **前台不做文件 IO**：捕获、release、sync push 只提交 durable intent 并唤醒 worker，
  避免 Vault、同步盘或杀毒扫描阻塞 watcher/HTTPServer。
- Vault 不可达（路径不存在/被占用）→ lease 释放回重试队列，指数退避，Web UI
  暴露不含正文/路径的聚合状态；绝不丢数据（clip 与 intent 已在 SQLite）。
- `soft_runtime_budget_ms` 只限制是否开始下一条，不能取消已经进入内核的文件系统调用；
  进程退出通过 stop + wake 让等待中的 worker 及时收束。

## 8. GitHub 备份要点（布局见 CONTRACTS §7）

- 每 15 分钟（可配）排空 backup_queue → 按日期追加 `clips/YYYY/MM/YYYY-MM-DD.jsonl` → 单次 commit → push。
- push 失败：保留队列、指数退避（1m→2m→…→30m 封顶）、Web UI 亮红灯。本地 JSONL 已 commit，不丢。
- **永不 pull / 永不 force push / 永不 rebase**。备份仓库是只追加日志。
- 恢复工具（v1.0）：从 JSONL 重建 SQLite 与 Markdown，这是"备份可用"的唯一证明，列入 v1.0 门禁。
- 若密钥事后泄漏入库：执行 docs/RUNBOOK_PURGE.md（git filter-repo + 远端强推 + token 轮换）——这是唯一允许改写历史的场景。

## 9. 失败模式与对策

| 故障 | 行为 |
|---|---|
| 剪切板监听器注册失败 | 自动降级 500ms 轮询，日志告警 |
| 监听进程崩溃 | 计划任务/开机自启拉起；单实例锁防双开 |
| SQLite 忙/锁 | WAL + busy_timeout=5s + store 层单写锁 |
| Vault 不可达 | 重试队列 + 退避，UI 黄灯 |
| GitHub 推送失败 | 队列保留 + 退避，UI 红灯 |
| 桌面离线 | Android outbox 累积，重连续传 |
| Android 进程被杀 | outbox 持久化在 Room；WorkManager 周期兜底 |
| HTTP 同步请求失败/超时 | Android outbox 持久化保留，WorkManager 后续重试 |
| 超大内容（>1MB） | 拒收 + 通知（CFG 可调上限） |
| 时钟漂移 | 排序靠 ULID/seq，不靠墙钟；LWW 冲突极少且后果轻（仅标志位） |

## 10. 可观测性（自用级别）

- 结构化日志（JSON lines）落本地文件，按天轮转，保留 14 天。
- Web UI 状态页：最近捕获、队列深度（obsidian/backup/outbox）、最后 push 时间、配对设备与最后在线时间。
- 不引入任何外部监控/上报。**日志中绝不打印 clip 正文**（只打 id + hash 前 8 位 + 长度），防止日志成为第四条泄漏通道。

## 11. 性能预算（自用规模，超出即架构错误）

| 指标 | 预算 |
|---|---|
| 日捕获量 | < 500 条 |
| 库规模 | 10 万条 clip 无感 |
| 捕获→落库 | < 100ms |
| FTS 搜索 | < 50ms |
| IME 面板冷开 | < 150ms |
| 建议查询（本地） | < 30ms |
| 同步一批 100 条 | < 2s（LAN） |

不需要分库、分片、缓存层、消息中间件。出现这些字眼即范围蔓延。

## 12. 升级与迁移

- SQLite：`schema_meta(version)` + 顺序迁移脚本 `migrations/000N_*.sql`，启动时自动执行，先备份 db 文件再迁移。
- 同步协议：envelope 带 `v` 字段；不兼容时高版本端拒绝并提示升级（自用两端一起升，不做兼容矩阵）。
- 合同变更流程：改 CONTRACTS.md → 标注版本号与日期 → 同步更新两端实现与测试向量。Builder 不得静默改合同（见 PROMPTS/BUILDER_CODEX_GOAL.md）。
