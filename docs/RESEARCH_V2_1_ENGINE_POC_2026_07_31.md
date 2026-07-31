# ClipVault v2.1 中文引擎 PoC 调研与启动记录（2026-07-31）

## 结论

中文输入引擎继续采用 **librime**。长期 Android 集成保持 A/B 双路线：

- A：ClipVault 自建最小 JNI 前端，将 librime 嵌入现有完整键盘服务。它最符合单 APK、统一
  CandidateMixer、UI 与隐私边界全控的目标，仍是优先路线。
- B：ClipVault 作为 fcitx5-android 的外部 addon。它是工程失败时的回退，不是未经验证的
  自动替代；必须证明 addon 确实能够注入候选并接收点击回传。
- Trime：只用于核对 Android 上 librime 的构建和会话模型。其相关构建/JNI代码为
  GPL-3.0-or-later，不复制到 ClipVault。

这次没有把中文引擎直接接入 production IME。仓库目前仍受 v1.6 发布门和 v2.0 双 IME
稳定门约束；PoC 首先建立隔离、可审计、可失败的证据链。

## 2026-07-31 重新核验的版本锁

| 项目 | 当前稳定版 | 精确 SHA | 前一稳定版 / SHA | 用途 |
|---|---|---|---|---|
| librime | 1.16.1 | `de4700e9f6b75b109910613df907965e3cbe0567` | 1.16.0 / `a251145d3aafa33871824a40bbec04c966bd8b56` | A 路线引擎与固定升级演练 |
| fcitx5-android | 0.1.3 | `048f581c652367567b8ee5c28c5163b805288895` | 0.1.2 / `5a4870c05785a8c9de390586e966ca4de70ed8e1` | B 路线框架与固定升级演练 |
| Trime | v3.3.10 | `11440ffceb618b68deeddf4bdf7497b082cb87ae` | 不纳入升级预算 | 参考，不取代码 |

旧 ADR 中“librime 1.17.0”的描述不是当前可验证稳定版，应以本表和
`spikes/librime-android/POC_LOCK.json` 为 PoC 锁定事实；ADR 的最终选择仍需 A/B 完整证据后
由 Owner 更新。

## 可复用项目分工

| 项目 | 采用方式 | 不采用原因/边界 |
|---|---|---|
| librime | 核心中文引擎 | 不自研拼音分词、组词和候选生成 |
| fcitx5-android | B 路线与候选框架参考 | 外部安装、升级耦合和候选控制权需实测 |
| Trime | JNI、部署、会话生命周期参考 | GPL 源码不能复制到现有 ClipVault |
| FlorisBoard | Compose/UI、扩展和隐私交互参考 | 不是现成中文引擎替代 |
| HeliBoard/AOSP LatinIME | 键盘手感、布局、词典管理参考 | GPL/AOSP 架构不替代 librime |
| Espanso/CopyQ | 文本扩展与剪贴板动作模型参考 | 不参与 Android 中文候选生成 |

## 已落地的 P0

`spikes/librime-android/` 新增：

- 机器可读的 `POC_LOCK.json`，锁定工具链、ABI、16 KB 模拟器和 A/B 上游版本；
- `THIRD_PARTY_NATIVE.md`，在 schema、dictionary 与传递依赖不完整时禁止二进制上传；
- 项目自写的合成拼音/重置向量；
- fail-closed 静态校验器；
- 遍历所有 `.so` 的 16 KB ELF LOAD alignment 检查器；
- 仅运行静态检查、不会构建或上传原生产物的 GitHub Actions gate。

## 下一实施片

### P1：数据与许可锁

选择一个最小简体拼音 schema/dictionary 组合，逐项记录仓库、SHA、SPDX、NOTICE、是否修改及
分发义务。未完成前，向量保持 inactive，CI 不上传 APK/`.so`。

### P2-A：最小 librime JNI

独立 Gradle 工程只暴露 `initialize/reset/processKey/getCandidates/selectCandidate/getCommit`。
每个向量使用全新 user-data，禁用学习、同步和网络；不得读 Room、剪贴板、应用输入框正文。

### P2-B：外部 addon

固定安装 fcitx5-android、Rime plugin/data 与 ClipVault addon。先证明 `nihao` 能产生 Rime
候选，再注入一个唯一合成候选并验证点击 payload；记录 Kotlin/C++/IPC 真实边界。

### P3：硬证据

两路线都执行 arm64-v8a/x86_64 clean build、所有传递 `.so` alignment、16 KB emulator
冷启动、断网向量、两次可复现构建、体积/耗时/补丁/bootstrap/升级演练。只有这一步完成后才
按 V2-S004 的固定算法终裁。
