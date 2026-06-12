# ClipVault Personal — 切片路线图（ROADMAP）

> 每个 Slice = 一个 PR 的体量。完成定义：门禁全过 + HANDOFF.md 更新 + Architect 裁决通过。
> 顺序经过依赖排序，不要跳片。每片的详细规格由 Architect 在开工前写入 docs/SLICES/。

| Slice | 版本 | 名称 | 内容 | 依赖 |
|---|---|---|---|---|
| S001 | v0.1a | Core Pipeline（纯逻辑） | 仓库骨架、core/（models/normalize/classifier/secret_guard）、store/（迁移+clips_repo+backup_queue）、obsidian/writer、测试向量接入。**无监听、无网络、无 UI** | — |
| S002 | v0.1b | Desktop Service | win32 剪切板监听 + 轮询降级、config 加载、ingest 编排、单实例锁、日志、main 入口 | S001 |
| S003 | v0.1c | GitHub Backup Worker | JSONL 序列化、git 操作、定时器、退避、闸门 C、RUNBOOK_PURGE.md | S001 |
| S004 | v0.1d | Local API + Web UI | FastAPI REST（API-1）、极简 Web UI（历史/搜索/隔离区/状态）、localhost 豁免 | S002 |
| S005 | v0.2 | Android Capture App | Compose 工程、Room、Share Target、手动保存、QS Tile、历史 UI、Kotlin 端 vectors 通过、outbox 表 | —（可与 S003/S004 并行） |
| S006 | v0.3 | 双端同步 | 桌面 WS 服务端 + 配对（PAIR-1）、Android WS 客户端 + WorkManager 兜底、push/ack/pull、双重幂等、状态 UI | S004, S005 |
| S007 | v0.4a | Personal Memory（桌面） | memory_items CRUD、Web UI 管理页、Obsidian 标题导入、GitHub 仓库名导入、clip 一键提升 | S004 |
| S008 | v0.4b | Memory 同步 | memory_upsert/delete 事件、Android Room 缓存、App 内浏览 | S006, S007 |
| S009 | v0.5 | Keyboard Personal | InputMethodService、面板（剪切板/同步/6 类 memory）、一键粘贴、IME 内保存、切回键、隐私门禁 | S008 |
| S010 | v0.6 | Suggestion Engine | SUG-1 双端实现、IME Suggestion Bar、使用计数回流、评分一致性用例 | S009 |
| S011 | v0.7 | Context Action（规则版） | 类型→动作 chips（桌面通知/Web、Android 通知与 IME），规则表驱动 | S010 |
| S012 | v1.0 | 加固与恢复 | tools/restore.py、7 天稳定性、全门禁复验、安装/配置文档 | 全部 |

## 并行建议

桌面线（S001→S002→S003→S004）与 Android 线（S005）可并行；S006 是合流点。
如果只有一个 Builder 串行做，按编号顺序执行。

## 范围刹车（出现以下字眼直接拒绝）

多用户、账号、支付、云中转服务器、消息中间件、微服务、分库分表、iOS、拼音引擎、
皮肤、插件市场、浏览器扩展（P2 再议）、实时协同编辑、E2E 加密中继。
