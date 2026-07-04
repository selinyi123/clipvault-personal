# ClipVault Personal — 威胁模型（THREAT_MODEL）

> 状态：v1.0 冻结（2026-06-12）。这是 Secret Guard 与隐私门禁的依据文件。
> 自用系统的威胁模型不追求企业级完备，只覆盖"真实会发生、且后果不可逆"的场景。

## 1. 资产

| 资产 | 价值 | 最坏后果 |
|---|---|---|
| 剪切板里的密钥（token/私钥/密码） | 极高 | 进入 GitHub/Obsidian 后永久泄漏，需轮换全部凭据 |
| 个人知识语料（prompt、笔记、命令史） | 高 | 隐私泄漏 |
| 输入法可见的按键流 | 极高 | 等同键盘记录器 |
| 配对 token | 中 | 持有者可读写 clip 流 |
| 备份仓库 | 高 | 全量语料泄漏 |

## 2. 信任边界

```text
[Win 桌面进程] --(localhost REST)-- [本机浏览器 Web UI]     边界1：本机
[Win 桌面进程] --(HTTP push/pull over LAN/Tailscale)-- [Android App]    边界2：网络
[Android App] --(同进程/Room)-- [IME 键盘]                  边界3：IME 隐私
[桌面进程] --(文件系统)-- [Obsidian Vault]                   边界4：Vault 可能被第三方同步盘上传
[桌面进程] --(git push)-- [GitHub 私库]                      边界5：出境，不可逆
```

## 3. 主要威胁与对策

| # | 威胁 | 对策 |
|---|---|---|
| T1 | 密钥被自动备份到 GitHub | 三道闸门（§4）；备份仓库必须 private；RUNBOOK_PURGE.md 兜底 |
| T2 | 密钥进入 Obsidian → 被用户的同步盘上云 | 闸门 A/B；密钥不写 Vault |
| T3 | 密钥进入 FTS 索引 / 日志 / 预览 | FTS 排除不变量（DB-1）；日志永不打正文；预览脱敏 |
| T4 | IME 变成键盘记录器 | 架构禁令：ime/ 模块无网络依赖、无按键持久化路径；门禁逐版本人工复查 |
| T5 | LAN 上恶意设备连接同步端口 | 配对 token（哈希存储）+ auth 失败即断；推荐 Tailscale 加密通道 |
| T6 | 手机丢失 | token 存 Android Keystore；桌面 Web UI 一键解除配对使 token 失效 |
| T7 | 备份仓库误设为 public | 安装文档强制检查；backup worker 启动时调 `gh repo view --json visibility` 校验（失败仅告警不阻断，离线可用优先） |
| T8 | 同步对端实现 bug 把密钥 clip/memory 发过来 | 接收端独立再过 Secret Guard；clip 本地隔离，memory 安全拒绝，均记内容安全的 ERROR |
| T9 | 误判放行（false negative） | 熵启发兜底 + 规则集可独立升级 + 闸门 C 用"当时最新规则"复扫 |
| T10 | 误判拦截（false positive）烦人 | suspect 级别 + 一键释放流程，释放留审计字段 |

## 4. Secret Guard 三道闸门（ADR-0006）

```text
闸门 A（捕获）：clip ingest / memory upsert 落库前判定 secret → 决定 FTS/派生/候选是否发生
闸门 B（出口）：sync outbox 入队、obsidian 写入、backup_queue 入队统一拒绝 secret 内容
闸门 C（备份序列化）：backup worker 写 JSONL 前用当前规则集对 content 复扫，命中即丢弃
```

三道闸门**独立实现、独立测试**。任何一道的失效不应导致泄漏（防御纵深）。

## 5. 已接受的残余风险（显式记录，不装看不见）

| 风险 | 接受理由 | 缓解 |
|---|---|---|
| SQLite 明文落盘 | 自用单机，全盘加密更合适 | 文档建议开 BitLocker / Android 默认 FBE |
| 纯 LAN 模式 HTTP 明文 | 自用家庭/LAN + Android 端为当前 LAN 同步显式允许 cleartext；推荐走 Tailscale | 配对 token；P2 提供自签 TLS + 钉扎 |
| Secret Guard 永远有漏网 | 模式匹配的本质局限 | 三道闸门 + 熵兜底 + purge runbook |
| 释放流程被误点 | 单用户，自己负责 | 释放留审计字段，UI 二次确认 |

## 6. 给 Builder 的硬性安全规则

1. ime/ 模块禁止 import 任何网络库；禁止把 composing text 写入任何存储。
2. 日志只允许 clip 的 id、hash 前 8 位、长度、类型，禁止正文。
3. 任何新增"内容离开本设备"的代码路径，必须先在 HANDOFF 提 disagreement。
4. 测试中使用的"密钥样本"必须是明显伪造的（如 `AKIAIOSFODNN7EXAMPLE`）。
