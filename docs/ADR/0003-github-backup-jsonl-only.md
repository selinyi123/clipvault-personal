# ADR-0003: GitHub 只做备份，且只存 JSONL（不存 Markdown 镜像）

状态：Accepted（2026-06-12）。**偏离原 ChatGPT 方案**（原方案要求 JSONL + Markdown 双备份）。

## 决策
1. GitHub 私库永远只是灾难恢复备份：批量 commit、定时 push、永不 pull、永不作为同步通道。
2. 备份内容只有 `clips/YYYY/MM/YYYY-MM-DD.jsonl`（每行一个 Clip 对象）。不存独立元数据文件，也不再镜像 Markdown。
3. 备份必须使用专用私有仓库；适配器只提交显式 daily JSONL 路径，并拒绝推送历史中曾跟踪其他路径的仓库。

## 理由
- JSONL 是无损事实源：Markdown 可以由 `tools/restore.py` 从 JSONL 确定性重建，反向不行（frontmatter 有损）。
- 双格式备份 = 双写一致性问题 + 仓库体积翻倍，换不来任何恢复能力。
- 用户若想备份 Vault 本身，正确做法是把 Vault 自己做成 git 仓库，与 ClipVault 无关。

## 后果
- 恢复需要跑工具而不是直接看 Markdown；v1.0 门禁强制恢复演练，保证工具真的可用。
- 若密钥事后被发现已入备份：唯一允许改写历史的场景，按 RUNBOOK_PURGE.md 执行。
