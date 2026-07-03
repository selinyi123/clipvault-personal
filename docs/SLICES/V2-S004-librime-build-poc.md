# V2-S004 — librime Android Build PoC

> 状态：计划；V2-S003 paper spike 的后继执行片。
> 目标：在隔离工程中验证 A（自建 librime 前端）与 B（fcitx5 外部 addon）是否可构建、分发和维护，
> 再由 Owner 更新 ADR-0010。本文阈值是 ClipVault 的工程预算，不是上游项目事实。

## 0. 边界

- 不修改 `ClipVaultPanelImeService`、`ClipVaultFullKeyboardService` 或 production APK 的依赖图。
- 不接 CandidateMixer，不写 Room/outbox，不读取或记录普通键入，不添加网络权限。
- PoC 只使用固定测试向量和全新临时 Rime user-data；不使用用户词典、剪切板或个人数据。
- A、B 都产出通过或失败证据后才终裁；任一路线缺证据都不能用“预期可行”代替。

## 1. 锁定输入与工具链

- 记录 librime、全部 submodule、schema、dictionary、fcitx5-android 与 addon 的完整 commit SHA；禁止浮动分支。
- 固定 JDK 17、Gradle 8.10.2、AGP 8.5.2、Build Tools 35.0.0、CMake 3.22.1、
  Android NDK `28.0.13004108`（r28）。必须构建 `arm64-v8a`（目标交付/体积测量）与 `x86_64`
  （锁定 emulator runtime）；两种 ABI 分别留存 hash/alignment 证据。
- 16KB 设备固定 `system-images;android-35;google_apis_ps16k;x86_64`。`POC_LOCK.md` 必须记录
  SDK package revision、package checksum、Android Emulator package version，以及 `ubuntu-24.04` runner 的
  `ImageOS`、`ImageVersion` 和对应 runner-images release URL；GitHub 未提供的 VM digest 不作门禁。不得使用
  “API 35 或更高”或 `ubuntu-latest` 这种浮动环境。运行前必须断言：

```bash
adb shell getconf PAGE_SIZE   # 必须输出 16384
```

- 工具链变更必须作为独立审阅项更新本文与 build metadata，不能在失败后静默漂移版本。

## 2. 供应链与许可硬门

在引入源码前创建 `THIRD_PARTY_NATIVE.md`。engine、schema、dictionary、构建工具、A 的传递 native
依赖、B 的 fcitx5/addon 依赖逐项记录：

- name / role / version 或 SHA / source URL / SPDX license / copyright；
- 是否修改、补丁文件与上游 issue；静态/动态/JNI/data 的组合方式；
- 实际进入 spike APK/addon 的文件路径；
- 随产物提供的 license text、NOTICE、对应源码、修改源码或 relink 义务及交付位置。

清单必须覆盖 spike APK、B addon 与全部传递依赖，不能只写“NOTICE obligation”，也不能用
“librime 是 BSD-3”概括 Rime 数据。许可按 A、B 路线分别审批；某路线全部条目经 Owner/指定
license reviewer 标为 `approved` 才通过该路线的许可门。在此之前：

- CI 可以编译并上传纯文本测试、alignment 和 metadata 报告；
- CI **不得上传 APK、AAB、`.so`、addon 或含第三方数据的二进制 artifact**；
- 不创建 Release，不合入 production APK。

## 3. A / B 最小 PoC

### A — 自建 librime 前端

- 从固定源码构建 librime 及传递依赖，只暴露 reset / key input / select / candidates 的最小 JNI。
- 固定 schema 后完成 `nihao → 候选 → 选择 → reset`，Kotlin/UI 只作测试壳。
- 第三方源码不得靠未记录的工作区手改才能构建；所有必要补丁必须是 repo 内可重放 patch。

### B — fcitx5 外部 addon

- 构建独立 addon APK，并安装固定版本 fcitx5-android + Rime plugin/data；先证明固定 `nihao` 输入能产生
  Rime composing/候选，再证明 addon 能向同一候选流注入一项测试候选并接收点击回传。
- 记录实际边界是 Kotlin facade、C++ addon 还是 IPC；不得把“存在 plugin schema”等同于候选注入 API。
- 记录安装依赖、进程边界、候选点击回传与升级耦合；不把 fcitx5 代码并入 ClipVault production APK。

## 4. 路线专属行为门

### A — Rime 候选

- ClipVault 自写 5–10 条输入/候选前 N 项/选择结果向量；不复制无明确许可的 `rime/tests` 内容。
- 每条用例创建全新临时 user-data，禁用学习和同步，清除部署缓存；不得复用用户词典或历史状态。
- 固定 locale、schema、数据 SHA 与候选页大小；失败输出向量 ID 和实际候选，不输出任何个人内容。
- reset 后 composing/candidates 必须为空。

### B — addon 候选注入

- 固定 fcitx5-android/Rime plugin/data/addon SHA 和一个专用测试触发器；先用固定 schema 断言 `nihao`
  产生非空 composing 与 Rime 候选，再断言候选流出现唯一固定 addon 测试候选、点击后返回固定 payload，
  且该测试候选在 reset/重启测试 session 后消失。
