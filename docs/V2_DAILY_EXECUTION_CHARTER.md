# ClipVault v2 Daily Execution Charter

状态：Active（Owner 于 2026-08-09 授权多智能体、多分支并行开发）

本文是当前 v2 日用候选的执行索引。它不替代 ADR、合同、稳定性计划或
Owner 发布门禁；发生冲突时，依次以已接受 ADR、`CONTRACTS*.md`、
`V2_DAILY_USE_ACCEPTANCE.md`、稳定性计划和本章程为准。

## 1. 唯一主目标

把 ClipVault 推进为仅内部使用、可以持续日常输入的跨端个人输入中枢：

```text
Android 本地中文主输入法
+ Windows 原生 TSF 中文输入法
+ librime 共享输入语义与黄金向量
+ Personal Memory / 显式保存的跨端剪贴板
+ 独立、短时、一次性、端到端加密的 OTP Relay
```

“v2 Daily Candidate”是跨历史 v2.0-v2.5 能力的集成候选名称，不自动等于
某个稳定版本号。最终公开版本号、稳定声明和发布由对应 release gate 与
Owner 决定；不得把规划标签、源码存在、编译成功或合成测试当成日用证据。

## 2. 不可漂移边界

以下边界优先于新功能、进度和代码复用便利性：

1. 普通按键、组字和完整上屏正文默认不持久化、不记录、不上传。
2. 普通内容只有经过用户显式保存，才成为 ClipVault 资产。
3. Android IME 不申请网络、短信权限，不直接打开 Runtime 数据库。
4. Windows TSF DLL 不加载 librime、Python、数据库、网络或配对密钥。
5. IME 逐键路径只访问进程内状态或有界本地 IPC，不等待网络和 Python。
6. OTP 不进入剪贴板、普通同步事件、数据库、搜索、备份、日志正文或离线 outbox。
7. OTP 默认显式点击或预先武装后定向填入；不向任意当前焦点盲目注入，不自动提交表单。
8. 密码框、无痕模式、未知敏感上下文和已撤销设备一律 fail closed。
9. Android/Windows Runtime、网络或数据库故障时，基本本地输入仍须可用。
10. 不新增 typed-text 学习、行为画像、分析 SDK、默认云 AI 或云端明文索引。

任何任务若需要改变以上一项，必须停止实现，先提交独立 ADR 与隐私/迁移方案，
并取得 Owner 对该架构变化的明确批准。

## 3. 五条工作线与所有权

| 工作线 | 分支 / worktree | 允许范围 | 当前完成定义 |
|---|---|---|---|
| Foundation | `codex/v2-next-foundation` | ADR、合同、黄金向量、许可、威胁模型、门禁 | 每个实现任务都能映射到冻结合同和可判定门禁 |
| Android | `codex/v2-android-ime` | 独立无网络 IME、librime、候选面、Runtime 有界桥接 | 真机可连续完成中文/英文/数字/符号输入，Runtime 失效不阻断基本输入 |
| Windows | `codex/v2-windows-ime` | TSF DLL、外置 Host、librime、候选 UI、安装/恢复 | x64 真实 TSF 中文闭环通过，Host 崩溃不带崩宿主，安装升级可恢复 |
| OTP/Backend | `codex/v2-otp-relay` | Android 授权捕获、Desktop ingress/pairing、Windows Broker、E2EE 临时通道 | 配对、撤销、TTL、防重放、单次消费和定向填入均有自动与人工证据 |
| Integration | `codex/v2-daily-integration` | 已审查工作线合流、候选构建、CI、Owner QA 交接 | 同一提交通过自动门禁，并生成可追溯内部候选与未完成证据清单 |

路径所有权以工作线为准。并行 Agent 不得同时编辑同一文件；发现跨线修改需求时，
先把接口/合同变更交给 Foundation，再由各线分别实现。共享 Git index、合并、提交、
推送和 CI 调度只由主协调 Agent 执行，避免并发 Git 写入和重复构建。

## 4. 每项任务的漂移检查

开始实现前必须依次回答：

1. 它关闭 `V2_DAILY_USE_ACCEPTANCE.md` 的哪一个验收缺口？
2. 它属于哪一条工作线，是否触碰其他工作线的路径所有权？
3. 它是否受现有 ADR/合同覆盖？若没有，是否真的需要新 ADR？
4. 它会不会把网络、持久化、秘密或复杂运行时带入 IME 关键路径？
5. 最小充分证据是什么：静态、单元、构建、系统集成、真机还是 Owner 人工？

