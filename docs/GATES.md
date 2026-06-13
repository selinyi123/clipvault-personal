# ClipVault Personal — 验收门禁（GATES）

> 规则：门禁先冻结，结果后判断。Builder 不得自我验收；以原始测试输出、diff、文件为准。
> 全局门禁适用于每一个 slice；版本门禁在对应版本的最后一片全部复验。

## 全局门禁（每个 PR 都要过）

- G1. 密钥不进 Obsidian、不进 GitHub、不进同步、不进 FTS、不进 memory 派生。
- G2. IME 不记录普通键入；ime/ 模块无网络依赖。
- G3. GitHub 只做备份，不做同步；禁止 pull/force-push/rebase（purge runbook 除外）。
- G4. SQLite 是本地事实源；任何数据可从本地重建展示。
- G5. 范围外功能未经 Architect 批准不得出现在 diff 中。
- G6. 日志不含 clip 正文。
- G7. 所有新行为有对应测试；contracts/vectors 既有用例不得删改。
- G8. core/ 纯逻辑层无 IO import（桌面端）。

## v0.1 门禁（Desktop Core）

- 能初始化 SQLite（迁移从零执行成功，schema_meta=当前版本）。
- 能保存一条 clip（完整字段，ULID、UTC 时间）。
- 相同 content_hash 去重：times_seen 递增，不新建行；已删除的不复活。
- 分类器对 vectors/classifier.json 全部通过。
- Secret Guard 对 vectors/secret_guard.json 全部通过（含负例不误报）。
- is_secret=1 的 clip 不出现在 clips_fts、backup_queue、Obsidian 输出目录。
- 非密钥 clip 生成 OBS-1 格式 Markdown（golden file 比对），原子写、幂等、防撞名。
- backup_queue 正确入队公开 clip；backup worker 产出 JSONL + git commit（用本地裸仓库测试，不碰真实远端）。
- 剪切板监听：复制 → 落库 ≤ 1s（手动验证脚本）；监听注册失败时轮询降级生效（单测模拟）。
- Web UI：能搜索（FTS）、能 pin/favorite/delete、能查看隔离区并释放、能看队列状态。
- release 流程：释放后该 clip 走完 Obsidian + backup 管线。

## v0.2 门禁（Android Capture）

- 分享任意文本到 ClipVault → Room 落库 ≤ 1s，含 normalize/hash/secret 判定。
- Kotlin 端对三个 vectors 文件全部通过。
- 手动保存当前剪切板可用（App 内 + QS Tile）。
- 历史列表可搜索、可删除；密钥项脱敏显示。
- 无桌面在线时一切采集功能正常（离线优先验证）。

## v0.3 门禁（双端同步）

- 配对流程走通：一次性码 → token → 重启两端后仍可认证。
- 桌面复制 → Android 可见 ≤ 3s（同一 LAN，App 前台）。
- Android 采集 → 桌面落库 → Obsidian 文件出现 ≤ 5s。
- 断网采集 20 条 → 恢复连接 → 全部到达且不重复（按 hash 与 (device,seq) 双重幂等验证）。
- 两端各自重启后从游标续传，无丢失、无重复。
- 密钥 clip 在两端都不进 outbox（单测 + 集成测试）。
- pin/favorite/delete 双向传播；冲突按 LWW，delete 优先。

## v0.4 门禁（Personal Memory）

- memory CRUD（Web UI + API）可用；(kind,text) 唯一。
- Obsidian 标题导入、GitHub 仓库名导入可重复执行且幂等。
- memory_upsert/delete 经同步到达 Android Room。
- 从 clip 一键提升为 memory 项。

## v0.5 门禁（Keyboard Personal）

- IME 可在系统输入法列表启用，切入后面板冷启动 ≤ 150ms。
- 面板：最近剪切板 / 同步内容 / 6 类 memory 可浏览，一键 commitText 粘贴成功。
- "保存当前剪切板"按钮在 IME 激活时可读剪切板并入库。
- 一键切回上一个输入法。
- **隐私验证**：人工代码审查 + 自动检查 ime/ 模块依赖清单无网络库；输入普通文本后检查 Room 无按键流痕迹。

## v0.6 门禁（Suggestion Engine）

- 桌面 /suggest 与 Android 本地 suggest 对同一输入输出一致排序（共享评分向量用例）。
- 前缀输入 2 字符 → 建议出现 ≤ 50ms（Android 真机）。
- use_count/last_used_at 随点击更新并随同步回流桌面。
- 权重改 config 后生效，无需改代码。

## v0.7 门禁（Context Action）

- 每种 content_type 触发对应动作 chips（规则表驱动）。
- 动作全部本地执行，无 AI、无网络调用。

## v1.0 门禁（稳定自用版）

桌面侧（S012，2026-06-13 达成）：

- [x] 恢复演练：tools/restore.py 从备份 JSONL 重建 SQLite，clip 数与 hash 全等（C6/I3）。
- [x] sync outbox 有界：裁剪所有对端已确认事件（I1–I2）。
- [x] 全局门禁 G1–G8 全量复验（121 tests 绿）。
- [x] 文档齐：INSTALL.md（安装/配置/备份仓库/配对/自启/恢复/隐私）、RUNBOOK_PURGE.md。
- [ ] 连续运行 7 天无崩溃 —— **运行期观察项**（无法在构建期断言；靠日志佐证，交 Owner 验证）。

Android 侧（S005/S008/S009）与端到端双端联调在 Android 完成后复验。
