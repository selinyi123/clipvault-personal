# ClipVault Personal 会话设计归档

## 0. 总结

我们从一个“智能剪切板管理工具”的需求出发，逐步演化到：

```text
ClipVault Personal
=
个人自用的输入法级剪切板知识采集系统
```

它的最终目标不是商业化推广，而是提升个人使用体验、舒适度和效率。

最终核心：

```text
双端剪切板同步
+ Android 输入法快捷面板
+ 个人常用词/短语/Prompt/命令记忆
+ 续词/续动作推荐
+ Obsidian 自动入库
+ GitHub 私有备份
+ Secret Guard
```

---

## 1. 最初需求确定

最初需求：

```text
我想要一个剪切板管理工具，
可以根据剪切板内容智能分类管理，
并连接到我的知识库中自动上传，
以及我的 GitHub 仓库中备份。
```

最初提炼出的功能：

| 功能 | 说明 |
|---|---|
| 剪切板捕获 | 自动监听文本、代码、链接、图片、路径、命令、日志、Prompt |
| 智能分类 | 判断内容类型、用途、来源、敏感性 |
| 知识库上传 | 写入 Obsidian Markdown Vault |
| GitHub 备份 | 私有仓库批量备份 |
| 本地存储 | SQLite 保存历史 |
| 搜索 | 全文搜索、标签搜索、分类搜索 |
| Secret Guard | 检测 Token、密码、Cookie、私钥等 |
| 可扩展 | 后续接入 Android、输入法、AI、OCR、RAG |

最早产品名：

```text
ClipVault / Clipboard Intelligence Hub
```

---

## 2. 知识库确认为 Obsidian

用户确认知识库是 Obsidian。

因此方案收敛为：

```text
Obsidian-first Clipboard Knowledge Manager
```

Obsidian 目录建议：

```text
Obsidian Vault/
  00_Inbox/
    Clipboard/
  01_Prompt/
  02_Code/
  03_Error_Log/
  04_Web_Link/
  05_Command/
  06_Project_Note/
  07_Architecture/
  20_Snippet/
  90_Quarantine/
```

---

## 3. 剪切板工具对比阶段

我们调研了普通剪切板工具、知识采集工具、Obsidian 工具、开发者记忆工具。

关键结论：

```text
现有工具各自解决一部分问题，但没有完整覆盖：
双端剪切板同步
+ 智能分类
+ Obsidian 自动入库
+ GitHub 私有备份
+ 输入法级调用
+ 个人常用词记忆
```

从剪切板工具中吸收的功能：

```text
历史记录
搜索
固定/收藏
多格式保存
脚本能力
内容转换
Markdown 剪藏
Obsidian 入库
代码片段管理
本地加密
敏感内容忽略
```

---

## 4. 第一次方案设计

第一次完整方案：

```text
ClipVault Sync
=
Desktop 主节点
+ Mobile 采集端
+ Obsidian 知识库
+ GitHub 备份层
```

核心架构：

```text
Clipboard Watcher
↓
Content Normalizer
↓
Secret Guard
↓
Smart Classifier
↓
SQLite Local Store
↓
Sync Router
├─ Obsidian Writer
└─ GitHub Backup
```

第一次方案解决了：

```text
桌面端剪切板管理
+ Obsidian 入库
+ GitHub 备份
```

但仍有缺口：

```text
手机端体验不足
Android 剪切板限制
双端同步不够自然
输入法级快捷入口缺失
个人常用词和续词体验缺失
```

---

## 5. 双端互通需求

用户进一步要求：

```text
我要的是双端互通的工具，
可以双端同步剪切板，
手机上也能实现我想要的那些功能。
```

因此方案升级为：

```text
ClipVault Sync
=
Windows Desktop
+ Android Mobile
+ LAN/Tailscale/WebSocket 同步
+ Obsidian
+ GitHub Private Backup
```

手机 → 电脑流程：

```text
手机复制/分享
↓
ClipVault Android
↓
加密同步到 Desktop
↓
Desktop 分类
↓
Obsidian 入库
↓
GitHub 备份
```

电脑 → 手机流程：

```text
电脑复制
↓
Desktop 捕获
↓
分类/保存
↓
同步到 Android
↓
手机端/输入法面板一键粘贴
```

---

## 6. 输入法路线提出

由于 Android 10 之后对普通后台剪切板访问严格，用户提出从输入法角度寻找思路。

输入法路线核心判断：

