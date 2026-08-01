# ROADMAP V2 — ClipVault Runtime → Android/Windows 主输入法 → OTP/E2EE → 智能输入

> 状态：ADR-0008 的 Runtime 方向继续有效；Owner 于 2026-08-01 授权启动跨端输入基础阶段。
> 铁律不变：先隔离验证底座、进程边界和合同，再进入 production；最后才做 AI/语音。
> 总体边界见 [NEXT_PHASE_V2_INPUT_FOUNDATION](NEXT_PHASE_V2_INPUT_FOUNDATION.md)。

## 阶段总览

| 版本 | 主题 | 一句话 |
|---|---|---|
| v1.0/1.1 | Runtime 收口 | 现有 v1 明确为 Runtime；Android 引入 ClipVaultFacade；panel IME 改走 facade |
| v1.2 | SyncTransport 抽象 | 不做云，但把 HTTP push/pull 抽象为 transport，为云预留接口 |
| v2.0 | 双 IME 入口 | 同一 APK 内：ClipVault Panel + ClipVault Keyboard Lab（基础英文键盘 + 工具栏）；稳定判据见 `STABILITY_PLAN_V2_0.md` |
| v2.1 | 跨端输入基础 | Android librime A/B build PoC + Windows TSF PoC + Engine Protocol V2；不接 production |
| v2.2 | Android 中文 Beta | Android 中文日用闭环；系统 Autofill、Rime 与 ClipVault 工具栏分层展示 |
| v2.3 | Windows TSF Beta | 薄 TSF DLL + 外置 librime Host + Python Runtime 异步候选快照 |
| v2.4 | OTP Relay 本机/合成 PoC | 内存态单次消费；分别验证 Android 授权捕获面与 Windows 显式 TSF 插入面，不跨设备传明文 |
| v2.5 | 跨设备 OTP Beta + E2EE/传输硬化 | 最小配对/E2EE 是 OTP 跨端硬门；LAN/Tailscale 优先、可选密文 Relay、配对撤销与恢复 |
| v3.0 | 智能输入 | 纠错/长句补全/Prompt 改写/语音/显式云 AI；AI 可关、显式触发 |

## 开源底座裁决（v2.1 验证，尚非 production 终裁）

| 候选 | 角色 |
|---|---|
| Rime / librime | 中文输入引擎（核心，不自研拼音） |
| Fcitx5 Android | Android B 路线/完整输入法壳候选；addon 与薄 Fork 的真实扩展边界必须分别验证 |
| Trime | Android Rime IME，最快验证 spike；GPL-3.0，长期 fork 需评估 |
| HeliBoard | UI/隐私/手感参考，不作中文引擎底座 |
| TypeDuck Windows + libIME2 | Windows 第一 TSF/外置 Host PoC；新且产品特定，必须锁版本、剥离网络/AI 并完成多架构门禁 |
| Microsoft SampleIME | Windows TSF 平台合同参考，不作产品底座 |
| Weasel | Windows Rime 行为参考；GPL 代码不混入不兼容模块 |
| Espanso | 文本扩展模型参考（trigger / app-specific config） |
| CopyQ | 剪切板动作模型参考（剪切板项可触发动作） |

## 当前最该执行的 5 个 PR（v1.1 起步）

- **PR1（docs，✅ 完成）**：ADR-0008 定义 v1 为 Runtime + 原则更新 + 本路线图。
- **PR2（android，✅ 完成）**：引入 `ClipVaultFacade`（`com.clipvault.app.runtime`）+ `RoomClipVaultFacade` +
  `ClipVaultRuntime.facade()`；Panel IME 改走 facade（listRecentClips/listMemory/saveExplicit），
  不再直接碰 Room DAO / Capture / SyncScheduler；行为不变（同样的查询与 take(40)）。编译通过、
  模拟器实测 App 启动无崩溃、IME 仍注册。注：facade 暂在 app 模块内 `runtime/` 包，独立 Gradle 模块为后续细化。
- **PR3（android，✅ 完成）**：`ClipVaultKeyboardService` → `ClipVaultPanelImeService`；
  `res/xml/ime_config` → `ime_panel_config`；Manifest 服务名/资源/label（"ClipVault 面板"）更新。git mv 保留历史。
