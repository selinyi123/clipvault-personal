# ADR-0003: GitHub 只做备份，且只存 JSONL（不存 Markdown 镜像）

状态：Accepted（2026-06-12）。**偏离原 ChatGPT 方案**（原方案要求 JSONL + Markdown 双备份）。

## 决策
1. GitHub 私库永远只是灾难恢复备份：批量 commit、定时 push、永不 pull、永不作为同步通道。
2. 备份内容只有 `clips/YYYY/MM/YYYY-MM-DD.jsonl`（每行一个 Clip 对象）。不存独立元数据文件，也不再镜像 Markdown。
3. 备份必须使用专用私有仓库；适配器只提交显式 daily JSONL 路径，并拒绝推送历史中曾跟踪其他路径的仓库。
4. 本地 SQLite 是事实源，Git commit 是 backup queue ack 的 durable boundary；远端 push 可以重试，但不得反过来成为同步或事实确认通道。
5. 只允许自动重建 exact remote base 之后、尚未发布且可证明为 append-only 的污染后缀。已发布 ancestry 出现后来重新隔离的 clip 时，worker 必须 fail closed 并要求 Owner 处置；实现不得 force push 或自动改写远端历史。

## 理由
- JSONL 是无损事实源：Markdown 可以由 `tools/restore.py` 从 JSONL 确定性重建，反向不行（frontmatter 有损）。
- 双格式备份 = 双写一致性问题 + 仓库体积翻倍，换不来任何恢复能力。
- 用户若想备份 Vault 本身，正确做法是把 Vault 自己做成 git 仓库，与 ClipVault 无关。

## 后果
- 恢复需要跑工具而不是直接看 Markdown；v1.0 门禁强制恢复演练，保证工具真的可用。
- 若密钥事后被发现已进入已发布备份：先轮换真实凭据，再按 RUNBOOK_PURGE.md 执行历史清理，或替换/删除受污染的专用仓库并处理远端缓存。仅轮换 branch 不能消除旧对象或缓存中的泄漏。
- crash retry 以 durable commit blob 校验与 latest-state append 收敛；repo-scoped OS lock 串行化 JSONL、index/ref、恢复和 push，同时避免把 SQLite writer lock 持有到网络阶段。
- managed JSONL 只接受单链接普通文件并采用同目录原子替换；本地 index/worktree 只在 exact HEAD 可证明所有权时机械修复，人工 rewrite 保持 fail closed。
