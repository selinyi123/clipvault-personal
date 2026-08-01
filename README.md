# ClipVault Personal

> 个人自用的跨端输入中枢 · Personal Cross-device Input Hub

```text
双端剪切板同步 + Android/Windows 输入法主线 + 个人词库/Prompt/命令记忆
+ 临时 OTP Relay + Obsidian 自动入库 + GitHub 私有备份 + Secret Guard
```

非商业 · 单用户 · 本地优先。架构师：Claude Fable 5 ｜ 实现：Claude Fable 5（原 Codex 故障接管）｜ 最终裁决：Owner。

**状态**：当前源码版本与最新正式发布版本均为 **1.6.0**。[`v1.6.0`](https://github.com/selinyi123/clipvault-personal/releases/tag/v1.6.0)
已于 2026-07-30 发布，Issue #36 按 Owner 明确风险豁免关闭。最终人工工作表记录为
**15 pass / 0 fail / 10 blocked**；不能把未执行项描述成完整 QA 通过。

下一阶段已于 2026-08-01 启动为隔离的 v2 输入基础 PoC：Android librime、Windows TSF/外置 Host、
Engine Protocol V2 与内存态 OTP Relay。PoC 不等于 production 集成或稳定发布，详见
[v2 输入基础阶段](docs/NEXT_PHASE_V2_INPUT_FOUNDATION.md) 与 [HANDOFF](docs/HANDOFF.md)。

---

## ⬇️ 下载与安装（Releases）

到 [**Releases**](https://github.com/selinyi123/clipvault-personal/releases) 下载**最新版**安装包（以 Releases 页为准；
下表文件名取自当前最新发布 [v1.6.0](https://github.com/selinyi123/clipvault-personal/releases/tag/v1.6.0)）：

| 平台 | 文件 | 说明 |
|---|---|---|
| Windows 桌面（推荐，有图标） | `ClipVault-Setup-v1.6.0.exe` | 安装器；桌面图标 + 开始菜单 |
| Windows 桌面（便携） | `ClipVault-Desktop-v1.6.0-portable.exe` | 单文件，无需安装 Python。双击或命令行运行 |
| Android | `ClipVault-Android-v1.6.0-release-signed.apk` | 已签名侧载包（versionCode 13） |

> **Android 升级提示**：v1.6.0 使用新的签名身份，不能直接覆盖安装 v1.5.10。
> 卸载旧版前先按 [v1.6.0 Release notes](https://github.com/selinyi123/clipvault-personal/releases/tag/v1.6.0)
> 完成公开数据同步、隔离区检查与旧设备撤销。

### 桌面端

```powershell
# 便携版：首次运行生成 config.toml 模板并退出（提示填 obsidian.vault_path）
.\ClipVault-Desktop-v1.6.0-portable.exe --config config.toml
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
  desktop/      Python 桌面主节点（Windows 托盘运行时：Pillow + pystray）
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
# 桌面测试（测试工具与冻结构建环境隔离，不进入发布二进制）
cd desktop
python -m venv .venv-test
.\.venv-test\Scripts\python -m pip install "pytest>=8.0"
.\.venv-test\Scripts\python -m pytest -q               # 以当前输出为准
# Linux/CI 会根据平台能力自动跳过 Windows-only 用例；不要把旧测试数量写成发布证据。

# 桌面打包（固定 Pillow/pystray/PyInstaller 完整 wheel 闭包）
New-Item -ItemType Directory -Force packaging/wheelhouse | Out-Null
python -m pip download --require-hashes --only-binary=:all: `
  --dest packaging/wheelhouse `
  --requirement packaging/windows-release-requirements.txt
python -m venv .venv-build
.\.venv-build\Scripts\python -m pip install --no-index `
  --find-links packaging/wheelhouse --require-hashes `
  --requirement packaging/windows-release-requirements.txt
.\packaging\Export-WheelNotices.ps1 `
  -Wheelhouse packaging/wheelhouse `
  -Destination packaging/runtime-notices
.\.venv-build\Scripts\python -m PyInstaller `
  --clean --noconfirm --onefile --name clipvault `
  --hide-console hide-early `
  --icon "$PWD/packaging/clipvault.ico" `
  --hidden-import pystray._win32 `
  --workpath build_pyi `
  --specpath packaging `
  --add-data "clipvault/store/migrations;clipvault/store/migrations" `
  --add-data "clipvault/api/webui;clipvault/api/webui" `
  --add-data "$PWD/../THIRD_PARTY_NOTICES.md;." `
  --add-data "$PWD/../third_party;third_party" `
  --add-data "$PWD/packaging/runtime-notices;third_party/licenses" `
  packaging/run_clipvault.py
.\dist\clipvault.exe --self-test-tray

# v1.6.0 的正式发布流程已由 release workflow 生成并校验第九项
# ClipVault-v1.6.0-LGPL-relink-kit.zip；历史要求详见 ADR-0012 与发布 runbook。

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
