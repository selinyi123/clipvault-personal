# v2.1 Build PoC 去重调研（2026-07-02）

> 目标：为“最小 librime Android build PoC → ADR-0010 A/B 终裁”补齐工具链、许可、验证与回滚事实。
> 本轮只规划，不接生产 IME、不复制 GPL/未许可代码、不触碰 typed-text/云能力。

## 0. 去重基线

调研前已完整核对：

- `RESEARCH_AND_ROADMAP.md` R1–R8（含 R8 librime Android 宏观构建前提）；
- `ROADMAP_V2_KEYBOARD.md`、`ADR-0010`、`V2-S003`；
- 已有 librime / Trime / fcitx5-android / HeliBoard 许可与角色结论。

因此本轮**不重复**“选哪个引擎”“Trime 是否 GPL”“fcitx5 是否有插件”等已裁定问题，只找 build PoC
仍缺的可执行证据。检索平台包括 GitHub、Android Developers、AOSP/CTS、Hacker News、Reddit、
Stack Overflow；后 3 类社区结果只作需求/坑位信号，不替代官方构建与许可事实。

R9 已取代 R8 中“NDK 25 / CMake 3.22 / SDK 35 是必须组合”的工具链快照：CMake 仍由项目显式锁定，
但当前 PoC 以 Android 官方 16KB 指南和 V2-S004 的精确版本为准，不再把 R8 的旧组合当硬门。

## 1. 新增结论（R9–R14）

| ID | 方向 | 新事实 | 决策 |
|---|---|---|---|
| R9 | Android 16KB page size | Android 要求 APK 内每个 native `.so` 同时满足 ELF load-segment 与 ZIP 16KB 对齐；NDK r28 默认产出兼容结果，AGP 需 ≥8.5.1。fcitx5-android 0.1.2 已迁到 r28。 | **Adopt**：PoC 固定 NDK r28；`zipalign -P 16`、逐 `.so` `llvm-objdump` 与 16KB emulator 成为硬门禁。 |
| R10 | fcitx5 回退真实成本 | 独立 APK plugin 主要封装 native Fcitx addon `.so` 与数据；Rime plugin 的 Android 层很薄，核心仍是 C++ addon/submodules。没有证据表明 Kotlin facade 可直接注册为候选提供器。 | **Watch / 降低乐观度**：B 不是“低成本确定回退”；终裁前先做最小外部 addon→候选流注入 PoC。 |
| R11 | Rime 数据许可 | librime 的 BSD-3 只覆盖引擎，不覆盖 schema/dictionary。`rime-pinyin-simp` 为 Apache-2.0；常见 `rime-prelude` / `rime-essay` 为 LGPL-3.0，且没有稳定 release 可直接钉住。 | **Adopt**：engine/schema/dictionary 分层列 SHA、license、NOTICE；禁止浮动 `master`，禁止把“引擎 BSD”写成“整包 BSD”。 |
| R12 | IME device QA | AOSP CTS `MockImeSession` 用 test-only IME、`UiAutomation.executeShellCommand("ime enable/set/reset")`、轮询当前 IME 与事件流断言生命周期。 | **Defer / 借结构**：用于后续 production IME 生命周期 QA；隔离 JNI/addon PoC 不切系统 IME，因此不列为 v2.1 build 门禁，也不依赖 CTS 私有代码。 |
| R13 | 引擎行为验证 | `rime/tests` 用 `spec.toml + <schema>.test.yaml` 做拼音/方案→候选 E2E，而不只测 JNI“返回非空”。仓库仍年轻且未声明 LICENSE。 | **Watch / 借思想**：ClipVault 自写少量黄金向量；不复制未许可测试内容。 |
| R14 | 原生构建可复现性 | fcitx5-android release 提供逐 ABI SHA-256 与 build metadata，并修复 gettext 版本造成的非确定产物。 | **Adopt-light**：两次 clean build 的 `.so` 与未签名 APK entry 内容必须相同；只允许报告 allowlist 内的 ZIP 容器元数据差异。 |

## 2. 直接来源

### R9 — 16KB / toolchain

- [Android: Support 16 KB page sizes](https://developer.android.com/guide/practices/page-sizes)
- [fcitx5-android 0.1.2 release](https://github.com/fcitx5-android/fcitx5-android/releases/tag/0.1.2)

### R10 — fcitx5 plugin 边界

- [fcitx5-android plugin tree](https://github.com/fcitx5-android/fcitx5-android/tree/master/plugin)
- [Rime plugin native tree](https://github.com/fcitx5-android/fcitx5-android/tree/master/plugin/rime/src/main/cpp)
- [plugin schema](https://github.com/fcitx5-android/fcitx5-android/blob/master/plugin/pluginSchema.xsd)

### R11 — engine / schema / dictionary 许可拆分

- [rime/librime](https://github.com/rime/librime) — BSD-3
- [rime/rime-pinyin-simp](https://github.com/rime/rime-pinyin-simp) — Apache-2.0
- [rime/rime-prelude](https://github.com/rime/rime-prelude) — LGPL-3.0
- [rime/rime-essay](https://github.com/rime/rime-essay) — LGPL-3.0

### R12–R14 — 测试与可复现

- [AOSP CTS MockImeSession](https://android.googlesource.com/platform/cts/+/fe21b7c221ca196a65817d6fa270af13813c36a9/tests/inputmethod/mockime/src/com/android/cts/mockime/MockImeSession.java)
- [AOSP MockImeSessionRule](https://android.googlesource.com/platform/cts/+/a92f93e9eddd15b600a62889975d1f65b5420e9c/tests/autofillservice/src/com/android/cts/mockime/MockImeSessionRule.java)
- [rime/tests](https://github.com/rime/tests)（未声明 LICENSE，只借测试结构思想）
- [fcitx5-android releases/build metadata](https://github.com/fcitx5-android/fcitx5-android/releases)
- [fcitx5-android reproducible installation notes](https://fcitx5-android.github.io/en/installation/)

## 3. 社区平台结果的处置

- Hacker News / Reddit 的 HeliBoard、Urik、Fcitx5 讨论再次确认用户重视“无网络权限、全本地”，但该结论已在
  PRODUCT_SPEC/P2/G2 冻结，**不记为新方向**。
- Stack Overflow 中通过旧 UIAutomator runner 或测试内直接启动 `adb` 的方案年代久、维护成本高；
  **Reject**，优先官方 `UiAutomation.executeShellCommand` + test orchestration。
- 未找到有明确维护者、可验证 provenance、适配当前 librime 且许可清晰的预编译 AAR；
  **Reject** 随机第三方二进制，PoC 必须从钉住的源码构建。

## 4. 对下一关键节点的影响

v2.1 build PoC 的“成功”不再只是“JNI 能返回一个中文词”，而必须同时满足：

1. 固定源码/数据 SHA，并对 A/B APK/addon、全部传递依赖记录逐项许可与实际交付义务；
2. 按 V2-S004 锁定精确工具链及 API 35 `google_apis_ps16k` image/emulator revision，先确认
   `PAGE_SIZE=16384`；
3. 固定拼音输入的确定性候选黄金断言，每例使用全新 user-data 并禁用学习/同步；
4. 所有 `.so` 与 APK 通过 16KB 对齐检查；
5. 两次 clean build 满足明确的 entry/hash 一致规则并产出差异报告；
6. A/B 两路 PoC 与冻结工程预算证据齐全后，才按 V2-S004 算法更新 ADR-0010；两路都失败则阻塞。

下一轮不得重复 R9–R14；只有上游版本、许可、构建结果或实测成本发生变化时才重开。
