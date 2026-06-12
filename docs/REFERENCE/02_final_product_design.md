# ClipVault Personal 最终产品设计方案

## 1. 产品名称

ClipVault Personal

## 2. 中文定位

个人自用的输入法级剪切板知识采集系统。

## 3. 英文定位

Personal Input-Aware Clipboard Knowledge System.

## 4. 最终目标

```text
双端剪切板同步
+ Android 输入法快捷面板
+ 个人常用词/短语/Prompt/命令记忆
+ 续词/续动作推荐
+ Obsidian 自动入库
+ GitHub 私有备份
+ Secret Guard
```

## 5. 核心原则

```text
本地优先
桌面主节点
Android 优先
Obsidian-first
GitHub 只做备份
输入法是知识面板，不是完整拼音输入法
Secret 默认隔离
自用舒适度优先于商业完整度
```

## 6. 系统架构

```text
ClipVault Personal
  ├─ ClipVault Desktop
  │   ├─ Clipboard Watcher
  │   ├─ SQLite Store
  │   ├─ Rule Classifier
  │   ├─ Secret Guard
  │   ├─ Personal Memory Engine
  │   ├─ Suggestion Engine
  │   ├─ Context Action Engine
  │   ├─ Obsidian Writer
  │   ├─ GitHub Backup
  │   └─ Local API / WebSocket Server
  │
  ├─ ClipVault Android
  │   ├─ Share Target
  │   ├─ Save Clipboard
  │   ├─ Recent Clips
  │   ├─ Sync Client
  │   ├─ Local Cache
  │   ├─ Quick Settings Tile
  │   └─ Notification Actions
  │
  └─ ClipVault Keyboard Personal
      ├─ Suggestion Bar
      ├─ Clipboard Panel
      ├─ Synced Clips Panel
      ├─ Common Terms Panel
      ├─ Snippet Panel
      ├─ Prompt Panel
      ├─ Command Panel
      └─ Key Info Panel
```

## 7. 核心数据流

### 7.1 电脑复制 → 手机使用

```text
电脑复制内容
↓
Desktop 捕获
↓
去重
↓
Secret Guard
↓
分类
↓
SQLite 保存
↓
同步到 Android
↓
Keyboard Personal 显示
↓
手机一键粘贴
```

### 7.2 手机复制 → Obsidian 入库

```text
手机复制/分享内容
↓
ClipVault Android / Keyboard
↓
同步到 Desktop
↓
Desktop 分类
↓
Obsidian Markdown 写入
↓
GitHub 私有备份队列
```

### 7.3 输入续词

```text
用户输入前缀
↓
Suggestion Engine 查询 Personal Memory
↓
推荐常用短语/Prompt/命令
↓
用户点击
↓
一键粘贴
↓
使用权重更新
```

## 8. 最终核心功能

### 剪切板功能

```text
Windows 剪切板监听
Android 手动保存剪切板
Android 分享到 ClipVault
双端剪切板同步
历史搜索
收藏
去重
长文本预览
代码识别
URL 识别
```

### 输入法功能

```text
最近剪切板面板
电脑同步内容面板
常用词面板
常用短语面板
常用 Prompt 面板
常用命令面板
一键粘贴
一键保存剪切板
一键同步到桌面
```

### 个人记忆功能

```text
个人词库
高频短语统计
手动固定词条
Obsidian 标题导入
GitHub 仓库名导入
项目关键词管理
使用频率排序
最近使用加权
关键信息面板
```

### 续词/推荐功能

```text
前缀匹配
高频短语推荐
常用 Prompt 推荐
常用命令推荐
项目关键词推荐
Obsidian 路径推荐
根据复制内容推荐下一步动作
根据当前 App 推荐内容
根据最近上下文推荐内容
```

### Obsidian 功能

```text
自动生成 Markdown
YAML frontmatter
自动分类路径
Inbox 收件箱
Prompt / Code / Error / Link / Command 分类目录
自动标签
后续 AI 摘要
后续反向链接
```

### GitHub 功能

```text
私有仓库备份
JSONL 原始备份
Markdown 备份
index.json 索引
批量 commit
定时 push
Secret 内容禁止备份
```

### 安全功能

```text
Token 检测
Cookie 检测
SSH 私钥检测
JWT 检测
.env 检测
敏感内容隐藏预览
敏感内容不入库
敏感内容不 GitHub
输入法不记录普通键入内容
```

## 9. 技术路线

### Desktop

```text
Python
FastAPI
SQLite + FTS5
pyperclip / pywin32
watchdog
Git CLI
本地 Web UI
WebSocket Server
```

### Android

```text
Kotlin
Jetpack Compose
Room
OkHttp / Ktor WebSocket
InputMethodService
Share Target
Quick Settings Tile
Android Keystore
```

### 同步

```text
局域网优先
Tailscale / ZeroTier 可选
WebSocket
GitHub 只做备份
```

## 10. 版本路线

### v0.1 Desktop Core

```text
Windows 剪切板监听
SQLite 保存
规则分类
Secret Guard
Obsidian Markdown 写入
GitHub 批量备份
```

### v0.2 Android Capture

```text
Android App
分享到 ClipVault
保存当前剪切板
同步到 Desktop
查看最近历史
```

### v0.3 双端同步

```text
Desktop → Android
Android → Desktop
WebSocket 实时同步
离线队列
去重
同步状态
```

### v0.4 Personal Memory

```text
个人词库
常用短语
常用 Prompt
常用命令
使用频率统计
手动固定
Obsidian 标题导入
GitHub 仓库名导入
```

### v0.5 Keyboard Personal

```text
Android InputMethodService
最近剪切板
电脑同步内容
常用词
常用短语
常用 Prompt
常用命令
一键粘贴
```

### v0.6 Suggestion Engine

```text
前缀匹配
高频短语推荐
项目关键词推荐
Prompt 推荐
命令推荐
Obsidian 路径推荐
```

### v0.7 Context Action Engine

```text
复制 URL → 保存链接 / 入库 / 摘要
复制代码 → 保存代码 / 解释 / 入库
复制 Prompt → 归档 / 版本化
复制日志 → 错误分析 / 保存
```

### v1.0 稳定自用版

```text
双端剪切板同步
输入法个人面板
个人词库
续词推荐
常用 Prompt / 命令 / 模板
Obsidian 自动入库
GitHub 私有备份
Secret Guard
全文搜索
```