- **PR4（android，✅ 完成）**：新增第二个 InputMethodService `ClipVaultFullKeyboardService`（Full Keyboard Lab）：
  可用英文键盘（QWERTY + 一次性 shift + ?123 符号层 + 空格/回车/退格）+ ClipVault 工具栏（"最近剪切板"经
  facade 调取并一键粘贴 + 切回键）。不接中文引擎。`res/xml/ime_full_config` + Manifest 注册（label "ClipVault 键盘(实验)"）。
- **PR5（docs，✅ 完成）**：`docs/SLICES/V2-S003-keyboard-base-spike.md`——Trime / Fcitx5 Android spike 清单 +
  `InputEngineAdapter`(RimeAdapter) 目标接口 + license/build/integration 评分表 + 预期裁决（输出 ADR-0010）。

## 文档清单（对应阶段开工时再写，不一次性产出）

```text
[done] docs/ADR/0010-keyboard-base-selection.md      (v2.1，paper spike；A/B 终裁待 build PoC)
[done] docs/CONTRACTS_KEYBOARD.md                     (v2.0，接口与不变式冻结)
[done] docs/KEYBOARD_PRIVACY.md                       (v2.0，L0–L4 操作化 + 可验断言)
[done] docs/GATES.md「Keyboard 主线门禁」            (v1.1→v3.0 验收门冻结)
[done] docs/STABILITY_PLAN_V2_0.md                    (v2.0，稳定定义、证据分层、agent 任务流)
[todo] docs/ADR/0009-sync-transport-abstraction.md   (v1.2)
[done] docs/ADR/0011-input-context-privacy.md        (v2.0，敏感上下文 session token + 候选/保存闸门)
[done] docs/SLICES/V2-S004-librime-build-poc.md      (v2.1，A/B build PoC 执行门与终裁算法)
[done] docs/ADR/0012-windows-tray-dependencies-and-lgpl-delivery.md (v1.6；该编号已占用)
[done] docs/ADR/0013-cross-platform-input-process-boundary.md      (v2.1)
[done] docs/ADR/0014-engine-protocol-v2-and-candidate-surfaces.md  (v2.1)
[done] docs/ADR/0015-windows-tsf-stack.md                          (v2.1 PoC)
[done] docs/ADR/0016-otp-relay.md                                  (v2.4 设计)
[done] docs/CONTRACTS_INPUT_ENGINE_V2.md                           (v2.1)
[done] docs/CONTRACTS_OTP_RELAY.md                                 (v2.4)
[done] docs/THREAT_MODEL_OTP_RELAY.md                              (v2.4)
[todo] docs/CONTRACTS_SYNC_TRANSPORT.md               (v1.2)
[todo] docs/SLICES/V2-S00N-*.md                       (各阶段开工时)
```

## 候选面与 CandidateMixer

v2.2 第一阶段不把所有来源交错混排：

```text
system Inline Autofill | engine/Rime candidates | ClipVault toolbar
```

ClipVault 工具栏内部继续使用确定性排序：

```text
final = engine_score + prefix + recency + frequency + pinned_boost
      + app_context_boost + remote_freshness + explicit_saved_boost
      - secret_risk_penalty - sensitive_field_penalty
```
pinned 硬置顶（沿用 SUG-1.1）；Secret 不进候选；密码框不展示 ClipVault 候选。统一跨来源混排
不再是 v2.2 必选门，需在稳定 ID、revision、学习所有权与隐私证据齐全后另作裁决（ADR-0014）。

## v2.1 当前执行节点（2026-08-01）

四条分支可以并行，但各自保持隔离：

1. Android：继续按 [V2-S004](SLICES/V2-S004-librime-build-poc.md) 执行 A/B build PoC；
2. Windows：按 ADR-0015 锁定上游、冻结 protobuf/golden frames，再做原样 TSF 闭环；
3. Engine V2：按 `CONTRACTS_INPUT_ENGINE_V2.md` 验证 session/revision/stable ID/UTF-16/restart；
4. OTP：先完成纯内存合成事件，再做 Android/Windows 平台捕获与插入。

在对应 PoC 产出许可证、可复现构建、兼容、故障隔离与固定向量前，不接 production APK、
Windows installer 或普通同步管线。并行分支建议按 foundation → OTP core → Android → Windows 顺序审查合并。

## 范围刹车（明确暂不做）
商业 SaaS、多用户账号、支付、插件市场、皮肤商店、云端明文索引、云端知识库、
自动上传普通键入、自动保存所有上屏文本、多人协同编辑、CRDT 笔记编辑器、
OTP 进入普通剪切板/离线 outbox、向任意焦点盲目注入验证码。
