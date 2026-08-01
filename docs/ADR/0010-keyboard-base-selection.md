# ADR-0010: 中文输入底座选型 — ClipVault 自有 Android 壳 + librime

状态：Accepted（2026-08-01，A 路线已终裁）。

历史 PoC 与筛选依据见 [V2-S004](../SLICES/V2-S004-librime-build-poc.md) 和
[build PoC 去重调研](../RESEARCH_V2_1_BUILD_POC_2026_07_02.md)。生产输入由
`android/ime-app`、`android/rime-engine-android` 和仓库根 `shared-input/rime`
承载；`spikes/librime-android` 不再是生产事实来源。

## Subsequent decisions

ADR-0010 继续负责“中文引擎采用 librime、Android 使用项目自有最小壳”的裁决；以下后续 ADR
取代了早期 PoC 中与该引擎选择无关的集成假设：

- ADR-0013 supersedes the former single-package IME boundary: production uses a standalone,
  no-network IME APK and a separate networked Companion Runtime.
- ADR-0014 supersedes index-based candidate integration: Engine Protocol V2 uses sessions,
  revisions and stable candidate IDs, while Rime, Runtime and Inline Autofill remain separate surfaces.
- ADR-0016 supersedes clipboard-shaped OTP handling: plaintext capture belongs to the Companion
  Runtime and OTP uses a distinct ephemeral channel, never the IME package or ordinary clipboard flow.

这些修订不撤销 librime/A 路线本身。对应规范分别见
[ADR-0013](0013-cross-platform-input-process-boundary.md)、
[ADR-0014](0014-engine-protocol-v2-and-candidate-surfaces.md) 和
[ADR-0016](0016-otp-relay.md)。

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

3. **长期 Android 底座 = A 路线：ClipVault 自有最小 `InputMethodService` + 自有 JNI + librime。**
   - 独立无网络 IME APK 只承载键盘、Rime 会话、候选与签名级 Runtime Snapshot 客户端。
   - Runtime APK 继续承载 Room、同步、Personal Memory 与网络；两包由签名级 Binder 隔离。
   - Rime 与 ClipVault 候选保持两个独立 surface。第一阶段不修改 Rime 原生排序，也不让 Runtime
     故障进入中文按键关键路径。
   - normal/private schema、`default.yaml` 和标点只维护在 `shared-input/rime/`；Android Gradle
     直接从该 canonical 目录打包，Windows 可复用同一资产。外部词典按生产锁定 hash 注入。

4. **B 路线不进入生产依赖。** Fcitx5 Android、YuyanIme、Trime、HeliBoard 继续作为交互、兼容性、
   构建与测试参考。当前 APK 不包含它们的运行时或复制代码。构建所用 fcitx5-android prebuilt
   仓库只提供锁定的 yaml-cpp/LevelDB/OpenCC/marisa 静态输入，并由生产 lock 与 NOTICE 单独治理。

5. **Trime/HeliBoard（GPL）** 仅作架构、键盘布局、手感和隐私参考，不复制代码、不作为底座。

## 验证证据与剩余发布门禁

- 生产入口 `android/scripts/build-v2-ime.ps1` fail closed：先核对 librime、词典、prebuilt Git HEAD 和
  每个 native archive/data 文件 SHA-256，再构建双 ABI release。
- 自动验证已覆盖 API 36 x86_64 与 Android 15 API 35 16 KB AVD：全拼 `nihao → 你好`、normal/private
  中文标点、URL ASCII 标点、会话清理与一次上屏。
- release APK 检查 API 36、唯一 IME service、无网络/SMS/通知监听权限、arm64-v8a+x86_64、Rime
  canonical assets、NOTICE/许可证、zipalign `-P16` 与每个 ELF 的 16 KB LOAD 对齐。
- `RIME_PRODUCTION_LOCK.json` 是 production 构建事实；历史 `POC_LOCK.json` 只治理 spike。
- 未签名 release APK、模拟器测试和本地构建仍不是稳定发布证据。Owner 证书、同证书 Runtime/IME
  安装、真实手机/常用应用手工矩阵、升级/卸载与发布门禁仍需独立完成。
- 隐私不变量延续：普通键入不持久化；密码/敏感/无痕 context 不请求 Runtime Snapshot；IME 无网络；
  Snapshot 超时、Runtime 崩溃或 Binder 饱和只清空 ClipVault surface，不影响 Rime/Direct 输入。

## 关联
[[0008-v1-as-runtime]]（P7 允许主输入法；CandidateMixer 是其落点）、
[[0007-deterministic-suggestions-v1]]（ClipVault 候选的本地确定性评分进 CandidateMixer）。
