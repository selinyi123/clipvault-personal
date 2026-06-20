# ADR-0008: v1 是 ClipVault Runtime（Clipboard Bus），不是"旧版"

状态：Accepted（2026-06-19，Owner 裁定）。**修订 PRODUCT_SPEC 原则 P7。**

## 背景

v1（S001–S012）把 companion IME（panel 知识面板，不做拼音、不接触普通键入）做到了
稳定可用。但 Owner 实际试用后的目标已经扩大：希望 ClipVault 成为**个人输入内容
Runtime**，输入法（含主输入法）、云同步、AI、语音都作为前端/能力接入，而不是各做一套。

## 决策

1. **v1 升级为 ClipVault Runtime / Clipboard Bus**，作为核心资产长期保留。
   现有 core / store / pipeline / sync / obsidian / backup / api 就是 Runtime 的实现。
2. 后续主输入法、云中继、AI、ASR 都**接入 Runtime**，不重写 Runtime。
3. 原则演进（覆盖部分旧原则）：

| 优先级 | 新原则 |
|---|---|
| P1 | 自用舒适度最高 |
| P2 | ClipVault Runtime 是核心资产（输入法/云/AI 都接入，不重写） |
| P3 | 本地事实源优先（SQLite / Room 仍是事实源） |
| P4 | **显式保存才成为长期记忆**：普通键入可用于输入，但默认不入库/不同步/不备份 |
| P5 | Secret 默认不出设备（命中后禁候选/同步/Obsidian/GitHub/Memory 提升） |
| P6 | 云端只能是**加密中继**（不看明文、不分类、不索引、不做事实源） |
| P7 | **输入法可以成为主入口**：允许普通键入、拼音、候选、纠错、联想（取代旧 P7"只做面板"） |
| P8 | AI 必须可关、可替换、**显式触发优先**；默认不把输入上下文发给云端 AI |

一句话：**键入用于输入；显式保存才成为 ClipVault 资产。**

## 关键澄清（语义重命名）

- 现有 `ClipVaultKeyboardService` 实际是 **panel IME**（不记录键入、无网络、显式保存才写入）。
  后续重命名为 `ClipVaultPanelImeService`，与未来的 `ClipVaultFullKeyboardService`（主输入法实验）区分。
- 两者共用一个 Runtime 接口 `ClipVaultFacade`（v1.1 引入）。

## 普通键入隐私分层（新 P7 的前提）

主输入法必须处理普通键入，但"看"不等于"存/传/学"：

| 层 | 内容 | 默认 |
|---|---|---|
| L0 | Key event | 仅当前输入，不持久化 |
| L1 | Composing text | 内存态，提交/取消即清 |
| L2 | Committed text | 可本地即时学习，不自动入历史 |
| L3 | Explicit saved | 用户显式保存后进 Runtime |
| L4 | Synced / backed | 过 Secret Guard 后才同步/备份 |

密码框/敏感 App/Secret 命中：禁候选、禁学习、禁同步、禁 AI。详见后续 docs/KEYBOARD_PRIVACY.md。

## 后果

- 旧 PRODUCT_SPEC 原则 P7（"输入法只做知识面板，不做完整输入法"）被本 ADR 取代。
  PRODUCT_SPEC.md 标注此演进，但 **v1 行为不回退**：panel IME 仍是稳定默认。
- 主输入法、云中继、AI 是 v2/v2.4/v3 的事，按 docs/ROADMAP_V2_KEYBOARD.md 分期，不一次做完。
- 范围刹车（仍禁止）：商业 SaaS、多用户账号、支付、插件市场、皮肤商店、云端明文索引、
  自动上传普通键入、自动保存所有上屏文本、多人协同、CRDT 笔记编辑器。

## 关联
[[0002-event-log-sync-not-state-sync]]（事件日志为云中继与 SyncTransport 抽象的基础）、
[[0004-keyboard-companion-ime]]（被本 ADR 扩展：companion 仍在，但不再是唯一形态）、
[[0007-deterministic-suggestions-v1]]（本地确定性排序仍是 CandidateMixer 的基线）。
