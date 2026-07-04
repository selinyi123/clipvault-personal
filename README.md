# ClipVault Personal

> 个人自用的输入法级剪切板知识采集系统 · Personal Input-Aware Clipboard Knowledge System

```text
双端剪切板同步 + Android 输入法快捷面板 + 个人词库/Prompt/命令记忆
+ 续词推荐 + Obsidian 自动入库 + GitHub 私有备份 + Secret Guard
```

非商业 · 单用户 · 本地优先。架构师：Claude Fable 5 ｜ 实现：Claude Fable 5（原 Codex 故障接管）｜ 最终裁决：Owner。

**状态**：源码树 `__version__` = **1.6.0**（2026-06-28 由 1.5.16 bump，反映累计加固），但 **v1.6.0 二进制尚未发布**。
当前 main 的自动化证据以 GitHub Actions 的当前 main CI 与 release-candidate dry run 为准；本地测试数量会随稳定化补丁变化，请以实际命令输出为准。
最新**已发布**二进制仍为 [v1.5.10](https://github.com/selinyi123/clipvault-personal/releases/tag/v1.5.10)（main 领先于它）。
v1.6 release gate（Issue #36）仍需 Owner-controlled signing secrets、signed Windows/Android artifacts、manual device QA 和最终 `v1.6.0` GitHub Release publication；完成前不得宣称 v1.6 稳定发布。
v1.7 仅作为稳定化/隐私/同步可靠性规划线推进，不绕过 v1.6 release gate（见 [docs/STABILITY_PLAN_V1_6_V1_7.md](docs/STABILITY_PLAN_V1_6_V1_7.md) 与 [docs/HANDOFF.md](docs/HANDOFF.md)）。

---

## ⬇️ 下载与安装（Releases）

到 [**Releases**](https://github.com/selinyi123/clipvault-personal/releases) 下载**最新版**安装包（以 Releases 页为准；
下表文件名取自当前最新发布 [v1.5.10](https://github.com/selinyi123/clipvault-personal/releases/tag/v1.5.10)）：

| 平台 | 文件 | 说明 |
|---|---|---|
| Windows 桌面（推荐，有图标） | `ClipVault-Setup-v1.5.10.exe` | 安装器；桌面图标 + 开始菜单，可选开机自启 |
| Windows 桌面（便携） | `ClipVault-Desktop-v1.5.10-portable.exe` | 单文件,无需安装 Python。双击或命令行运行 |
| Android | `ClipVault-Android-v1.5.10.apk` | 侧载安装。已签名（self-use 证书，versionCode 11） |

### 桌面端

```powershell
# 便携版：首次运行生成 config.toml 模板并退出（提示填 obsidian.vault_path）
.\ClipVault-Desktop-v1.5.10-portable.exe --config config.toml
# 填好 vault_path 后再次运行；浏览器打开 http://127.0.0.1:8787/
# （安装器版 ClipVault-Setup 首次启动会自动建好配置并打开面板，无需手动改）
```

详见 [docs/INSTALL.md](docs/INSTALL.md)（配置、GitHub 备份仓库、配对、开机自启、恢复、隐私）。

### Android

侧载 APK → 系统设置启用 “ClipVault” 输入法 → 桌面 Web UI 点「配对设备」拿一次性码 →
App 内填桌面 IP + 码完成配对。详见 [android/README.md](android/README.md)。

---

## 功能

- **捕获**：Windows 剪切板自动监听；Android 分享/手动/输入法显式保存（平台禁止后台读，故无轮询）
- **分类**：text / url / path / command / code / error_log / prompt（规则，确定性）
- **Secret Guard**：三道闸门，密钥不进 Obsidian / GitHub / 同步 / 全文索引 / 词库；预览脱敏
- **Obsidian**：按类型目录自动写 Markdown（原子写、幂等）
- **GitHub 备份**：JSONL 批量 commit + 定时 push；附恢复工具 `tools/restore.py`
- **本地 Web UI**：历史、全文搜索、固定/收藏/删除、隔离区释放、词库、状态、配对
- **Personal Memory**：词/短语/Prompt/命令/路径，导入与提升
- **Suggestion Engine**：前缀+频率+时间衰减（确定性，pinned 硬置顶）
- **Context Action**：按内容类型给出下一步动作（规则版，无 AI）
- **双端同步**：HTTP 事件日志复制（配对鉴权、双重幂等、字段级 LWW）
- **Keyboard Personal**：伴随式 IME，最近/词库/短语/Prompt/命令面板，一键粘贴，**永不记录按键**

## 文档地图

| 文件 | 回答的问题 |
|---|---|
| [docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md) | 做什么、不做什么、原则优先级 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 系统长什么样、模块怎么分、失败怎么办 |
| [docs/CONTRACTS.md](docs/CONTRACTS.md) | 所有数据结构/协议/格式的精确定义 |
| [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) | 密钥与隐私如何被保护 |
| [docs/GATES.md](docs/GATES.md) | 每个版本怎样才算"做完了"（含 keyboard 主线门禁） |
| [docs/ROADMAP.md](docs/ROADMAP.md) · [docs/SLICES/](docs/SLICES/) | S001–S012 切片与各片规格 |
| [docs/ROADMAP_V2_KEYBOARD.md](docs/ROADMAP_V2_KEYBOARD.md) | v2 keyboard 主线（北极星：完整输入法）分期路线 |
| [docs/CONTRACTS_KEYBOARD.md](docs/CONTRACTS_KEYBOARD.md) · [docs/KEYBOARD_PRIVACY.md](docs/KEYBOARD_PRIVACY.md) | 键盘接口契约 · L0–L4 输入隐私规格 |
| [docs/ADR/](docs/ADR/) | 关键决策及理由 |
| [docs/HANDOFF.md](docs/HANDOFF.md) | 项目当前状态（repo 记忆） |
| [docs/INSTALL.md](docs/INSTALL.md) · [docs/RUNBOOK_PURGE.md](docs/RUNBOOK_PURGE.md) | 安装运维 · 密钥泄漏清除 |

## 仓库结构

```text
clipvault/
  desktop/      Python 桌面主节点（零运行时依赖：stdlib + ctypes）
    clipvault/  core·store·pipeline·watcher·obsidian·backup·sync·api(+webui)
    tests/      pytest 回归套件（具体数量以当前命令输出为准）
    packaging/  PyInstaller 入口
  android/      Kotlin
    core/       与桌面对应的 normalize/classify/secret-guard（通过 VEC-1）
    app/        Compose·Room·Share·QSTile·Sync·IME
  contracts/vectors/  跨平台一致性测试向量（两端唯一仲裁）
  tools/        restore.py（灾难恢复）· gen_vectors.py
  docs/         设计与运维文档（项目记忆）
```

## 从源码构建

```powershell
# 桌面测试
cd desktop; python -m venv .venv; .\.venv\Scripts\python -m pip install pytest
.\.venv\Scripts\python -m pytest -q                    # 以当前输出为准
# Linux/CI 会根据平台能力自动跳过 Windows-only 用例；不要把旧测试数量写成发布证据。

# 桌面打包（单文件 exe）
.\.venv\Scripts\python -m pip install pyinstaller
.\.venv\Scripts\python -m PyInstaller --onefile --name clipvault `
  --add-data "clipvault/store/migrations;clipvault/store/migrations" `
  --add-data "clipvault/api/webui;clipvault/api/webui" packaging/run_clipvault.py

# Android（需 Android SDK）
cd android; .\gradlew :core:test            # VEC-1 跨平台一致性
.\gradlew :app:assembleDebug                # 产出 app-debug.apk
```

## 协作工作流

```text
1. Builder 读 docs/，执行当前 SLICE
2. Builder 跑测试、更新 HANDOFF.md（只报原始结果，不自评）
3. Owner 把 HANDOFF + diff 交给 Architect 裁决
4. Architect 裁决、查范围蔓延、写下一片 SLICE
```

铁律：不在 repo docs 里 = 没发生；Builder 不自我验收；Architect 不写实现代码；分歧显式记录；验收标准先冻结、结果后判断。
