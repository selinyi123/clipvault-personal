# ADR-0001: 桌面是主节点

状态：Accepted（2026-06-12）

## 背景
需要决定分类、Obsidian 入库、GitHub 备份在哪个节点执行。

## 决策
Windows 桌面是唯一主节点：分类、入库、备份、同步服务端、Memory 主存全部在桌面。Android 只做采集与消费。

## 理由
- 只有桌面能稳定后台监听剪切板（Android 10+ 禁止）。
- Obsidian Vault 与 git 工作副本天然在桌面文件系统上。
- 单 hub 星型拓扑让同步协议保持平凡（事件日志复制，见 ADR-0002）。

## 后果
- 桌面离线时，Android 采集进 outbox 排队，Obsidian/备份延迟到桌面恢复。可接受：自用场景桌面在线时间长。
- Android 永不直接写 Vault、永不直接 push GitHub（即使技术可行也禁止，避免双主）。
