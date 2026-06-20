# V2-S003 — 输入法底座 Spike 计划（PR5 docs）

> 状态：计划（ROADMAP_V2 v2.1）。本片是**调研/裁决**，不写产品代码。
> 目标：在 Full Keyboard Lab（PR4，已落地的英文键盘 + ClipVault 工具栏）之上，
> 裁决中文输入引擎/底座的长期方案，输出 **ADR-0010**。

## 0. 背景与边界

- ClipVault 不从零实现拼音/中文引擎（范围刹车）。中文输入能力**接入**成熟开源引擎。
- Full Keyboard Lab 现状：纯英文 + 符号 + ClipVault 候选/工具栏（commitText / InputConnection）。
- 要回答的问题：中文候选从哪来？工具栏/候选栏怎样把"引擎候选 + ClipVault 候选"混合（CandidateMixer，PR6/v2.2）？

## 1. 候选底座与角色（待 spike 验证）

| 候选 | 角色 | License | 初判 |
|---|---|---|---|
| **Rime / librime** | 中文输入引擎（音码/形码、YAML 方案、繁简） | BSD-3 | 核心引擎，必接 |
| **Fcitx5 Android** | 长期主输入法框架（候选栏/工具栏/插件/剪贴板/RIME 插件） | LGPL-2.1（分发更友好） | 长期优先底座 |
| **Trime（同文）** | Android 上的 Rime IME（JNI），音形码通用 | **GPL-3.0** | 最快验证 spike；长期 fork 受 GPL 约束 |
| **HeliBoard** | AOSP/OpenBoard 派生，隐私键盘（无联网权限） | GPL-3.0 | 仅借鉴 UI/布局/手感，不作中文引擎 |
| **Espanso** | 文本扩展模型（trigger / app-specific config） | GPL-3.0 | 借鉴 Prompt/Command 触发器思路 |
| **CopyQ** | 高级剪切板动作模型 | GPL-3.0 | 借鉴"剪切板项可触发动作" |

## 2. Spike A — Trime（最快验证可行性）

清单（只验证，不长期承诺）：

- [ ] 拉取 + 构建 Trime（Gradle / NDK / librime 子模块）成本与耗时
- [ ] 找到候选栏（Candidate view）的渲染与点击注入点
- [ ] 找到工具栏/快捷条插入点（能否塞 ClipVault 工具栏按钮）
- [ ] 评估"把 ClipVault 候选混入 Rime 候选列表"的难度（同一候选流 vs 叠加视图）
- [ ] **GPL-3.0 对 ClipVault 分发的影响**：若 fork Trime，ClipVault 是否被传染为 GPL；
      能否以"独立进程/独立 APK + IPC"规避（需法律判断，先记风险）
- 产出：可行性结论 + 截图 + 接入点清单

## 3. Spike B — Fcitx5 Android（长期优先）

清单：

- [ ] 构建成本（CMake/NDK、fcitx5 + 插件）
- [ ] 插件系统：ClipVault 能否作为**插件/候选提供器**接入，而不 fork 主体
- [ ] Candidate view 接入点（混入 ClipVault 候选）
- [ ] 内置 clipboard manager 接入点（与 ClipVault Runtime 的关系，避免重复）
- [ ] RIME plugin 接入方式（Fcitx5 + librime）
- [ ] **LGPL-2.1 分发友好度**：动态链接/插件边界下 ClipVault 自有代码可否保持私有
- 产出：长期底座可行性 + 接入架构草图

## 3.5 RimeAdapter 目标接口（无论选谁，ClipVault 侧抽象）

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
ClipVault 的 CandidateMixer（v2.2）消费 `EngineCandidate` + ClipVault 候选，统一排序。

## 4. 评分表（spike 后填）

| 维度（权重） | Trime | Fcitx5 Android |
|---|---|---|
| 构建/上手成本（低=好） | | |
| 候选/工具栏接入难度（低=好） | | |
| ClipVault 候选混入难度（低=好） | | |
| 插件化（不 fork 主体，高=好） | | |
| License 分发友好（高=好） | | |
| 维护活跃度（高=好） | | |
| **加权总分** | | |

## 5. 预期裁决（待 spike 数据确认，写入 ADR-0010）

- **Trime**：最快验证"Android + Rime + ClipVault 工具栏/候选"可行性的 spike 载体。
- **Fcitx5 Android**：长期优先底座（插件化 + LGPL 分发更友好）。
- **Rime / librime**：核心中文引擎。
- **HeliBoard**：UI/隐私/手感参考。

## 6. 不做（本片范围刹车）
- 不在本片接任何引擎进产品；不 fork；不发布带 GPL 引擎的合并 APK 直到 License 判断完成。
- 不做完整中文输入法竞争；ClipVault 的差异点是 **Runtime（剪切板/词库/Prompt/命令 + 跨端 + Obsidian/GitHub）**，引擎只是中文输入的一块拼图。
