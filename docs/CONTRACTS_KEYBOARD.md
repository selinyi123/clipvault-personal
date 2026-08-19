# ClipVault Personal — 键盘合同（CONTRACTS_KEYBOARD）

> 状态：v2.0 阶段冻结（接口与不变式）。v2.1 新接入使用
> [CONTRACTS_INPUT_ENGINE_V2](CONTRACTS_INPUT_ENGINE_V2.md)；本文保留既有行为与兼容事实；
> 上位路线见 [ROADMAP_V2_KEYBOARD.md](ROADMAP_V2_KEYBOARD.md)，决策依据见
> [ADR-0008](ADR/0008-v1-as-runtime.md)（Runtime + L0–L4）与 [ADR-0010](ADR/0010-keyboard-base-selection.md)（引擎=librime）。
> 隐私层细则在 [KEYBOARD_PRIVACY.md](KEYBOARD_PRIVACY.md)，验收门禁在 [GATES.md](GATES.md)「Keyboard 主线门禁」。
>
> **本文只定义接口与不变式，不写实现。** 标 🔒 = 本阶段冻结（改动需新 ADR）；标 ⏳ = 占位，
> 对应阶段开工时随 slice 细化（不在此盲想）。全局门禁 G1–G8 对所有键盘 PR 适用。
>
> 既有契约不重写，只复用并引用：[CONTRACTS.md](CONTRACTS.md) 的 SUG-1（评分基线）、SG-1（Secret Guard）、
> DB-1（事实源）、API-1，以及 `ClipVaultFacade`（v1.1，Runtime 访问门面）。

## KBD-1 — 输入引擎适配器（v2.0 Lab compatibility）🔒

中文输入能力**接入** librime（ADR-0010），不自研拼音。ClipVault 侧以下抽象冻结，
底座 (A) 自建 librime 前端 / (B) fcitx5 插件二选一不影响本接口（取自 V2-S003 §3.5）：

```kotlin
interface InputEngineAdapter {
    fun reset()
    fun onKey(key: String): EngineState         // 喂按键，返回 composing + 候选
    fun selectCandidate(index: Int): String     // 选词，返回上屏文本
    fun candidates(): List<EngineCandidate>
}
data class EngineCandidate(val text: String, val comment: String?)
data class EngineState(val composing: String, val candidates: List<EngineCandidate>)
```

该接口只保留为 v2.0 Lab 和最小 JNI bring-up 兼容面。它的 `selectCandidate(index)` 不能安全
跨异步刷新、分页或进程重启；Android/Windows 正式适配器必须使用 ADR-0014 与 Engine Protocol V2
的 session、revision 和稳定 `candidate_id`，不得偷偷扩展本接口制造第二套非版本化协议。

不变式：
- 适配器**纯本地**：`onKey`/`candidates` 不发网络（G2）。引擎数据（Rime 方案/词典）本地加载。
- 适配器**无副作用入库**：喂键与取候选**不**向 Runtime 写历史；只有 KBD-5 的显式上屏+显式保存才写。
- `comment` 为引擎注释（拼音/编码提示），可空；ClipVault 不依赖其格式。

## KBD-2 — CandidateMixer 排序（v2.0 原目标；v2.2 UI 由 ADR-0014 修订）🔒

以下公式继续冻结为 ClipVault 自有候选的确定性排序基线：

```text
final = engine_score + prefix + recency + frequency + pinned_boost
      + app_context_boost + remote_freshness + explicit_saved_boost
      - secret_risk_penalty - sensitive_field_penalty
```

ADR-0014 已修订“立即进入同一候选栏”的 UI 假设。v2.2 首版分开承载系统 Inline Autofill、
Rime 引擎候选和 ClipVault 工具栏；不得把三个来源塞进同一数组下标命名空间。跨来源统一混排
需要新的证据与决策，不是 v2.2 的完成条件。

不变式（沿用并扩展 SUG-1 / SUG-1.1）：
- **确定性、可解释**：同一输入 + 同一本地状态 → 同一排序。无 ML、无在线请求。v2.0 单包兼容面
  仅经 `ClipVaultFacade` 查询本地 Room；ADR-0013 的双包目标中，IME 不打开数据库，只读取
  Companion Runtime 通过签名级 Binder 提供的有界、已过滤本地快照（SUG-1）。
