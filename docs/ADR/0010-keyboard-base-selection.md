# ADR-0010: 中文输入底座选型 — librime 为引擎，长期底座二选一

状态：Accepted（2026-06-20，engine=librime）；A/B 长期底座终裁仍待 build PoC。
2026-07-02 工具链/许可/验证增补见 [V2-S004](../SLICES/V2-S004-librime-build-poc.md) 与
[build PoC 去重调研](../RESEARCH_V2_1_BUILD_POC_2026_07_02.md)。

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

- v2.1 下一子片 = **V2-S004 build PoC**（需 16KB emulator/device + NDK）：分别验证 (A) 最小
  librime JNI 与 (B) 外部 fcitx5 addon 候选注入，两边证据齐全后按冻结算法终裁；A 通过全部硬门和
  工程预算则选 A，A 失败而 B 全部通过才选 B，两边都失败则保持未裁定/阻塞。
- Build PoC 固定 **NDK r28** 并验证所有传递 `.so`/APK 的 16KB page-size 对齐；输出逐 ABI 构建元数据。
- “librime=BSD-3”不等于“中文数据整包 BSD”：schema/dictionary 分别钉 SHA、license、NOTICE，
  未许可内容不复制，浮动 `master` 不进入构建。
- (B) 回退需先证明独立 fcitx5 addon 能注入候选；现有插件以 native addon 为主，不能预设 Kotlin facade
  有现成候选提供器 API。
- License 红线覆盖 A/B spike APK、addon、全部传递依赖和 Rime 数据：逐项确认 license/NOTICE/源码或
  relink 等交付义务；清单未经 reviewer 批准前不上传二进制 artifact，不向 production APK 合入。
- 不变量延续：无论 (A)/(B)，主输入法处理普通键入仍遵守 L0–L4（ADR-0008），普通键入不持久化；
  Secret/密码框不出候选（CandidateMixer 的 PrivacyAwareFilter，v2.2）。

## 关联
[[0008-v1-as-runtime]]（P7 允许主输入法；CandidateMixer 是其落点）、
[[0007-deterministic-suggestions-v1]]（ClipVault 候选的本地确定性评分进 CandidateMixer）。
