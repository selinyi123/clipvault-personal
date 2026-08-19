# ClipVault Personal — 产品规格（PRODUCT_SPEC）

> 状态：v1.0 行为冻结；v2 输入中枢演进于 2026-08-01 经 Owner 授权启动。
> 本文件是产品层面的事实源。功能取舍以本文件为准；技术实现以 ARCHITECTURE.md 与 CONTRACTS.md 为准。

## 1. 定位

**中文**：个人自用的跨端输入中枢。
**英文**：Personal Cross-device Input Hub.

一句话：ClipVault Personal 以 Android 与 Windows 原生输入入口连接中文输入、双端剪切板、
Personal Memory、临时 OTP Relay、Obsidian 入库和 GitHub 备份；v1 Runtime 继续作为数据基础。

**非商业。** 永远只服务一个用户。所有设计决策以"自用舒适度"为最高优先级，而不是功能完整度、可扩展性或市场竞争力。

## 2. 核心目标

```text
双端剪切板同步
+ Android 主输入法 + Windows TSF 输入法
+ 个人常用词/短语/Prompt/命令记忆
+ librime 中文输入 + 本地候选增强
+ 一次性 OTP 临时中继
+ Obsidian 自动入库
+ GitHub 私有备份
+ Secret Guard
```

## 3. 核心原则（按优先级排序，冲突时上面的赢）

> **2026-06-19 演进（ADR-0008）**：v1 升级为 **ClipVault Runtime**；原则 P2/P7 演进——
> 允许输入法成为主入口、处理普通键入（但"键入用于输入，显式保存才成为资产"，分 L0–L4 隐私层）。
> v1 行为不回退；详见 [ADR-0008](ADR/0008-v1-as-runtime.md) 与 [ROADMAP_V2_KEYBOARD](ROADMAP_V2_KEYBOARD.md)。
> 下表为 v1 冻结原则，仍是当前已实现版本的事实源。

| # | 原则 | 含义 |
|---|---|---|
| P1 | 密钥不出设备 | 疑似密钥默认隔离：不进 Obsidian、不进 GitHub、不进同步、不进词库、不进全文索引 |
| P2 | 输入法不记录普通键入 | （v1）IME 只在用户显式点击时写入数据，永不记录按键流。（v2 演进：处理普通键入但默认不存/不传/不学，见 ADR-0008 L0–L4） |
| P3 | 本地优先 | SQLite 是事实源；断网时一切核心功能可用 |
| P4 | 桌面是主节点 | 分类、入库、备份只在桌面执行；Android 是采集端和消费端 |
| P5 | Obsidian-first | Obsidian 是唯一主知识库 |
| P6 | GitHub 只做备份 | 批量 commit + 定时 push；永远不是同步通道 |
| P7 | 输入法是知识面板 | （v1）不做拼音引擎。（v2 演进：允许成为主输入法入口，接 Rime/Fcitx5，见 ADR-0008） |
| P8 | 自用舒适度 > 商业完整度 | 少打字、少切换、少选择、少重复输入、少手动归档 |
| P9 | 输入与 Runtime 故障隔离 | Android IME 与 Windows TSF 按键路径不等待网络、Python、数据库或同步；Runtime 故障不阻断基本输入 |

## 4. 角色与终态体验

终态（v1.0）应该达到：

```text
你刚输入两个字，它知道你大概率要说什么；
你刚复制一段内容，它知道你大概率要保存到哪里；
你刚打开输入法，它知道你最近最可能要粘贴什么；
你刚开始写项目规划，它知道你的常用结构、Prompt、命令和路径。
```

## 5. 功能清单（按子系统）

### 5.1 桌面端（Windows，主节点）

- 剪切板自动监听（文本；图片为 P2）
- 内容规范化、去重（content_hash）
- 规则分类：text / url / path / command / code / error_log / prompt
- Secret Guard（捕获即检测，三道闸门，见 THREAT_MODEL.md）
- SQLite + FTS5 全文搜索（密钥内容不进索引）
- Obsidian Markdown 自动写入（幂等、原子写）
- GitHub 私有仓库 JSONL 批量备份
- 本地 Web UI：历史、搜索、收藏/固定、隔离区管理、同步与备份状态
- 局域网/Tailscale HTTP push-pull 同步服务端 + 设备配对
- Personal Memory：词条/短语/Prompt/命令/关键信息，使用频次统计
- Suggestion Engine：前缀匹配 + 频率 + 时间衰减 + 上下文加权（确定性算法，无 ML）

### 5.2 Android App（采集端）

- 分享到 ClipVault（Share Target，**主采集路径**）
- 手动保存当前剪切板（应用内按钮 / Quick Settings Tile / 通知动作）
- 最近历史浏览与搜索（本地 Room 缓存）
- 与桌面双向同步（HTTP push-pull + 离线 outbox）
- 本地 Secret Guard（与桌面同一套规则，捕获即检测）
- 配对管理、同步状态

> **平台约束（写死）**：Android 10+ 禁止后台读剪切板。只有前台应用或当前默认输入法能读。
> 因此 Android 端**不存在**自动剪切板监听；采集路径只有：分享、手动保存、输入法面板内保存。

### 5.3 ClipVault Android IME

v2.0 双入口是迁移结构：Panel 保留既有能力，Full Keyboard Lab 用于验证主输入法生命周期。
正式目标是单一主 IME，Panel 演进为键盘内部工具页，而不是长期维护两个并列输入法入口。

