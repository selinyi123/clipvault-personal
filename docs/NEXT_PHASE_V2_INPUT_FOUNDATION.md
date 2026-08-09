# ClipVault v2 输入基础阶段

> 状态：Owner 于 2026-08-01 授权启动。本文定义下一阶段范围、并行工作流与停止条件；
> 它不等于 Android/Windows 输入法已经达到发布质量，也不覆盖任何设备、签名或发布门禁。
> 跨 v2.1-v2.5 的日用验收合同与当前执行快照见
> [V2_DAILY_USE_ACCEPTANCE.md](V2_DAILY_USE_ACCEPTANCE.md)。
> OTP 的 Android restricted-permission 发布证据见 [PLAY_SMS_PERMISSION.md](PLAY_SMS_PERMISSION.md)。
> 多智能体、多分支的当前任务所有权、漂移检查、合流和证据规范见
> [V2_DAILY_EXECUTION_CHARTER.md](V2_DAILY_EXECUTION_CHARTER.md)。

## 1. 产品目标

ClipVault 从“输入法级剪切板知识系统”演进为个人自用的跨端输入中枢：

```text
Android 主输入法 + Windows 原生 TSF 输入法
+ librime 中文核心和跨平台输入合同
+ Personal Memory / 跨端剪切板
+ 独立、短时、一次性 OTP Relay
```

以下不变式优先于功能完整度：

- 普通按键、组字和完整上屏正文默认不持久化、不记录、不上传；
- 只有用户显式保存的普通内容才能成为 ClipVault 资产；
- IME 按键关键路径不访问网络、Python、HTTP、Room/SQLite 或远程服务；
- 密码、永久凭据与 OTP 使用不同数据类型和不同通道；
- Android 或 Windows Runtime 故障时，基本输入必须继续工作。

## 2. 目标进程边界

### Android

```text
ClipVault IME APK
  Android IME shell + librime
  Rime candidate surface
  ClipVault toolbar
  system Inline Autofill host
  no INTERNET / SMS permission
          |
          | signature-protected Binder / bounded local snapshot
          v
ClipVault Companion Runtime APK
  Room, clipboard, Personal Memory, pairing, sync, OTP capture, E2EE
```

当前同 APK 双 IME 是 v2.0 迁移状态，不是最终权限边界。拆包只能在 IPC 合同、签名权限、
Runtime 失效降级与升级路径通过门禁后进行。

### Windows

```text
Windows application
        | TSF
ClipVaultTextService.dll
  COM/TSF, composition, edit sessions, candidate UI
  no librime / Python / database / network
        | per-user ACL named pipe + versioned protobuf
ClipVaultImeRuntime.exe
  librime sessions, local candidate state, host recovery
        | independent asynchronous local IPC
existing Python Desktop Runtime
  clipboard, Personal Memory, sync, OTP receiver, settings and diagnostics
```

Python Runtime 只异步发布经过隐私过滤的候选快照；不得成为逐键 RPC 服务。

## 3. 候选面分层

第一阶段保留三个独立候选面：

1. 系统 Inline Autofill suggestion；
2. librime 原生输入候选；
3. ClipVault 工具栏候选。

暂不把三类候选交错排序。`CandidateMixer` 第一阶段只负责 ClipVault 自有来源；待稳定
candidate ID、revision、分页、异步失效和隐私策略通过跨平台合同后，再单独评估统一混排。

## 4. 版本重排

| 版本 | 目标 | 明确不包含 |
|---|---|---|
| v2.0 | 现有 Android 双 IME 入口的真机稳定证据 | 中文引擎、Windows TSF、OTP |
| v2.1 | 输入基础：Android A/B PoC、Windows TSF PoC、Engine Protocol V2 | production 接入、正式安装包 |
| v2.2 | Android 中文日用 Beta、三层候选面、Runtime 失效降级 | 统一候选混排、OTP 自动中继 |
| v2.3 | Windows 原生 TSF Beta、外置 Host、本地 ClipVault 工具栏 | Python/网络进入逐键路径 |
| v2.4 | 本机/合成 OTP Relay PoC：内存核心、Android 捕获面、Windows 显式插入面 | 任何未加密跨设备 Beta、普通剪切板历史、离线补传、盲目自动输入 |
| v2.5 | 最小配对/E2EE 后的跨设备 OTP Beta + 通用 Relay/恢复硬化 | 云端明文、云端事实源 |
| v3.0 | 显式语音、ASR、纠错和 AI | 默认上传普通输入上下文 |

## 5. 并行开发线

| 分支 | 范围 | 本轮产物 |
|---|---|---|
| `codex/v2-next-foundation` | 治理与跨端合同 | 本文、ADR、GATES、Engine/OTP 合同、研究矩阵 |
| `codex/v2-android-ime` | Android 隔离实验 | 不接 production APK 的输入会话/候选合同 PoC |
| `codex/v2-windows-ime` | Windows 隔离实验 | 上游锁、Engine V2 protobuf 与 golden frames |
| `codex/v2-otp-relay` | OTP 隔离实验 | 无数据库/网络接线的内存态单次凭据核心 |

每条分支独立验证。建议合并顺序为：foundation → OTP core → Android PoC → Windows PoC。
后续分支在合并前必须重新基于已接受的合同检查差异；本轮不自动合并、提交、推送或发布。

## 6. 研究收敛规则

每个类别可以保留 20 个以上项目作为发现池，但生产决策不按项目数量投票：

```text
120+ 发现池
→ 20–30 个源码级审阅
→ 8–10 个证据矩阵候选
→ 4–6 个隔离构建 PoC
→ 少数生产依赖
```

每个进入源码审阅的项目必须记录固定 commit、许可证、子模块、词库/模型来源、构建结果、
进程/权限边界、需要维护的补丁数量和升级演练。未完成这些字段的项目不能成为生产底座。

## 7. 当前停止条件

本阶段只有在以下证据齐全后，才能讨论 production 接入：

- Android A/B PoC 均按 V2-S004 产生 pass/fail 证据；
- Windows TSF 原样上游闭环和 Host 崩溃隔离至少在 x64 完成；
- Engine Protocol V2 的跨平台实现分别映射并通过
  `contracts/vectors/input_foundation_v2.json` 中 `ENG2-V001..ENG2-V008`；
- OTP 内存态模型映射并通过同一文件中的 `OTP-V001..OTP-V010`，覆盖 TTL、单次消费、防重放、
  缺少配对/E2EE 时拒绝跨设备发送和无持久化审查；
- 任何跨设备 OTP Beta 另须通过 v2.5 的显式配对、E2EE、认证 envelope、撤销与重放门禁；
- 第三方二进制、schema、dictionary、submodule 与 installer 许可证来源已批准；
- Owner 明确接受对应 production slice。
