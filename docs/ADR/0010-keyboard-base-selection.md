# ADR-0010: 中文输入底座选型 — librime 为引擎，长期底座二选一

状态：Accepted（2026-06-20）。依据 V2-S003 paper spike（primary-source 调研）。

## 背景

Full Keyboard Lab（PR4）已有可用英文键盘 + ClipVault 工具栏，但无中文。范围刹车规定
ClipVault **不从零做拼音引擎**，要接成熟开源引擎。需裁决：引擎用哪个、长期键盘底座怎么接，
且不能让外部 License 把 ClipVault 整体锁死。

## 调研事实（2026-06-20，见 V2-S003 Sources）

| 项目 | License | 角色 | 维护 |
|---|---|---|---|
| **librime** | **BSD-3** | 模块化可嵌入 C++ 引擎，音码/形码/繁简 | v1.17.0（2026-06），活跃 |
| **Trime** | **GPL-3.0** | librime via JNI 的 Android Rime 前端 | v3.3.10（2026-05），4.4k★ |
| **fcitx5-android** | **LGPL-2.1** | 输入法框架，**插件系统可加载其他 APK 的 addon**，RIME 插件、可扩展候选视图、剪贴板 | v0.1.2（2025-11），活跃 |

## 决策

1. **引擎层 = librime（BSD-3）。** 无争议：成熟、可嵌入、覆盖中文音/形码与繁简，BSD 不传染
   ClipVault 自有代码。ClipVault 的中文候选来自 librime。

2. **Trime 不作长期 fork 底座，仅作 spike 参考。** 理由：
   - GPL-3.0：把 Trime fork 进 ClipVault 同一 APK 会**传染整个 ClipVault 为 GPL-3.0**（当前 ClipVault
     源码公开但未声明 GPL，不接受被动 GPL 化）。
   - UI 定制（候选栏/工具栏混入 ClipVault 候选）按其文档需 fork。
   - 价值在于：它是"librime + Rime 数据 + JNI 在 Android 跑起来"的最佳活参考。**读它学接法可以**
     （学习/研究），**不把其 GPL 代码拷进 ClipVault**。

3. **长期键盘底座 = 二选一，待 build PoC 终裁：**
   - **(A) 自建 librime 前端（推荐）**：librime 经 JNI 嵌进 `ClipVaultFullKeyboardService`。
     - 优：最契合 Runtime/CandidateMixer（Rime 候选 + ClipVault 候选**同一管线排序**，PRODUCT_SPEC P7/CandidateMixer）；
       BSD 全程友好；ClipVault 全控 UI 与许可；单一 APK。
     - 劣：成本最高（为 Android NDK 编 librime + 打包 Rime 数据/方案 + 写 JNI 桥）。
   - **(B) fcitx5-android 插件（务实回退）**：ClipVault 作为独立 APK 插件，给 fcitx5 提供候选/工具栏。
     - 优：LGPL 分发友好；借力活跃维护的框架；成本更低。
     - 劣：候选混合发生在 fcitx5 管线内、ClipVault 不全控；用户需装两个 App（fcitx5 + ClipVault 插件）。

4. **HeliBoard（GPL）** 仅作键盘布局/手感/隐私参考，不取代引擎。

## 后果与下一步

- v2.1 下一子片 = **build PoC**（需真机/NDK）：在仓库内试编最小 librime-for-Android，经 JNI 喂一次
  拼音取候选，量出 (A) 的真实构建成本。PoC 通过则定 (A)，否则回退 (B)。
- License 红线：在 build PoC 与最终接入前，**不向 ClipVault 主 APK 合入任何 GPL 代码**；保持 ClipVault
  自有许可的可选性。
- 不变量延续：无论 (A)/(B)，主输入法处理普通键入仍遵守 L0–L4（ADR-0008），普通键入不持久化；
  Secret/密码框不出候选（CandidateMixer 的 PrivacyAwareFilter，v2.2）。

## 关联
[[0008-v1-as-runtime]]（P7 允许主输入法；CandidateMixer 是其落点）、
[[0007-deterministic-suggestions-v1]]（ClipVault 候选的本地确定性评分进 CandidateMixer）。
