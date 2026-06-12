# RUNBOOK — 备份仓库密钥泄漏清除（唯一允许改写 git 历史的场景）

触发条件：发现某条真实密钥已进入 GitHub 备份仓库（Secret Guard 漏网且已 push）。

## 步骤（按序执行，不跳步）

1. **先轮换密钥**。泄漏的 token/密码/私钥立即在源头作废重发。清历史不能代替轮换——GitHub 的 commit 可能已被缓存/抓取。
2. 暂停 backup worker（Web UI 关闭 backup.enabled 或停进程）。
3. 在本地备份仓库副本中定位污染行：`git log --all -S "<泄漏片段前几位>"`。
4. 用 `git filter-repo` 删除/替换污染内容（按行替换 JSONL 中对应行为 `{"purged": true, "id": "<clip_id>"}`）。
5. `git push --force` 到远端（仅此场景允许）。
6. 在 GitHub 仓库 Settings → 联系 GitHub Support 请求清除悬挂 commit 缓存（cached views）。
7. 本地 SQLite：将该 clip 标记 `is_secret=1`（重新隔离），`backed_up_at` 置空。
8. 若该内容也写入了 Obsidian：删除对应 .md，检查 Vault 的同步盘/git 历史是否需要同样清除。
9. 在 docs/HANDOFF.md 的 Architect Decisions Log 记录：日期、规则缺口、已补的 SG 规则 ID。
10. 给 `contracts/vectors/secret_guard.json` 增加该模式的用例，恢复 backup worker。

## 事后必做

- 漏网的模式必须固化为新 SG 规则或新向量用例，否则视为事故未关闭。