- **pinned 硬置顶**：沿用 SUG-1.1，pinned 项不得被任何高频项越过（排序键 pinned 优先于 score）。
- **Secret 不进候选**：`secret_risk_penalty` 不是"降权"而是**硬剔除**——SG-1 命中项永不出现在候选栏（见 KBD-4）。
- **密码框不展示 ClipVault 候选**：`sensitive_field_penalty` 对敏感输入域为硬剔除（见 KEYBOARD_PRIVACY.md）。
- 各项权重可在 config 覆盖（CFG-1 风格），默认值随 v2.2 slice 冻结。⏳ 具体默认权重在 v2.2 开工时定。

## KBD-3 — ClipVault 候选来源与类别 🔒

ClipVault 候选只来自 Companion Runtime 拥有的本地事实源（Room/SQLite，DB-1）。v2.0 单包兼容面经
`ClipVaultFacade` 读取；ADR-0013 双包目标经签名级 Binder/有界本地快照读取，IME 包本身不打开
Room/SQLite。类别取自 PRODUCT_SPEC §5.3 面板：

| 来源 | 内容 | 取数门面 |
|---|---|---|
| recent_clip | 最近剪切板（非密钥、非删除） | `listRecentClips` |
| memory:term/phrase/prompt/command/key_info/path | 6 类 Personal Memory | `listMemory` |
| synced | 桌面同步来的内容（apply-only 缓存） | `listRecentClips`/`listMemory` |

不变式：
- 候选**只读本地**：IME 内绝不为按键发网络请求（SUG-1，G2）。同步是后台事件日志（SYNC-2），不在按键路径上。
- 候选集与 SUG-1 一致性：复用既有 origin source-cap（来源上限）逻辑，不另造排序通道。

### KBD-3A — 双包候选快照桥（v2.1+ 目标）

- Binder 服务只由 Companion Runtime 导出，并由 ClipVault 签名级权限、调用方 UID/签名和协议版本共同校验。
- Companion Runtime 在后台生成有界、已过 SG-1/隐私过滤的不可变候选 DTO 快照；IME 在本地按当前前缀过滤/排序。
  逐键路径不得同步调用 Binder、Room、SQLite、Python、HTTP 或网络，也不得把按键流/完整正文发送给 Runtime。
- 快照只含稳定 opaque ID、允许显示/上屏的 text/label、固定 kind、排序所需数值和单调 revision/expiry；
  不含数据库句柄、同步密钥、完整来源记录、短信正文、OTP 或任何被硬剔除内容。
- 请求数、响应项、单项长度、总字节和处理时间必须有界。Binder 超时、死亡、身份/版本不符或畸形响应时，
  IME 清空 ClipVault 快照并继续基本/Rime 输入；不得回退为直接打开数据库。
- 密码/incognito/敏感 App 会话不订阅快照并立即擦除现有 ClipVault 候选。普通会话结束、Runtime 撤销或
  snapshot expiry 同样清除内存副本；IME 不持久化快照内容。

## KBD-4 — 候选隐私闸门（引用 SG-1 + KEYBOARD_PRIVACY）🔒

