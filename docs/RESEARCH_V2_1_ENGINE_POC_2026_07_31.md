# ClipVault v2.1 中文引擎 PoC 调研与实施记录

修订日期：2026-08-01

## 结论

中文输入核心继续选择 **librime**，Android 集成保留两条受证据约束的路线：

- **A：自建最小 JNI，单 APK 集成 librime。** 这是优先路线，能够保留现有键盘 UI、统一
  CandidateMixer、ClipVault 候选增强层和隐私边界。
- **B：fcitx5-android 外部 addon。** 仅作为 A 路线工程失败后的回退。必须实际证明外部 addon
  能注入候选并接收点击回传，不能把“支持插件”直接等同于满足 ClipVault 的候选通路要求。
- **Trime：参考用途。** 只研究 Android 构建、部署和会话生命周期，不复制其 GPL-3.0-or-later
  JNI、构建脚本或实现代码。

PoC 与现有 production IME 隔离，不修改生产 Gradle 工程、InputMethodService、Room、同步队列或
正式 APK 依赖图。

## 上游版本锁

2026-07-31 的首次记录曾把 librime 1.16.1 误判为最新稳定版。重新核验后，当前锁定为：

| 项目 | 当前稳定版 | 精确 SHA | 升级基线 |
|---|---|---|---|
| librime | 1.17.0 | `33e78140250125871856cdc5b42ddc6a5fcd3cd4` | 1.16.1 / `de4700e9f6b75b109910613df907965e3cbe0567` |
| fcitx5-android | 0.1.3 | `048f581c652367567b8ee5c28c5163b805288895` | 0.1.2 / `5a4870c05785a8c9de390586e966ca4de70ed8e1` |
| Trime | v3.3.10 | `11440ffceb618b68deeddf4bdf7497b082cb87ae` | 参考项目，不纳入升级预算 |

机器可读事实以 `spikes/librime-android/POC_LOCK.json` 为准。ADR 只有在 A/B 两路线均形成完整
通过或失败证据后才允许更新。

## 可复用项目分工

| 项目 | 使用方式 | 边界 |
|---|---|---|
| librime | 中文分词、组词、候选和 schema 引擎 | 不自行重写拼音引擎 |
| fcitx5-android | B 路线框架及外部 addon 可行性验证 | 外部安装、升级耦合和候选控制权需实测 |
| Trime | librime Android 生命周期参考 | 不复制 GPL 源码 |
| FlorisBoard | 键盘 UI、扩展和隐私交互参考 | 不替代中文引擎 |
| HeliBoard / AOSP LatinIME | 布局、按键手感和词典管理参考 | 不替代 librime |
| Espanso / CopyQ | 文本扩展与剪贴板动作模型参考 | 不参与中文候选生成 |

## 已实施内容

隔离工作区 `spikes/librime-android/` 已包含：

1. `POC_LOCK.json`：工具链、ABI、16 KB 模拟器、上游版本、数据哈希及许可门。
2. `A_ROUTE_SOURCE_LOCK.json`：librime、Boost、LevelDB、yaml-cpp、marisa、OpenCC 及 OpenCC
   内嵌依赖的精确来源、运行时状态、排除策略和补丁锁。
3. `patches/opencc-library-only.patch`：为 OpenCC 增加保持上游默认值的 `BUILD_TOOLS` 和
   `BUILD_DATA` 开关。补丁按 SHA-256 锁定。
4. 项目自写的 table schema、四词合成字典和 default 配置，全部按 SHA-256 固定，且关闭
   user dictionary 学习。
5. 固定向量：`nihao → 你好`、`zhongguo → 中国`、候选选择提交及 reset。
6. fail-closed 静态校验：检查 Git SHA、数据/补丁/桥接源码哈希、路径逃逸、隐私状态、许可门和
   最小构建策略。
