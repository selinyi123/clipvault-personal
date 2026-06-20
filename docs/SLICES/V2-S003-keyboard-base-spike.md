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

## 4. 评分表（2026-06-20 调研填表，primary-source）

数据来源：各项目 GitHub 主页（README/LICENSE/releases），见文末 Sources。

| 维度 | Trime | Fcitx5-Android | 自建 librime 前端 |
|---|---|---|---|
| License | **GPL-3.0**（传染） | **LGPL-2.1**（插件边界友好） | **librime=BSD-3**（最友好） |
| 架构 | Kotlin 94% + JNI(librime) | Kotlin 88% + C++/NDK | ClipVault 自己 + librime JNI |
| 候选/工具栏接入 | 需 fork 改 UI（文档未提扩展点） | **插件系统**：可加载其他 APK 的 addon；可扩展候选视图 | 完全自控（自己渲染 CandidateBar） |
| ClipVault 候选混入 | 难（fork 改候选渲染） | 中（作为 addon/候选提供器进 fcitx5 候选流） | **最易**（同一候选管线，Rime+ClipVault 一起排序） |
| 插件化（不 fork 主体） | 否（要 fork） | **是**（独立 APK 插件） | N/A（本就自有） |
| 维护活跃 | 很活跃 v3.3.10(2026-05)，4.4k★ | 活跃 0.1.2(2025-11)，1953 commits | librime v1.17.0(2026-06)，活跃 |
| 上手/构建成本 | 低（拉来就能跑，验证最快） | 中（NDK + 插件机制） | **高**（自己编 librime for Android + 配 Rime 数据 + JNI） |
| 对 Runtime 愿景契合 | 低（GPL 锁死、UI 不可控） | 中（候选进 fcitx5 管线，ClipVault 不全控） | **高**（CandidateMixer 原生混合、自有许可、全控） |

**关键结论**：
- **引擎层已定 = librime（BSD-3）**，无争议：成熟、可嵌入、覆盖音码/形码/繁简、活跃。
- **Trime 不作长期底座**：GPL-3.0 会把 ClipVault 整体传染为 GPL；且 UI 定制要 fork。
  → 只作 **spike 参考**（读它学"librime 在 Android 上的 JNI/构建/Rime 数据"接法；读 GPL 代码学习可以，不把其代码拷进 ClipVault）。
- **长期二选一**（待 build PoC 定）：
  - **(A) 自建 librime 前端**：把 librime 经 JNI 嵌进 `ClipVaultFullKeyboardService`。最契合 Runtime/CandidateMixer
    （引擎候选 + ClipVault 候选同一管线排序）、BSD 最友好、全控；**成本最高**（NDK 编译 + Rime 数据）。
  - **(B) Fcitx5-Android 插件**：ClipVault 作为独立 APK 插件给 fcitx5 提供候选/工具栏。LGPL 分发友好、
    借力活跃框架、成本更低；但候选混合在 fcitx5 管线里、ClipVault 不全控，且用户要装两个 App。

## 5. 裁决（已写入 ADR-0010，2026-06-20）

- **引擎 = librime（BSD-3）**：定了。
- **Trime = spike 参考**（学 librime-on-Android 接法），不作 fork 底座（GPL-3.0 传染）。
- **长期底座 = (A) 自建 librime 前端 [推荐，待 build PoC 确认成本] 或 (B) fcitx5-android 插件 [LGPL 务实回退]**。
- **HeliBoard**：键盘布局/手感/隐私参考（GPL，不取代引擎）。
- **下一步具体 spike（需真机/NDK）**：build PoC —— 在 ClipVault 仓库内试编一个最小 librime-for-Android
  并经 JNI 喂一次拼音、取候选，量出 (A) 的真实构建成本，再终裁 (A)/(B)。本片为 paper spike（架构+许可+维护分析），
  build PoC 是 v2.1 的下一子片。

## Sources
- fcitx5-android（LGPL-2.1，插件系统/RIME 插件/候选视图/剪贴板，v0.1.2 2025-11）：https://github.com/fcitx5-android/fcitx5-android
- Trime（GPL-3.0，librime via JNI，v3.3.10 2026-05，4.4k★）：https://github.com/osfans/trime
- librime（BSD-3，模块化可嵌入 C++ 引擎，v1.17.0 2026-06）：https://github.com/rime/librime

## 6. 不做（本片范围刹车）
- 不在本片接任何引擎进产品；不 fork；不发布带 GPL 引擎的合并 APK 直到 License 判断完成。
- 不做完整中文输入法竞争；ClipVault 的差异点是 **Runtime（剪切板/词库/Prompt/命令 + 跨端 + Obsidian/GitHub）**，引擎只是中文输入的一块拼图。
