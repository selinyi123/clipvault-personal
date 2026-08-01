# ClipVault v2.1 中文引擎 PoC 调研与启动记录（2026-07-31，2026-08-01 修订）

## 结论

中文输入引擎继续采用 **librime**。长期 Android 集成保持 A/B 双路线：

- A：ClipVault 自建最小 JNI 前端，将 librime 嵌入现有完整键盘服务。它最符合单 APK、统一
  CandidateMixer、UI 与隐私边界全控的目标，仍是优先路线。
- B：ClipVault 作为 fcitx5-android 的外部 addon。它是工程失败时的回退，不是未经验证的
  自动替代；必须证明 addon 确实能够注入候选并接收点击回传。
- Trime：只用于核对 Android 上 librime 的构建和会话模型。其相关构建/JNI 代码为
  GPL-3.0-or-later，不复制到 ClipVault。

PoC 不直接接入 production IME。仓库仍受 v1.6 发布门和 v2.0 双 IME 稳定门约束；这里先建立
隔离、可审计、可失败的证据链。

## 版本锁勘误与当前锁

2026-07-31 的首次记录把 librime 1.16.1 误判为最新稳定版。2026-08-01 对上游 tag/commit
重新核验后，确认 1.17.0 已于 2026-06-05 发布。当前锁如下：

| 项目 | 当前稳定版 | 精确 SHA | 前一稳定版 / SHA | 用途 |
|---|---|---|---|---|
| librime | 1.17.0 | `33e78140250125871856cdc5b42ddc6a5fcd3cd4` | 1.16.1 / `de4700e9f6b75b109910613df907965e3cbe0567` | A 路线引擎与固定升级演练 |
| fcitx5-android | 0.1.3 | `048f581c652367567b8ee5c28c5163b805288895` | 0.1.2 / `5a4870c05785a8c9de390586e966ca4de70ed8e1` | B 路线框架与固定升级演练 |
| Trime | v3.3.10 | `11440ffceb618b68deeddf4bdf7497b082cb87ae` | 不纳入升级预算 | 参考，不取代码 |

`spikes/librime-android/POC_LOCK.json` 是当前 PoC 锁定事实。ADR 的最终选择仍需 A/B 完整证据后
由 Owner 更新。

## 可复用项目分工

| 项目 | 采用方式 | 不采用原因/边界 |
|---|---|---|
| librime | 核心中文引擎 | 不自研拼音分词、组词和候选生成 |
| fcitx5-android | B 路线与候选框架参考 | 外部安装、升级耦合和候选控制权需实测 |
| Trime | 构建与会话生命周期参考 | GPL 源码不能复制到现有 ClipVault |
| FlorisBoard | UI、扩展和隐私交互参考 | 不是现成中文引擎替代 |
| HeliBoard/AOSP LatinIME | 键盘手感、布局、词典管理参考 | 架构不替代 librime |
| Espanso/CopyQ | 文本扩展与剪贴板动作模型参考 | 不参与 Android 中文候选生成 |

## 已落地

`spikes/librime-android/` 当前包含：

- `POC_LOCK.json`：工具链、ABI、16 KB 模拟器和 A/B 上游版本；
- `A_ROUTE_SOURCE_LOCK.json`：librime 1.17.0、Boost 1.89.0、librime submodule 以及 OpenCC
  内嵌依赖的精确来源、许可状态、补丁哈希和最小构建策略；
- `patches/opencc-library-only.patch`：在保留 OpenCC 上游默认行为的同时增加可关闭工具与数据
  构建的开关；补丁字节已锁定，但尚未在 CI 中证明可干净应用；
- 项目自写的最小 table schema、四词合成字典和 default 配置，全部按 SHA-256 锁定；
- 与数据哈希绑定的合成候选/重置向量；
- `THIRD_PARTY_NATIVE.md`：许可未批准时禁止二进制上传；
- fail-closed 静态校验器：核对 Git SHA、数据/补丁/桥接源码哈希、路径边界、隐私声明和最小依赖策略；
- 项目自写 C++17 bridge contract：规范 lifecycle、失败初始化清理、未处理按键、候选选择、commit
  和 reset 语义；
- host fake-backend 测试：验证 `nihao → 你好 → 选择提交 → reset`，但不声称已链接 librime；
- 遍历全部 `.so` 的 16 KB ELF LOAD alignment 检查器；
- GitHub Actions 静态与 bridge host build/test gate，不上传二进制产物。

极小 table schema 用于验证引擎通路，而不是评价生产词库。这样先隔离词库版权、体积和来源问题，
同时固定验证 `nihao → 你好`、`zhongguo → 中国` 和 reset。

## 技术方案

### A：单 APK / 原生 librime（优先）

生产目标是一个 ClipVault JNI `.so`，内部静态链接 librime 的最小传递闭包。PoC 关闭 glog、
upstream tests、timestamp、Darts、benchmark、Python binding 和意外 Snappy 发现，并复用同一
marisa，降低 ABI 数量、许可面和复现漂移。

补充核验发现，OpenCC 1.1.9 的库代码会使用其内嵌 RapidJSON 1.1.0 头文件；同时 OpenCC 的
CMake 默认无条件进入 `src/tools` 和字典数据构建，导致 TCLAP、宿主工具与 Python 出现在构建图中。
Darts 也是可选内嵌依赖。当前已锁定一个小型、可重放的 OpenCC library-only patch，并计划显式关闭
Darts、tools 和 data；在 CI 证明补丁可干净应用、且对象图证明 TCLAP、Darts 和工具目标未进入
Android 产物之前，不能宣称传递依赖闭包已经通过许可门。

项目自写 `Bridge` 只管理边界与不变量；下一步 `LibrimeBackend` 只调用 librime 公共 C API：
`setup/initialize/start_maintenance/create_session/process_key/get_context/get_commit/select_candidate/
clear_composition/destroy_session/finalize`。不得复制 Trime JNI。

### B：fcitx5-android 外部 addon（回退）

固定安装 fcitx5-android、Rime plugin/data 与 ClipVault addon。先证明锁定数据能产生 Rime 候选，
再证明 addon 能注入唯一合成候选并收到点击 payload。若候选流没有稳定的外部注入/回传接口，B 直接
记录为失败，而不是把“支持外部插件”等同于满足 ClipVault 需求。

## 下一实施片

1. 在 CI 中检出精确 OpenCC SHA、应用已锁定 patch，并检查 CMake target/object graph，确认
   RapidJSON 是预期运行时头文件依赖，TCLAP、darts-clone、tools 和 data 目标均未进入闭包。
2. 编写 `LibrimeBackend` 与最小 JNI 封装，保持 host contract 不变。
3. 建立隔离 Gradle/NDK 壳，构建 arm64-v8a 和 x86_64；任何必要补丁进入仓库且可重放。
4. 每个向量使用全新 user-data，断网运行，禁用学习和同步，不读 Room、剪贴板或输入框正文。
5. 生成实际 `.so` 闭包、许可证/NOTICE/source delivery 路径；审批前仍不上传 APK/`.so`。
6. 通过所有 ELF LOAD alignment、`zipalign -P 16` 和 16 KB emulator 冷启动门。
7. 再完成 B 路线外部 addon 证据；两路线都有完整通过或失败结果后，按 V2-S004 固定算法终裁。
