# ClipVault Personal

个人自用的输入法级剪切板知识采集系统（Personal Input-Aware Clipboard Knowledge System）。

```text
双端剪切板同步 + Android 输入法快捷面板 + 个人词库/Prompt/命令记忆
+ 续词推荐 + Obsidian 自动入库 + GitHub 私有备份 + Secret Guard
```

非商业，单用户。架构师：Claude Fable 5；实现：Codex；最终裁决：Human Owner。

## 文档地图（读这些就够了）

| 文件 | 回答的问题 |
|---|---|
| [docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md) | 做什么、不做什么、原则优先级 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 系统长什么样、模块怎么分、失败怎么办 |
| [docs/CONTRACTS.md](docs/CONTRACTS.md) | 所有数据结构/协议/格式的精确定义（Builder 的圣经） |
| [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) | 密钥与隐私如何被保护 |
| [docs/GATES.md](docs/GATES.md) | 每个版本怎样才算"做完了" |
| [docs/ROADMAP.md](docs/ROADMAP.md) | S001–S012 切片顺序 |
| [docs/ADR/](docs/ADR/) | 七个关键决策及理由 |
| [docs/HANDOFF.md](docs/HANDOFF.md) | 项目当前状态（repo 记忆） |
| [docs/SLICES/](docs/SLICES/) | 每片的开工规格 |
| [docs/RUNBOOK_PURGE.md](docs/RUNBOOK_PURGE.md) | 密钥泄漏入备份后的清除流程 |

## 工作流

```text
1. Builder（Codex）读 docs/，执行当前 SLICE 的 Paste Block
2. Builder 跑测试、更新 HANDOFF.md（只报原始结果，不自评）
3. Owner 把 HANDOFF + diff 交给 Architect（Fable）
4. Architect 裁决、查范围蔓延、写下一片 SLICE
5. 重复
```

铁律：不在 repo docs 里 = 没发生；Builder 不自我验收；Architect 不写实现代码；
分歧必须显式记录；验收标准先冻结、结果后判断。

## 仓库布局（目标形态）

```text
clipvault/
  desktop/      # Python：watcher、pipeline、store、obsidian、backup、sync server、API+WebUI
  android/      # Kotlin：app（采集/历史）、sync、ime（Keyboard Personal）
  contracts/
    vectors/    # 跨平台一致性测试向量（normalization/classifier/secret_guard）
  tools/        # restore.py 等
  docs/         # 本文档集（项目记忆）
```

## 现在开始

当前切片：**S001**。把 [docs/SLICES/SLICE_001.md](docs/SLICES/SLICE_001.md) 末尾的
Builder Paste Block 交给 Codex 即可开工。