无法明确映射到验收缺口的功能默认不做；只能改善美观、扩展性或假想未来需求的
改动进入 backlog，不得抢占 P0/P1 日用闭环。

## 5. 优先级与行动规范

### P0 — 立即处理

- 可能泄露普通键入、OTP、永久凭据或配对秘密；
- 可能在密码/无痕/撤销后继续候选、发送或填入；
- 可能导致宿主应用崩溃、不可恢复安装或数据损坏；
- 会绕过签名、身份、重放、TTL 或单次消费门禁。

### P1 — 当前工作线完成前处理

- Android/Windows 基本输入闭环失败或 Runtime 失效时无法降级；
- librime 初始化、部署、升级、重启存在稳定复现的泄漏或陈旧状态；
- 配对/撤销/凭据提供器的瞬时错误被误判为永久无效；
- 安装、升级、卸载、Host/Broker 恢复路径不确定；
- 已消费 OTP 发生无法观测的插入失败。

### P2 — 记录后排期

- 不影响安全或基本日用的可用性、性能和维护性问题；
- HTTPS/pinning、公共 Relay、ARM64、统一候选混排等后续硬化；
- UI 美化、语音、AI、手写、复杂主题和插件生态。

每次开发只处理一个明确问题：先静态复审调用链，再做最小补丁，再增加能防止回归的
最小测试。禁止借 P0/P1 修复进行全模块重构、依赖升级或接口重写。

## 6. 分支与合流协议

1. 工作线从已接受的 integration 基线同步，先确认 worktree 干净和目标 commit。
2. 一个提交只包含一条工作线的一个关注点；不得 stage 其他 Agent 或用户的改动。
3. Patch Agent 不自我批准。另一 Agent 或主协调 Agent按安全、隐私、失效降级和测试有效性复审。
4. 合流前先更新/检查相关合同向量；公共协议不得由单端实现偷偷扩展。
5. 合流顺序默认是 Foundation → OTP/Backend → Android → Windows → Integration。
6. 集成分支只接收可回滚提交；冲突必须根据合同重做，不用覆盖式解决隐藏差异。
7. 只有 integration 同一 SHA 的自动证据完成后才触发 `v2-daily-candidate` CI。
8. main、标签、Release、签名产物和公开稳定声明仍受独立 Owner gate 控制。

当前 integration worktree 已有跨线未提交改动。在这些改动完成审查、定向验证并形成
可追溯提交前，不重置、不搬运到各工作线，也不从旧工作线反向覆盖 integration。

## 7. 验证梯度与证据口径

每层只在上一层满足后进入下一层，避免并行重复启动重型任务：

```text
静态边界/格式检查
→ 最相关单元或合同测试
→ 受影响模块构建
→ Android/Windows 系统集成
→ 安装、升级、卸载与崩溃恢复
→ Owner 真机连续日用
→ 同一 SHA 的 CI 候选证据
```

证据必须记录命令、目标 SHA、产物和原始结果。以下表述禁止使用：

- “源码存在”代替系统注册/真实输入；
- “测试写了”代替测试实际运行；
- “unsigned candidate”代替签名/最终产物；
- “部分人工项通过”代替完整日用或稳定门禁；
- “应用层加密”代替 HTTPS/pinning 或公共传输硬化已经完成。

## 8. 当前执行顺序

1. 完成 integration 未提交代码的分线复审，先清除 P0/P1。
2. 冻结并提交当前后端、Android、Windows 的最小一致状态。
3. 运行分线定向测试与构建，不重复跑等价全量命令。
4. 复测 Windows x64 librime TSF 中文闭环及安装/升级/卸载恢复。
5. 复测 Android 独立 IME 中文输入、Runtime 失效和敏感上下文。
6. 复测 OTP 在线中继、撤销、过期、重复包与 TSF 定向填入。
7. 生成并推送同一 SHA 的内部候选，触发 `v2-daily-candidate` CI。
8. 由 Owner 按 `V2_DAILY_USE_MANUAL_QA.md` 连续日用并提交设备证据。

完成以上步骤前，继续使用“内部 v2 Daily Candidate / 尚未稳定”的口径。