```text
输入法更贴近输入框；
输入法可以提供剪切板面板；
输入法可以一键粘贴历史；
输入法可以承载常用片段、Prompt、命令；
自用版本可以更早使用输入法路线。
```

形成新模块：

```text
ClipVault Keyboard Lite
```

其定位：

```text
不是完整输入法；
而是输入法级剪切板入口和知识采集面板。
```

---

## 7. 输入法工具对比阶段

我们对比了闭源/商业输入法和开源输入法。

关键参考：

```text
SwiftKey：Android ↔ Windows 剪切板同步
Gboard：键盘剪切板面板和智能候选
微信输入法：系统匹配度、常用语、剪贴板、跨设备复制粘贴、词库同步
豆包输入法：AI 表达辅助、长文本、上下文联想
TextExpander：Snippet 和模板
Phraseboard：分类短语
RIME/Trime：YAML 配置驱动
Fcitx5：插件化输入法框架
HeliBoard/Simple Keyboard：隐私和无联网权限
```

---

## 8. 豆包输入法和微信输入法启发

用户研究了豆包输入法和微信输入法，并指出它们的体验让人舒适。

分析后提炼：

| 产品 | 核心气质 | 启发 |
|---|---|---|
| 豆包输入法 | AI-first | 输入法是智能表达辅助器 |
| 微信输入法 | Chat-first / Ecosystem-first | 输入法是聊天效率和生态入口 |

豆包启发：

```text
输入法不只是输入字符，
而是对当前文本提供下一步处理动作。
```

微信启发：

```text
真正舒服的输入法不是功能多，
而是知道你下一步大概率想干什么。
```

---

## 9. 自用版转向

用户明确：

```text
如果这个工具是我自己自用的，我不会商业化推广。
```

因此方案大幅简化。

不再优先考虑：

```text
账号系统
支付系统
商业官网
上架审核
多用户隔离
复杂云服务
公众隐私说服
iOS 首发
```

自用版优先：

```text
稳定
顺手
少打字
少切换
少选择
少重复输入
自动 Obsidian 入库
自动 GitHub 私有备份
Android 输入法快捷调用
```

产品名收敛：

```text
ClipVault Personal
```

---

## 10. 微信输入法优点最终吸收

用户指出微信输入法最好的体验：

```text
系统匹配度
续词推理
常用词以及关键信息的记忆
```

因此 ClipVault 从“剪切板知识工具”进一步升级为：

```text
个人输入体验增强系统
```

新增核心模块：

```text
Personal Memory Layer
Suggestion Engine
Context Action Engine
Keyboard Personal
```

---

## 11. 最终定位

```text
ClipVault Personal
=
个人自用的输入法级剪切板知识采集系统
```

英文：

```text
Personal Input-Aware Clipboard Knowledge System
```

最终一句话：

```text
ClipVault Personal 是一个专为个人工作流设计的双端剪切板、输入法片段、个人词库、Obsidian 入库和 GitHub 备份系统。
```

---

## 12. Fable Architect + Codex Builder 工作流

用户提供了一套方法：

```text
Claude Fable 5 = Architect
Codex / GPT 5.5 Codex = Builder
Repo docs = Memory
Human = Final Judge
```

我们将其改造成 ClipVault 专用流程：

```text
Fable 管方向、架构、体验、裁决；
Codex 管实现、测试、提交；
Repo docs 管记忆；
用户管最终拍板。
```

关键文件：

```text
docs/HANDOFF.md
docs/PRODUCT_SPEC.md
docs/ARCHITECTURE.md
docs/CONTRACTS.md
docs/GATES.md
docs/PROMPTS/ARCHITECT_FABLE5.md
docs/PROMPTS/BUILDER_CODEX_GOAL.md
```

核心规则：

```text
不在 repo docs 里的东西 = 没发生。
Builder 不自我验收。
Architect 不写实现代码。
分歧必须显式记录。
验收标准先冻结，结果后判断。
```

---

## 13. 最终结论

整个演化路径：

```text
最初：
智能剪切板管理工具

第一次升级：
双端剪切板同步 + Obsidian 入库 + GitHub 备份

第二次升级：
加入 Android 输入法路线，解决移动端操作效率问题

第三次升级：
吸收豆包输入法的 AI 联想、长输入、语义辅助

第四次升级：
吸收微信输入法的系统匹配度、常用语、剪贴板、跨设备复制粘贴、词库记忆

第五次升级：
自用版优先，转向舒适度、方便性、个人记忆和续词推荐

最终：
ClipVault Personal
=
个人输入体验增强系统
```
