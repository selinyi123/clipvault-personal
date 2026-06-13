# Slice 002 — Desktop Service（监听 + 配置 + 服务编排）

> Architect: Claude Fable 5 | 2026-06-13 | 状态：规格冻结
> 对应版本：v0.1b。本片完成后桌面端可真实运行：复制即入库、自动写 Obsidian。

## 1. 目标

把 S001 的纯逻辑管线接上真实世界：剪切板监听、配置加载、Obsidian 写入消费、
单实例锁、日志、进程入口。

## 2. 架构决策修订（D-004，Architect 裁决）

ADR-0005 原定 pywin32 消息窗监听。**修订为：ctypes + `GetClipboardSequenceNumber`
轮询（500ms，CFG-1 可调）**，理由：
- 序号轮询无需打开剪切板，开销可忽略；500ms 远低于"捕获→落库 ≤1s"门禁；
- 砍掉 pywin32 后桌面端**零运行时依赖**（纯 stdlib + ctypes）；
- 消息窗方案的消息泵线程是 ARCHITECTURE §9 列出的脆弱点，直接消除。
原"监听失败降级轮询"条款随之失效——轮询即主路径。

## 3. 允许触碰的文件

```text
desktop/clipvault/config.py
desktop/clipvault/instance_lock.py
desktop/clipvault/service.py
desktop/clipvault/main.py
desktop/clipvault/watcher/{__init__,win_clipboard}.py
desktop/tests/**
docs/{HANDOFF,CONTRACTS}.md（按规则更新）
```

## 4. 实现要求

1. **config.py（CFG-1）**：tomllib 加载；缺文件→写默认模板并以退出码 2 提示填
   `vault_path`；非法值→具体字段报错后退出码 2（fail fast）；`device_id` 为空→
   生成并回写文件。
2. **watcher/win_clipboard.py**：`PollingWatcher(get_seq, get_text, on_text, interval)`
   ——seq 变化才读 CF_UNICODETEXT；ctypes 实现 `get_clipboard_text()` 与
   `get_foreground_app()`（进程名，作 source_app）；全部依赖可注入以便测试。
3. **service.py**：`handle_clipboard_text(text, source_app)` = ingest →
   needs_obsidian 则 write_clip + set_obsidian_path；Obsidian 失败只记日志，
   clip 保持 obsidian_path=NULL；`retry_obsidian_sweep()` 扫描
   `obsidian_path IS NULL AND is_secret=0 AND deleted=0` 重试（DB 即重试队列）。
4. **instance_lock.py**：Windows 命名互斥量（ctypes CreateMutexW），
   重复获取报已运行。
5. **main.py**：`--config` / `--once`（手动采集当前剪切板一次，作 B8 验证）/
   默认循环；logging 文件+控制台，按天轮转保留 14 天；**日志禁止 clip 正文**
   （只允许 id、hash 前 8、长度、类型）；Ctrl+C 优雅退出。

## 5. 验收门禁

- B1 config：缺文件生成模板退出 2；坏端口报字段名退出 2；device_id 回写。
- B2 公开 clip：落库 + Obsidian 文件出现 + obsidian_path 记录 + backup 入队。
- B3 密钥 clip：隔离，Vault 目录无文件。
- B4 Obsidian 失败不丢数据：clip 在库，sweep 修复后文件出现。
- B5 watcher：seq 不变不读；变则恰好捕获一次；空文本忽略。
- B6 单实例：第二次获取锁失败。
- B7 日志不含正文（caplog 断言）。
- B8（手动）：真实复制 → 1s 内 DB 出现记录（--once 或运行态验证，记录于 HANDOFF）。

## 6. 验证命令

```text
desktop> .venv\Scripts\python -m pytest -v
desktop> .venv\Scripts\python -m clipvault.main --config config.toml --once   # B8
```