7. C++17 `Bridge`：处理初始化、失败清理、按键、候选、提交、重置和关闭语义。
8. host fake-backend 测试：验证候选流程、未处理按键、失败初始化清理和 stale state 拒绝。
9. `LibrimeBackend`：项目原创 PImpl 实现，仅调用 librime 公共 `rime_api.h` 函数表，覆盖
   setup、initialize、maintenance、session、process_key、get_input、get_context、get_commit、
   select candidate、clear composition 和 finalize。
10. CI：获取精确 librime commit，验证 `rime_api.h` Git blob，并对 `LibrimeBackend` 做
    exact-header syntax check；同时获取精确 OpenCC commit、应用锁定补丁、构建 host
    `libopencc` 并拒绝工具/数据目标进入目标图。
11. `.so` ELF LOAD 16 KB alignment 检查工具；目前尚无 native `.so` 可供验证。

上述内容只证明项目自有边界和源码级 API 兼容性，不证明 librime 已完成 Android 链接或运行。

## A 路线技术方案

目标产物为一个 ClipVault JNI `.so`，内部静态链接 librime 的最小传递闭包。计划关闭：

- glog；
- upstream tests；
- timestamp；
- Snappy 自动发现；
- OpenCC Darts、工具、数据、benchmark、Python binding；
- 生产词库和用户学习。

OpenCC 的库代码需要其内嵌 RapidJSON 1.1.0 头文件。上游 CMake 默认会进入命令行工具与转换数据
构建，因此需要锁定的 library-only patch。TCLAP 和 darts-clone 计划从产物中排除，但许可分类和
最终对象闭包仍必须记录，不能仅凭 CMake 参数推断其不存在。

`LibrimeBackend` 的边界原则：

- Android/Kotlin 层不直接依赖 Rime 类型；
- 每次测试向量使用新的 user-data 目录；
- reset 同时清空 composition 并消费未读取 commit，防止跨输入框残留；
- 不读取 Room、剪贴板、现有输入框正文或用户词库；
- 不调用同步和网络能力；
- 未处理按键返回 `handled=false`，交由上层键盘回退处理。

## B 路线技术方案

固定安装 fcitx5-android、Rime plugin/data 和 ClipVault 测试 addon。验证顺序：

1. 使用同一锁定 schema 和字典生成 Rime 候选；
2. 外部 addon 注入唯一合成候选；
3. 点击后 addon 收到确定 payload；
4. 记录 Kotlin、C++、IPC、插件 APK 和传递依赖边界；
5. 若候选注入或点击回传没有稳定公开接口，B 路线直接记为失败。

## 当前证据边界

尚未完成：

- librime、OpenCC 及全部依赖的 Android NDK 静态链接；
- JNI 导出层；
- arm64-v8a 与 x86_64 原生构建；
- schema 部署和真实 `nihao → 你好` 运行结果；
- 16 KB emulator 冷启动；
- 所有传递 `.so` alignment；
- 两次 clean build 可复现性；
- 完整许可证、NOTICE、源码和修改记录交付路径；
- fcitx5 addon 候选注入实证。

因此当前不能宣称“中文输入法已经可用”，也不能把 PoC 接入 production IME。

## 下一实施片

1. 让最新 CI 完成 OpenCC 补丁干净应用、host `libopencc` 构建和目标图排除验证。
2. 建立隔离 Gradle/NDK 壳，将现有 `LibrimeBackend` 与锁定的静态 librime 依赖闭包链接。
3. 构建 arm64-v8a、x86_64，并在每条测试向量中使用全新 user-data。
4. 运行真实 schema 部署、候选生成、选择提交、reset 和冷启动测试。
5. 生成对象/ELF 闭包、许可清单、NOTICE/source delivery 路径；审批前不上传 APK 或 `.so`。
6. 完成 16 KB page-size、`zipalign -P 16`、ELF LOAD alignment 和两次可复现构建。
7. 再实施 B 路线外部 addon；最后按固定算法终裁：A 通过则选 A；A 失败且 B 通过才选 B；否则阻塞。
