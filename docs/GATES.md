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

---

# Keyboard 主线门禁（v1.1 → v3.0）

> 北极星：**做一个完整的（中文）输入法**。主线定义见 [ROADMAP_V2_KEYBOARD.md](ROADMAP_V2_KEYBOARD.md)，
> 隐私分层见 [ADR-0008](ADR/0008-v1-as-runtime.md)（键入用于输入、显式保存才成资产、L0–L4）。
> 本段是"先冻验收标准、再开工"的门禁登记处；**门禁先冻结，结果后判断**（Builder 不自我验收）。
> 全局门禁 **G1–G8 仍逐 PR 适用**（IME 模块无网络、密钥不外泄、日志无正文、core 无 IO、新行为有测试）。
>
> **可验证性标签**（本仓库环境约束）：
> - 🟢 **本地可验**：桌面 Python / contracts 向量 / host-JVM 单测，能在 Linux 跑命令断言。
> - 🟡 **CI 可验**：需 Android SDK 编译 / windows-latest，本地编译不了，靠 CI 产出佐证。
> - 🔵 **设备/人工**：需真机或人工体验，交 Owner 验证（Builder 不代签）。

## v1.1 门禁（Runtime 收口）— 现状：PR2/PR3 已落地

- 🟡 Android 经 `ClipVaultFacade`（`listRecentClips`/`listMemory`/`saveExplicit`）访问数据，
  Panel IME 不再直接碰 Room DAO / Capture / SyncScheduler；行为不变（同样查询与 take(40)）。
- 🔵 模拟器/真机：App 启动无崩溃，IME 仍注册可用。
- 🟢 文档：v1 明确为 Runtime（ADR-0008 在 repo），原则 P2/P7 演进已记录。

## v1.2 门禁（SyncTransport 抽象）— 未开工

- 🟢 HTTP push/pull 抽象为 `SyncTransport` 接口，既有同步行为与契约**不变**（既有 sync 测试全绿、
  contracts/vectors 既有用例不删改）。
- 🟢 抽象层不引入云、不改变"桌面是事实源、密钥不进 outbox"语义（单测覆盖）。
- 🟢 文档：ADR-0009（sync-transport-abstraction）+ CONTRACTS_SYNC_TRANSPORT.md 落地。

## v2.0 门禁（双 IME 入口）— 现状：PR4 Full Keyboard Lab 已落地

- 🔵 同一 APK 内两个 InputMethodService 均可在系统输入法列表启用：ClipVault Panel + ClipVault Keyboard Lab。
- 🔵 Keyboard Lab：可用英文 QWERTY（一次性 shift + ?123 符号层 + 空格/回车/退格）+ ClipVault 工具栏
  （"最近剪切板"经 facade 调取一键粘贴 + 切回键）。
- 🟢/🔵 **隐私（L0–L4）**：普通键入默认不存、不传、不学；输入普通文本后 Room/同步/日志**无按键流痕迹**
  （host-JVM 决策逻辑单测 🟢 + 真机痕迹核查 🔵）。
- 🟢 文档：ADR-0011（input-context-privacy）+ KEYBOARD_PRIVACY.md + CONTRACTS_KEYBOARD.md 落地。

## v2.1 门禁（底座 Spike → ADR-0010）— 未开工（需 build PoC）

- 🟡 **build PoC**：在一个最小 Android 工程中跑通 librime（BSD）拼音候选产出（paper spike 已有，PR5）。
- 🟢 文档：ADR-0010 终裁——引擎=librime；长期框架 (A) 自建 librime 前端 / (B) fcitx5 插件二选一，
  附 license/build/integration 评分表（评分依据落在 repo，不口头）。
- 🟢 `InputEngineAdapter`(RimeAdapter) 目标接口签名冻结在 CONTRACTS_KEYBOARD.md（仅接口，不绑实现）。
- ⚖️ **A/B 选择尚未裁定**：本门禁只要求"做完 PoC 并产出 ADR-0010"，不预判结论。

## v2.2 门禁（CandidateMixer）— 未开工

- 🟢 排序公式可验（共享评分向量，两端一致）：
  `final = engine_score + prefix + recency + frequency + pinned_boost + app_context_boost
  + remote_freshness + explicit_saved_boost − secret_risk_penalty − sensitive_field_penalty`。
- 🟢 **pinned 硬置顶**（沿用 SUG-1.1）；**Secret 不进候选**；**密码框（sensitive field）不展示 ClipVault 候选**
  ——三条均有断言用例（既有 PrivacyAwareFilter / 来源上限逻辑复用，不另造）。
- 🟢 ClipVault 内容（剪切板/词库/Prompt/命令/路径）与引擎候选混排，确定性、可解释、权重可配。

## v2.3 门禁（本地学习）— 未开工（开工时细化）

- 🟢 只存**可解释统计事件**（词频/短语/Prompt/命令/场景/最近），**绝不存普通键入正文**
  （schema 审查 + 单测断言无正文字段）。
- 🟢 学习全本地、可关闭、可清除；不外传、不上云（G1/G2 复验）。
- ⏳ 具体事件 schema 与权重在该阶段开工时随 slice 冻结。

## v2.4 门禁（Cloud Relay POC）— 未开工（开工时细化，需威胁模型先行）

- 🟢 **端到端加密**：云只中继密文，服务端无法还原明文（密钥不出设备，G1 复验；加密/解密单测）。
- 🟢 云中继为**可选、默认关**；关闭时所有核心功能照常（本地优先）。
- 🟢 文档：ADR-0012（cloud-relay-threat-model）先于实现落地。
- ⏳ 协议细节随该阶段 slice 冻结。

## v3.0 门禁（智能输入）— 未开工（开工时细化）

- 🔵/🟢 纠错/长句补全/Prompt 改写/语音/云 AI：**全部可关、默认关、显式触发**；
  关闭时退回 v2.x 确定性行为（回归测试）。
- 🟢 AI 调用不携带密钥、不静默上传普通键入正文（G1/G2 复验）。
- ⏳ 能力清单与触发契约随该阶段 slice 冻结。

## 范围刹车（主线明确暂不做，违反即范围外）

商业 SaaS、多用户账号、支付、插件市场、皮肤商店、云端明文索引、云端知识库、
自动上传普通键入、自动保存所有上屏文本、多人协同编辑、CRDT 笔记编辑器。
（与 ROADMAP_V2_KEYBOARD「范围刹车」一致；G5 范围门禁对主线同样适用。）
