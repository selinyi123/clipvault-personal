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
| v2.1 | 底座 Spike | 裁决 Trime / Fcitx5 Android / Rime 长期方案（输出 ADR-0010） |
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
- PR3（android）：`ClipVaultKeyboardService` → `ClipVaultPanelImeService`；`ime_config` → `ime_panel_config`；Manifest 更新。
- PR4（android）：新增第二个 InputMethodService（Full Keyboard Lab 空壳）：基础英文键盘 + ClipVault 工具栏占位，不接中文引擎。
- PR5（docs）：键盘底座 Spike 计划（Trime/Fcitx5 checklist + RimeAdapter 目标 + license/build/integration 评分表）。

## 待写文档（对应阶段开工时再写，不一次性产出）

```text
docs/ADR/0009-sync-transport-abstraction.md     (v1.2)
docs/ADR/0010-keyboard-base-selection.md        (v2.1)
docs/ADR/0011-input-context-privacy.md          (v2.0)
docs/ADR/0012-cloud-relay-threat-model.md       (v2.4)
docs/CONTRACTS_KEYBOARD.md                        (v2.0)
docs/CONTRACTS_SYNC_TRANSPORT.md                  (v1.2)
docs/KEYBOARD_PRIVACY.md                          (v2.0)
docs/SLICES/V2-S00N-*.md                          (各阶段开工时)
```

## CandidateMixer 排序（v2.2 目标公式）

```text
final = engine_score + prefix + recency + frequency + pinned_boost
      + app_context_boost + remote_freshness + explicit_saved_boost
      - secret_risk_penalty - sensitive_field_penalty
```
pinned 硬置顶（沿用 SUG-1.1）；Secret 不进候选；密码框不展示 ClipVault 候选。

## 范围刹车（明确暂不做）
商业 SaaS、多用户账号、支付、插件市场、皮肤商店、云端明文索引、云端知识库、
自动上传普通键入、自动保存所有上屏文本、多人协同编辑、CRDT 笔记编辑器。