候选进入候选栏前，必须通过：
1. **Secret 闸门**：持久化 `is_secret` 只代表写入时规则，不构成候选出口授权。Runtime 在 clip
   候选出口必须用**当前 SG-1 规则**复扫正文；持久化已隔离或当前规则命中的项都要**硬剔除**，
   不进候选、不脱敏展示为可选项。该读路径不得隐式改写 Room。Desktop Owner release 不是 sync wire
   授权，Android 接收端仍可重新隔离；恢复 Android 候选资格只能走未来定义的本地显式 Owner 流程。
   Room 先一次读取最多 128 行无正文 metadata，再把合格 ID 按最多 4 个一批物化；只物化不超过
   64 Ki UTF-8 bytes 且 64 Ki UTF-16 code units 的候选。当前规则累计复扫最多 512 Ki code units、返回批次累计正文
   最多 256 Ki code units。统一排序最多看到 100 个已授权 clip，调用者 `limit` 只能在排序和稳定
   tie-break 完成后截断；Recent Clips 列表可以直接按自身 `limit` 收束。这些都是 fail-closed 的运行时预算，
   超限内容仍可留在主 App，但不得为补足 IME 候选而无界扫描或累计大正文。
   Personal Memory 使用独立但同样有界的两段式出口：Room 一次读取最多 128 行无 `text`/`label` 的
   `_rowid_` 与 UTF-8 byte-length metadata，再按最多 4 行一批物化；`text` 上限 64 KiB UTF-8 bytes，
   `label` 上限 4 KiB UTF-8 bytes，且先以对应 UTF-16 code-unit 上限短路以避免为异常超长旧数据分配
   巨大编码缓冲。水合后必须重新检查 deleted/kind/query/size 与当前 SG-1；单次出口累计 SG 扫描 payload
   （text + label）最多 512 KiB、保留 payload 最多 256 KiB、最多 100 个已授权 memory。`listCandidates`
   与 `listMemory` 必须消费同一个不可伪造的已授权批次；不得为补足结果继续翻页或无界重扫。
   候选投影不得物化不参与显示/排序的 `source`；kind 仅允许 KBD-3 的六种固定值，其他值在 metadata SQL、
   hydration SQL 与 Runtime 复检三层 fail-closed。query 仅在固定 metadata 窗口之后按 Kotlin
   `contains(ignoreCase=true)` 匹配正文或生成标签 `[memory:${kind}]`，不得把存储 label 扩展为新搜索面。
2. **敏感输入域闸门**：当前 `EditorInfo` 为密码/敏感域，或处于 incognito（`IME_FLAG_NO_PERSONALIZED_LEARNING`）时，
   **不展示任何 ClipVault 候选**，且**不产生学习事件**（细则 KEYBOARD_PRIVACY.md）。
3. **敏感 App 闸门**：⏳ 敏感 App 名单与匹配规则在 v2.2-L 学习阶段开工时随 slice 冻结。

可本地验证点（host-JVM，🟢）：给定密码 `EditorInfo` → KBD-2 输出**无** ClipVault 候选；给定 SG-1 命中项 → 不进候选。

## KBD-5 — 上屏与显式保存（commit / save）🔒

- **上屏**：选词或粘贴候选经 `InputConnection.commitText` 写入目标输入框（L2，见 KEYBOARD_PRIVACY）。
- **显式保存**：仅当用户**显式**点击"保存当前剪切板/保存为 memory"时，才经 `ClipVaultFacade.saveExplicit` 进 Runtime（L3）；
  之后才过 SG-1 决定能否同步/备份（L4）。**键入/上屏本身不自动入库**（ADR-0008 P4：显式保存才成资产）。
- 不变式：不存在"自动保存所有上屏文本"的路径（范围刹车）。

## 跨平台与验证

| 契约 | 本地可验（🟢 host-JVM / 桌面） | 需设备/CI（🟡🔵） |
|---|---|---|
| KBD-2 排序确定性 | 共享评分向量（两端一致，沿用 VEC-1 思路） | — |
| KBD-4 隐私闸门决策逻辑 | host-JVM 单测（密码域→无候选；Secret→剔除） | 真机域识别 🔵 |
| KBD-1 引擎接入 | — | build PoC（NDK/JNI）🟡 |
| KBD-5 commit/save 行为 | 决策逻辑单测 🟢 | 真机上屏 🔵 |
| ENG2 session/revision/stable ID | 共享 golden vectors 🟢 | Android Binder/JNI、Windows pipe/TSF 🟡🔵 |

## 不做（本文范围刹车）
- 不在本文绑定 (A)/(B) 底座选择（待 build PoC）；不定学习事件 schema；Engine V2 以独立合同演进。
- 不引入云、不引入"自动上传普通键入/自动保存上屏文本"（与 ROADMAP/ADR-0008 范围刹车一致）。