- librime 负责中文解码，不自研拼音分词、长句解码和基础用户词频；
- 英文、数字、符号、网址、密码、多行和物理键盘具备可日用输入路径；
- 系统 Inline Autofill、Rime 候选和 ClipVault 工具栏首版分层展示；
- ClipVault 工具栏提供最近剪切板、同步内容、Personal Memory、模板与临时 OTP；
- IME 不记录普通按键/组字/完整上屏正文，按键关键路径不访问网络；
- 最终权限目标是无 INTERNET/SMS 权限的 IME APK，通过签名级本地 IPC 访问 Companion Runtime。

### 5.4 Personal Memory Layer

- 类型：term（词）、phrase（短语）、prompt、command、key_info（关键信息）、path（Obsidian 路径等）
- 来源：手动添加、从高频 clip 提升、Obsidian 标题导入、GitHub 仓库名导入
- 手动固定（pinned 永远置顶）
- 使用频率 + 最近使用加权

### 5.5 Suggestion Engine

- 输入前缀 → 推荐 memory 项与高频 clip
- 评分 = 固定加权 + 前缀匹配 + 频率（对数）× 时间衰减 + 当前 App 匹配
- **推荐只读本地缓存，绝不按键发网络请求**：v2.0 单包兼容面经 `ClipVaultFacade` 查 Room；
  ADR-0013 双包目标由 Companion Runtime 持有 Room，IME 只读签名级 Binder 的有界过滤快照。
- 确定性、可解释、权重可在配置中调

### 5.6 Context Action Engine（v0.7，规则版）

复制内容后给出"下一步动作"建议（纯规则，无 AI）：

| 内容类型 | 动作 |
|---|---|
| url | 保存链接 / 入库 |
| code | 保存代码片段 / 入库 |
| command | 保存为常用命令 |
| prompt | 归档为 Prompt / 版本化 |
| error_log | 保存错误记录 |
| secret | 隔离（自动，不提供其他动作） |

AI 增强（摘要、解释、改写）全部属于 P2，且只能由用户显式触发。

### 5.7 Windows 原生输入法

- 使用 TSF，而不是剪切板模拟、全局键盘 Hook 或把桌面弹窗冒充系统输入法；
- 原生 TSF DLL 只承载 COM/编辑会话/组字/候选 UI，通过每用户本地 IPC 连接外置 librime Host；
- 现有 Python Desktop Runtime 保留剪切板、Memory、同步、设置和诊断职责，只异步发布本地候选快照；
- x86/x64、ARM64 发行设计、宿主进程隔离、DPI、安装/升级/卸载与签名属于正式门禁。

### 5.8 OTP Relay

- OTP 是一次性临时凭据，不是 clip、Memory 或永久 Secret；
- 默认在线、内存态、120 秒 TTL、目标设备绑定、单次消费、ACK 后销毁；
- 不进入剪切板历史、Room/SQLite、普通 outbox、搜索、学习、Obsidian、GitHub 或内容日志；
- Android 先支持系统 Inline Autofill；明文捕获只能在独立 Companion Runtime 经明确授权实现；
- Windows 默认显示非激活式确认卡片并通过 TSF 直接插入，不盲目注入、不自动按 Enter。

## 6. 版本路线（细节见 ROADMAP.md）

| 版本 | 内容 |
|---|---|
| v0.1 | Desktop Core：监听、存储、分类、Secret Guard、Obsidian、GitHub 备份、Web UI |
| v0.2 | Android Capture：分享/手动保存/历史 |
| v0.3 | 双端同步：HTTP push-pull、配对、离线队列、去重 |
| v0.4 | Personal Memory：词库、导入、统计、memory 同步 |
| v0.5 | Keyboard Personal：IME 面板、一键粘贴 |
| v0.6 | Suggestion Engine：前缀推荐、建议栏 |
| v0.7 | Context Action（规则版） |
| v1.0 | 稳定自用版：加固、恢复工具、全量验收 |
| v2.0 | Android 双 IME 迁移入口与真机稳定证据 |
| v2.1 | Android/Windows 输入底座 PoC + Engine Protocol V2 |
| v2.2 | Android 中文日用 Beta + 分层候选面 |
| v2.3 | Windows 原生 TSF Beta |
| v2.4 | 本机/合成 OTP Relay PoC；内存核心与两端平台面分别验证 |
| v2.5 | 最小配对/E2EE 后的跨设备 OTP Beta 与通用传输硬化 |
| v3.0 | 显式语音、纠错和 AI |

## 7. 明确不做（Non-goals）

```text
商业 SaaS / 账号系统 / 支付 / 多用户
公有云同步作为首选路径
iOS（第一阶段）
从零自研拼音/长句解码引擎
广告 / 皮肤商店 / 社交
上架应用商店（自用侧载）
浏览器扩展、Obsidian 插件、OCR（后续另议）
自动保存/上传普通键入、行为画像、输入法分析 SDK
把 OTP 放进普通剪切板历史或离线补传
```

## 8. 参考产品吸收结论

- **微信输入法**：系统匹配度、续词推理、常用语、跨设备复制粘贴、词库记忆 → Personal Memory + Suggestion Engine + 双端同步
- **豆包输入法**：对当前文本提供下一步处理动作 → Context Action Engine
- **SwiftKey**：Android ↔ Windows 剪切板同步 → 同步子系统
- **TextExpander / Phraseboard**：片段与分类短语 → 面板设计
- **Raycast Clipboard**：敏感内容忽略 → Secret Guard
- 完整对比见导出包 `03/04/05` 号文档（已归档于 docs/REFERENCE/）
