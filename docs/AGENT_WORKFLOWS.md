# ClipVault Personal — Agent Workflows

本文件定义未来 coding-agent 会话的任务流。它是**工程护栏**，不是建议。
与之配套的事实源：`HANDOFF.md`（repo 记忆/当前状态）、`PRODUCT_SPEC.md`（定位+非目标）、
`GATES.md`（验收门）、`CONTRACTS*.md`（接口事实）、`ROADMAP_V2_KEYBOARD.md`（北极星主线）、
`RESEARCH_AND_ROADMAP.md`（调研台账）、`docs/ADR/`（决策记录）。

> 北极星：**做一个完整的（中文）输入法**（local-first 剪切板/词库 Runtime → librime 主输入法）。
> 一切任务以是否服务北极星与 `PRODUCT_SPEC` 核心目标为准；偏了就停。

## 核心行为准则（每轮适用）

1. 以暗猜接口为耻，以认真查阅为荣。
2. 以模糊执行为耻，以寻求确认为荣。
3. 以盲想业务为耻，以人类确认为荣。
4. 以创造接口为耻，以复用现有为荣。
5. 以跳过验证为耻，以主动测试为荣。
6. 以破坏架构为耻，以遵循规范为荣。
7. 以假装理解为耻，以诚实无知为荣。
8. 以盲目修改为耻，以谨慎重构为荣。

## 可验证性路由（本仓库环境约束，给每个任务打标签）

- 🟢 **本地可验**：桌面 Python / contracts 向量 / host-JVM 纯逻辑 → 能在 Linux 跑命令断言。**优先做这类。**
- 🟡 **CI 可验**：需 Android SDK 编译 / windows-latest → 本地编不了，靠 CI 产出佐证。
- 🔵 **设备/人工**：需真机或人工体验 → 交 Owner 验证，Builder 不代签。

🟡🔵 的工作只产出**设计 + 脚手架 + CI/设备验证清单**，并显式交 Owner 签收；不把不可本地验证的东西当"完成"。

## 人工拍板门（必须 Owner 显式批准，不在自治循环内）

- 对外/难回退动作：**切 GitHub Release、bump 版本号、任何推给用户的产物**。
- 数据库 schema / 迁移的语义变更（需 ADR + 升级路径验证）。
- 改变业务语义、架构边界、Secret/隐私策略。
- 引入新运行时依赖（违反 stdlib-only 原则，需 ADR）。
- 大规模重构（超过最小改动范围）。

## 每轮会话循环（evidence-gated）

0. **目标锁定 + 漂移检**：本轮唯一目标是什么？服务北极星吗？读 `HANDOFF.md`。
1. **只读审计**（轮换组件）：先 grep/read 相关源码，不改代码。找真问题；干净就如实记"无"，**不硬凑 fix**。
2. **针对性调研**（可选）：由步骤 1 的发现驱动 + 至多 1 个新离散方向。非重复——先查 `RESEARCH_AND_ROADMAP.md`，
   命中过的方案不重做（链接级 / 方案级 / 决策级三层去重）。北极星过滤：偏离核心目标的记为 out-of-scope，**不建造**。
3. **设计 + 先冻验收标准**：重要决策写/更 ADR；按 `GATES.md` 先定"怎样算做完"，再动手。
4. **最小实现**（优先 🟢）：一个 PR 只装一个关切；新分支，**绝不直推 main**；只改必要文件。
5. **验证**：合并前必跑 `python -m pytest -q --ignore=tests/test_watcher.py --ignore=tests/test_instance_lock.py`
   （Linux 跳过 4 个 Windows-only）；动 schema 必验升级路径；动两端逻辑必同步 contracts/vectors。
6. **开 draft PR**：填 PR 模板（目标/范围/测试证据/风险/回滚）。CI 失败先诊断再推。
7. **反馈 + 及时修**：更新 `HANDOFF.md`（repo 记忆，"不在 docs = 没发生"）。
8. **Owner 合并**：人类 gate。AI 不自合、不擅自发版。
9. **复盘**：记未完成项/技术债/下一节点；到里程碑边界暂停等 Owner review。

## Agent 角色（单 agent 会话内按角色心智切换；写代码者不自我验收）

| 角色 | 职责 | 禁止 |
|---|---|---|
| Research | 找先例/风险信号，维护调研台账 | 不写代码、不擅自决定采纳 |
| Repo Auditor | 只读审计，产出事实 | 不改代码 |
| Architect | 写 ADR、定版本方案/模块边界 | 不直接实现 |
| Patch | 按目标做最小改动，保隐私边界 | 不广泛重构、不造接口 |
| Test | 补/跑确定性测试，纯候选逻辑优先 host-JVM | 不为过测改业务语义 |
| Review | 查漂移/架构破坏/安全/重复/无效测试 | 不审自己写的代码批准合并 |
| Release | 对齐版本元数据、生成回滚说明 | 不擅自切 Release / 发版 |
| Privacy gate | 拦普通键入采集、IME 内网络、分析 SDK、隐式保存；敏感域抑制候选 | —— |

> 独立审查:本仓库的人类 gate = **Owner**（审并合每个 PR）。若需 AI 独立 Review agent（与 Patch 分离），
> 由 Owner 显式要求后再开（含并行子代理）。

## 证据要求（任务"完成"的判据）

至少满足其一，否则不算完成：

- 改动文件在 commit 后被取回并引用；
- 测试命令实际运行 + 贴出原始结果（不接受"口头解释通过"）；
- CI 状态被取回并引用；
- 阻塞点被显式记录（连同卡在哪、下一步）。

## 当前状态锚点（保持更新）

- 版本：`__version__` = **1.6.0**（2026-06-28，未对外发版；最新已发布二进制 v1.5.10）。schema 版本 = **8**。
- v1.5 gate（Issue #3）：**已关闭**（2026-06-26）。
- 桌面测试：以当前 `cd desktop; python -m pytest -q` 输出和 GitHub CI 为准；不要把旧的固定测试数量写成发布证据。
- v1.6 release gate（Issue #36）：自动化 CI/unsigned dry-run 证据持续更新；signed artifacts、Owner/manual QA、最终 GitHub Release 发布前不得关闭。
- v1.7 stable：按 `docs/STABILITY_PLAN_V1_6_V1_7.md` 的 exit criteria 推进；未有专门 release issue 与 Owner approval 前不得声称 `v1.7.0` 已发布或稳定完成。
- v1.7 field-test packages：按 `docs/V1_7_FIELD_TEST_PACKAGES.md` 使用 `Release candidate dry run` 上传双端候选安装包做实机 smoke；不得把 unsigned candidate artifacts 冒充为 signed/final release evidence。
- v2.0 stable：按 `docs/STABILITY_PLAN_V2_0.md` 的 exit criteria 推进；v2.0 是双 IME 入口稳定线，不得把 v2.1 librime 或 TLS 支线冒充为 v2.0 发布证据。
- 主线下一步：恢复当前 main CI 并完成 R000 数据一致性 hotfix → Issue #36 Owner 证据 → v2.0 双 IME 稳定证据 → v2.1 librime build PoC → ADR-0010 终裁（🟡🔵，待 Owner 与设备/CI）。
- 支线候选：v2.0 自签 TLS（受 stdlib-only 约束，需 Architect 定证书生成方式）、Android Room CJK 搜索一致性。

## 范围刹车（明确不做，违反即范围外）

商业 SaaS、多用户账号、支付、云端明文索引、自动上传普通键入、自动保存所有上屏文本、
typed-text 学习/行为画像/分析 SDK（除非先有独立隐私设计 + ADR 批准）。