- 每例清空 fcitx5/addon app data 后重建测试 session；不导入用户配置、词典、剪切板或历史。
- 失败只输出测试 trigger/candidate ID 与事件序列，不输出应用输入框或个人内容。

源码与依赖取得后，A、B 的运行时候选路径必须在断网环境完成，且不触碰 ClipVault Room/outbox。

## 5. Native / 16KB 硬门

APK/addon 内每个传递 `.so`（不只 JNI wrapper）都要检查：

```bash
llvm-objdump -p libfoo.so             # 每个 LOAD align >= 2**14
zipalign -v -c -P 16 4 spike.apk
```

- 在 §1 锁定且 `PAGE_SIZE=16384` 的 emulator 上冷启动，并完成各路线 §4 的固定流程。真机只能作为
  附加证据，并需记录 build fingerprint，不能替代锁定 emulator 证据。
- 任一 ABI、任一传递 `.so`、ZIP 对齐或 16KB runtime 测试失败，路线即未通过。

## 6. 可复现门

每条路线分别在同一干净 runner、同一锁定输入连续做两次 clean build：

- 两次全部 `.so` 的 SHA-256 必须逐字节相同；
- 生成未签名 APK 内容清单（entry path → SHA-256），排除 `META-INF` 签名文件后必须相同；
- 原始未签名 APK hash 若不同，只允许 ZIP timestamp/order 等已列入报告的容器元数据差异；
  任一 entry 内容差异或未列入 allowlist 的差异都判失败；
- 保存工具链版本、环境镜像、全部 SHA、ABI、hash、alignment report 与差异报告。

## 7. 工程预算与终裁算法

以下是本项目的冻结预算。每条路线在相同 GitHub-hosted `ubuntu-24.04` ImageVersion 上测两次并取较差值：

| 指标 | 通过预算 |
|---|---|
| arm64 + x86_64 clean build | ≤ 30 分钟 |
| 可安装产物总字节数 | ≤ 80 MiB；A=sum(A spike APK)，B=sum(fcitx5 主 APK + Rime plugin/data APK + ClipVault addon APK)，split APK 全计入 |
| 可重放的长期第三方补丁 | ≤ 5 个，且每个有原因与上游 issue/跟踪链接 |
| SDK/NDK 已安装后的 bootstrap | README 中 ≤ 12 条非交互命令，无工作区手改 |
| 升级演练 | 按下述固定选版与计时边界 ≤ 4 小时，并重新通过该路线所有门禁 |

升级选版不能临场挑容易的 commit。PoC 开工时先提交 `POC_LOCK.md`：A 使用开工日 librime 最新稳定 tag
及其紧邻前一个稳定 tag，B 使用开工日 fcitx5-android 最新稳定 release tag 及其紧邻前一个稳定 tag；
记录 tag、resolved SHA 和查询日期。以“前一稳定版 → 最新稳定版”为升级演练，schema/dictionary 测试数据
保持同一 SHA。若上游不足两个稳定版本，该路线升级预算记为“不可判定”，路线不通过而不是另挑 commit。

4 小时从切换 baseline lock 前开始，包含 patch 刷新、配置修改、clean build、该路线行为/许可/16KB/复现
检查与报告；到报告生成结束停止。只可扣除有时间戳证据的外部 CI 排队或服务故障，正常下载/编译时间不扣除。

路线通过定义：

- **A 通过** = A 的 §2 许可 + §3 A + §4 A + §§5–6 + 本节全部预算通过。
- **B 通过** = B 的 §2 许可 + §3 B + §4 B + §§5–6 + 本节全部预算通过。
- “完成证据”可以是通过，也可以是带具体失败层和日志的失败；缺证据不是失败结论，而是尚未完成。

终裁顺序固定为：

1. A、B 都必须完成证据；B 不是未经验证的自动回退，任一路线缺证据时不终裁。
2. A 通过 → 选择 A（它是 ADR-0010 已记录的架构偏好）；B 即使失败也只作为回退风险记录，
   不反向推翻已通过的 A。
3. A 失败、B 通过 → 选择 B，并在 ADR 逐项记录 A 的失败证据。
4. A、B 都失败 → 状态保持“engine=librime；长期底座未裁定/阻塞”，不启动 v2.2，
   不用降低门槛制造结论。某路线许可未批准只使该路线失败，不自动否决另一条已通过路线。
5. Owner review 后才可更新 ADR、合并生产接入或切版本；CI 绿灯不等于自动裁决。

## 8. 必交证据

- `THIRD_PARTY_NATIVE.md` 与所需 license/NOTICE/source-offer/relink 文件；
- A、B 构建日志、黄金向量结果、16KB runtime 结果、alignment report；
- 两次 clean build 的 hash manifest、差异报告、耗时/体积/patch/bootstrap/升级测量；
- ADR-0010 更新草案，明确选择 A、选择 B 或保持阻塞，不得只写“成本可接受”。

## 9. 延后项

AOSP CTS `MockImeSession` 的 test-only IME/session 结构可用于后续 production IME 生命周期自动化；隔离 JNI/addon
PoC 不需要切换系统 IME，因此它不是本片通过门禁，也不引入 CTS 私有依赖。
