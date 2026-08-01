# ClipVault v2.1 中文引擎 PoC 进展（2026-08-01）

## 本次结论

选型结论不变：**中文引擎采用 librime，A 路线（ClipVault 自建最小 JNI 前端）继续作为优先路线，B 路线（fcitx5-android 外部 addon）保留为经过实证后才可启用的回退。**

本次完成的不是“中文输入法已经可用”，而是把 A 路线从静态调研推进到一个可审阅、可编译验证、但尚未接入原生库的最小接口实现。生产输入法、Android 依赖图、Room、同步和候选混合均未改动。

## 新增实现

`spikes/librime-android/native/` 新增项目自写的 C++17 适配层：

- 固定 `open / close / reset / processKey / snapshot / selectCandidate / takeCommit / engineVersion` 接口；
- 只调用 librime 1.16.1 已公开的 C API；
- composition、候选和 commit 只在内存中跨 JNI 返回；
- 不暴露 `sync_user_data`、`user_config_open`、网络、剪贴板、数据库或日志接口；
- 进程内只允许一个 PoC engine 实例，避免 librime 全局 initialize/finalize 被并发驱动；
- 失败信息只包含操作标识，不包含输入内容或候选正文。

`spikes/librime-android/android/` 新增独立 Kotlin facade：

- 强制同一线程使用；
- shared-data 与 user-data 目录必须为不同 canonical path；
- caller 必须为每个测试向量提供全新的临时 user-data；
- `close()` 后 native handle 立即失效；
- 未接入 `ClipVaultFullKeyboardService` 或 CandidateMixer。

`native/CMakeLists.txt` 只负责链接调用方提供的、ABI 匹配且已锁定的 librime：不下载源码、不解析浮动版本、不把未经审阅的二进制带入仓库，并固定 16 KB `max-page-size/common-page-size` 链接参数。

## 依赖锁推进

Track A 已将 librime 1.16.1 的直接依赖输入补充到机器可读 lock：Boost 1.89.0、glog、leveldb、yaml-cpp、googletest、marisa-trie 和 OpenCC。glog 与 googletest 分别由 `ENABLE_LOGGING=OFF`、`BUILD_TEST=OFF` 排除，后续必须通过实际构建产物证明没有进入 `.so` 闭包。

这仍不是完整供应链清单。OpenCC 的嵌套/生成数据、Boost 实际链接组件、ABI 级共享库闭包、NOTICE 交付路径与可复现构建证据仍未完成，因此 `transitive_closure_status` 保持 `INCOMPLETE_FAIL_CLOSED`，二进制上传继续禁止。

## A/B 对比更新

| 维度 | A：最小 librime JNI | B：fcitx5 外部 addon |
|---|---|---|
| 产品形态 | 单 APK，可直接接现有完整键盘 | 依赖外部 fcitx5 主应用与插件/addon |
| 候选控制 | 可进入 ClipVault 自己的 CandidateMixer | 候选注入和点击回传边界仍需实测 |
| 隐私边界 | 当前接口可限制在内存和临时 user-data | 需要审计跨 APK、插件和框架的数据边界 |
| 工程风险 | Android native 构建和依赖闭包较难 | 安装、升级、签名、插件 ABI 和多 APK 闭包较重 |
| 当前证据 | JNI/Kotlin/CMake 契约已落地，尚未 native build | 只有官方插件能力与源码结构证据，未实现 addon |

因此本次没有理由改变优先级：A 的产品契合度更高，当前应继续完成 A 的真实构建；B 不能因为“已有框架”就被假定为低成本或可直接注入 ClipVault 候选。

## 新增门禁

`validate_native_contract.py` 现在会失败关闭地检查：

- Kotlin 与 C++ JNI 方法集合完全一致；
- librime API 调用只来自 allowlist；
- reset、候选、选择和 commit 必需路径存在；
- 不出现网络、持久化、剪贴板、同步、日志或源码下载 API；
- CMake 必须保持 import-only 且包含 16 KB linker flags；
- Track A 直接依赖必须精确锁定，传递闭包必须继续标为未完成。

## 尚未完成

以下事项没有被本次实现伪装成“已通过”：

1. librime 及依赖的 arm64-v8a/x86_64 原生编译；
2. 实际 APK 或测试壳；
3. `nihao → 你好`、`zhongguo → 中国` 的设备/模拟器执行结果；
4. 所有传递 `.so` 的 16 KB 对齐和 16 KB emulator 冷启动；
5. 两次 clean build 的逐文件 hash 复现；
6. 完整许可、NOTICE、源码/relink 交付；
7. fcitx5 addon 候选注入与点击回传实证。

## 下一实施片

下一片只做 A 路线真实构建，不接生产 IME：

1. 建立无浮动输入、无隐式下载的 Android native dependency build；
2. 为每个 ABI 生成 source SHA、compiler/link flags、`.so` 路径和 SHA-256 manifest；
3. 建立独立 Android test shell，复制锁定 synthetic data，并为每个向量创建/销毁临时 user-data；
4. 跑离线候选、选择、commit、reset 测试；
5. 再进入 16 KB、复现、体积/耗时和升级演练。

A 与 B 的完整证据都齐备之前，不更新 ADR-0010 的长期底座终裁，不启动生产接入。
