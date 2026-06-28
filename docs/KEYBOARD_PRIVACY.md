# ClipVault Personal — 键盘隐私规格（KEYBOARD_PRIVACY）

> 状态：v2.0 阶段冻结（不变式与可验证断言）。本文把 [ADR-0008](ADR/0008-v1-as-runtime.md) 的
> **L0–L4 分层**与 **P4/P5/P8 原则**操作化为键盘主线必须遵守、且尽量可测的隐私规则。
> 接口契约见 [CONTRACTS_KEYBOARD.md](CONTRACTS_KEYBOARD.md)；验收门禁见 [GATES.md](GATES.md)。
>
> 一句话（ADR-0008）：**键入用于输入；显式保存才成为 ClipVault 资产。** “看”≠“存/传/学”。

## 1. L0–L4 分层（操作化）🔒

| 层 | 内容 | 允许 | 禁止 | 默认 |
|---|---|---|---|---|
| L0 | Key event（按键） | 喂给引擎产候选（KBD-1） | 持久化、落 Room、发网络 | 仅当前输入，过即弃 |
| L1 | Composing text（组字） | 内存态显示候选 | 写历史、写学习库 | 提交/取消即清 |
| L2 | Committed text（上屏） | `commitText` 写入目标框 | 自动入历史、自动同步 | 即时本地学习**可选**（见 §3），不自动入历史 |
| L3 | Explicit saved（显式保存） | 经 `saveExplicit` 进 Runtime（DB-1） | 未经用户显式动作写入 | 仅用户显式点击触发 |
| L4 | Synced / backed（同步/备份） | 过 SG-1 后进 outbox / GitHub | 密钥进同步/备份/Obsidian | 显式保存后再过 Secret Guard |

不变式：
- **L0/L1 永不持久化**：普通键入不产生 Room 行、不产生同步事件、不写日志正文（G6）。
- **L2→L3 只能由显式动作跨越**：不存在自动把上屏文本写入历史的路径（范围刹车）。
- **L4 受 SG-1 守卫**：is_secret 项不进 outbox/备份/Obsidian/Memory 提升（沿用 P5 / SG-1，不放宽）。

## 2. 敏感上下文：禁候选、禁学习、禁同步、禁 AI 🔒

以下任一成立时，键盘进入"只输入、不记忆"模式：

| 触发 | 判定 | 行为 |
|---|---|---|
| 密码/敏感输入域 | `EditorInfo.inputType` 含 password 变体，或 `IME_FLAG_NO_PERSONALIZED_LEARNING`（incognito，API 26+，minSdk 26） | 不展示 ClipVault 候选（KBD-4）、不学习（L2 学习关）、不保存、不同步 |
| Secret Guard 命中 | SG-1 判定 is_secret | 该内容禁候选、禁学习、禁同步、禁 AI（P5） |
| 敏感 App | ⏳ 名单与匹配规则随 v2.3 学习阶段冻结 | 同上（占位，不在此盲想具体名单） |

现状：incognito 抑制已实现（研究 R2 / `PrivacyAwareFilter`，host-JVM 已测）；本文将其确立为冻结契约。

## 3. 本地学习（L2，v2.3 目标）— 边界先冻，细节后定

- ✅ 冻结边界：本地学习**只**存可解释统计事件（词频/短语/Prompt/命令/场景/最近，沿用 CandidateMixer 输入），
  **绝不**存普通键入正文（无 raw committed text 字段）。学习全本地、可关、可清除，不外传（P4/P6）。
- ⏳ 具体事件 schema、半衰期、权重在 v2.3 slice 开工时冻结（不在此发明）。

## 4. AI / 云（v2.4 / v3.0）— 红线

- AI **可关、默认关、显式触发优先**（P8）；默认不把输入上下文发给云端 AI。
- 云只能是**端到端加密中继**（P6）：云看不到明文、不分类、不索引、不做事实源。
- 威胁模型先于实现：v2.4 需 ADR-0012（cloud-relay-threat-model）落地后才动代码。

## 5. 可验证断言（host-JVM 🟢，Builder 不自我验收）

下列可在 Android host-JVM 单测断言（无需真机），构成 v2.0 隐私门禁的自动化部分：

1. 给定密码 `EditorInfo` → CandidateMixer 输出**无** ClipVault 候选，且**不**发出学习事件。
2. 给定 `IME_FLAG_NO_PERSONALIZED_LEARNING` → 同上（incognito 抑制）。
3. 给定一串普通 L0/L1 输入后取消 → Room 无新增行、无同步事件（无键入痕迹）。
4. 给定 SG-1 命中内容 → 不进候选、不进 outbox（复用既有 SG-1/同步测试）。

需真机/人工（🔵，交 Owner）：真实密码框/敏感 App 的域识别、真机上屏与切换体验。

## 6. 不做（范围刹车，违反即范围外）
自动上传普通键入、自动保存所有上屏文本、行为画像、IME 内分析 SDK、云端明文索引、
未经显式 ADR 的 typed-text 学习。（与 ROADMAP_V2_KEYBOARD / ADR-0008 范围刹车一致。）
