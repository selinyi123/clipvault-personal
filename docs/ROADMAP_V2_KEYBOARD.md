# ROADMAP V2 — ClipVault Runtime → 主输入法 → 云中继 → 智能输入

> 状态：方向已裁定（ADR-0008，2026-06-19）。本文是 v1 之后的分期路线。
> 铁律不变：先把 v1 改造成稳定 Runtime；再加 Keyboard Lab；再评估底座；再做候选混合；
> 再考虑云中继；最后做 AI/语音。**不先做完整输入法，不先做云。**

## 阶段总览

| 版本 | 主题 | 一句话 |
|---|---|---|
| v1.0/1.1 | Runtime 收口 | 现有 v1 明确为 Runtime；Android 引入 ClipVaultFacade；panel IME 改走 facade |
| v1.2 | SyncTransport 抽象 | 不做云，但把 HTTP push/pull 抽象为 transport，为云预留接口 |
| v2.0 | 双 IME 入口 | 同一 APK 内：ClipVault Panel + ClipVault Keyboard Lab（基础英文键盘 + 工具栏） |
| v2.1 | 底座 Spike | **paper spike 完成（ADR-0010）**：引擎=librime；待 NDK r28/16KB/许可/确定性与工程预算 **A/B 双 build PoC** 终裁 |
| v2.2 | CandidateMixer | ClipVault 内容（剪切板/词库/Prompt/命令/路径）进入候选栏 |
| v2.3 | 本地学习 | 词频/短语/Prompt/命令/场景/最近，仅存可解释统计事件，不存普通键入正文 |
| v2.4 | Cloud Relay POC | 可选端到端加密中继；云只中继密文，看不到明文 |
| v3.0 | 智能输入 | 纠错/长句补全/Prompt 改写/语音/显式云 AI；AI 可关、显式触发 |

## 开源底座裁决（v2.1 验证，预期结论）

| 候选 | 角色 |
|---|---|
| Rime / librime | 中文输入引擎（核心，不自研拼音） |
| Fcitx5 Android | 长期主输入法框架（候选提供器/工具栏/插件接入），LGPL-2.1 分发更友好 |
| Trime | Android Rime IME，最快验证 spike；GPL-3.0，长期 fork 需评估 |
| HeliBoard | UI/隐私/手感参考，不作中文引擎底座 |
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
[todo] docs/ADR/0009-sync-transport-abstraction.md   (v1.2)
[done] docs/ADR/0011-input-context-privacy.md        (v2.0，敏感上下文 session token + 候选/保存闸门)
[done] docs/SLICES/V2-S004-librime-build-poc.md      (v2.1，A/B build PoC 执行门与终裁算法)
[todo] docs/ADR/0012-cloud-relay-threat-model.md     (v2.4)
[todo] docs/CONTRACTS_SYNC_TRANSPORT.md               (v1.2)
[todo] docs/SLICES/V2-S00N-*.md                       (各阶段开工时)
```

## CandidateMixer 排序（v2.2 目标公式）

```text
final = engine_score + prefix + recency + frequency + pinned_boost
      + app_context_boost + remote_freshness + explicit_saved_boost
      - secret_risk_penalty - sensitive_field_penalty
```
pinned 硬置顶（沿用 SUG-1.1）；Secret 不进候选；密码框不展示 ClipVault 候选。

## v2.1 下一执行节点（2026-07-02 冻结）

按 [V2-S004](SLICES/V2-S004-librime-build-poc.md) 执行隔离 A/B build PoC；新增调研与来源见
[RESEARCH_V2_1_BUILD_POC_2026_07_02](RESEARCH_V2_1_BUILD_POC_2026_07_02.md)。在 PoC 产出 alignment、
license、clean-state golden vectors、reproducible metadata 与冻结预算测量前，不把 librime 接进
production IME，也不启动 v2.2；A/B 都失败时保持阻塞，不降低门禁制造结论。

## 范围刹车（明确暂不做）
商业 SaaS、多用户账号、支付、插件市场、皮肤商店、云端明文索引、云端知识库、
自动上传普通键入、自动保存所有上屏文本、多人协同编辑、CRDT 笔记编辑器。
