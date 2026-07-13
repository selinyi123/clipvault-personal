# ClipVault Personal — 安装与运行（桌面端 v1）

桌面端是主节点。纯 Python，**零运行时依赖**（标准库 + ctypes）。

## 1. 环境要求

- Windows 10/11
- Python 3.11+（`python --version`）
- Git（`git --version`，用于 GitHub 备份）
- 测试需要 pytest（仅开发）

## 2. 获取与运行

```powershell
cd "D:\AI\CLAUDE CODE\Work Program\ClipVault\desktop"
python -m venv .venv
# 首次运行会生成 config.toml 模板并退出（提示填 vault_path）
.\.venv\Scripts\python -m clipvault.main --config config.toml
```

编辑生成的 `config.toml`，至少填写 `obsidian.vault_path`，再次运行即开始监听剪切板。
浏览器打开 **http://127.0.0.1:8787/** 管理历史、搜索、隔离区、词库、配对。

手动采集当前剪切板一次（不常驻）：

```powershell
.\.venv\Scripts\python -m clipvault.main --config config.toml --once
```

## 3. config.toml 要点（CFG-1）

```toml
[device]
device_id   = ""            # 留空首次自动生成并回写
device_name = "desktop-main"

[storage]
db_path        = "data/clipvault.db"
max_clip_bytes = 1048576

[obsidian]
vault_path = "D:/Obsidian/Vault"   # 必填

[backup]
repo_path        = "D:/clipvault-backup"  # 已 git init 且配好私有 remote
interval_minutes = 15
enabled          = false           # 准备好备份仓库后改 true

[server]
host = "0.0.0.0"   # 双端同步需 LAN 可达；管理 API 仍仅 127.0.0.1
port = 8787

[suggest]
half_life_days = 14
```

非法值会 fail fast（指出具体字段并退出码 2）。config 容忍 UTF-8 BOM。

## 4. GitHub 备份仓库准备

备份只存 JSONL（无损事实源），永不 pull/force/rebase。

```powershell
# 1) 在 GitHub 建一个【私有】仓库，例如 clipvault-backup
# 2) 本地准备工作副本
mkdir D:\clipvault-backup; cd D:\clipvault-backup
git init -b main
git remote add origin https://github.com/<you>/clipvault-backup.git
git commit --allow-empty -m "init"; git push -u origin main
# 3) config.toml: backup.repo_path 指向它，enabled = true
```

务必确认仓库为 **private**。万一密钥泄漏入备份，按 [RUNBOOK_PURGE.md](RUNBOOK_PURGE.md) 处理。

## 5. 双端配对（Android）

1. 桌面 Web UI 点「配对设备」→ 显示 8 位一次性码（5 分钟有效）。
2. Android 端输入该码完成配对，获得长期 token（桌面只存其 sha256）。
3. 之后两端通过 HTTP 事件日志同步（push/pull）。建议走 Tailscale 加密通道；
   纯 LAN 明文是已接受的残余风险（见 THREAT_MODEL.md）。

## 6. 开机自启（计划任务）

```powershell
$action  = New-ScheduledTaskAction -Execute "D:\...\desktop\.venv\Scripts\pythonw.exe" `
           -Argument "-m clipvault.main --config D:\...\desktop\config.toml" `
           -WorkingDirectory "D:\...\desktop"
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "ClipVault" -Action $action -Trigger $trigger
```

单实例锁（命名互斥量）防止重复启动。

## 7. 灾难恢复（验证备份可用）

```powershell
cd "D:\AI\CLAUDE CODE\Work Program\ClipVault"
python tools\restore.py D:\clipvault-backup D:\restored.db
# 从全部 JSONL 重建一个新库（不覆盖现有库）
```

### 7.1 数据库升级与应用回退

schema 9 首次启动会修复并重建 Desktop FTS 映射。升级前先完全停止 ClipVault，保存
SQLite 数据库及同目录的 `-wal` / `-shm` 文件（若存在）的一致性副本，并预留额外磁盘
空间。不要用旧 ClipVault EXE/Python 版本打开已经升级的原数据库：旧版本没有只读模式，
仍可能写入它不认识的新 schema。需要回退应用时，停止服务并恢复升级前的整套数据库
副本。只读诊断应针对副本使用 SQLite URI `mode=ro` 或外部只读工具。

## 8. 隐私须知

- 建议开启 BitLocker 全盘加密（SQLite 明文落盘）。
- 疑似密钥默认隔离：不进 Obsidian / GitHub / 同步 / 全文索引 / 词库；预览脱敏。
- 输入法（Android）永不记录普通键入；只在显式点击时保存。
- 日志只记 id/类型/长度/hash 前 8 位，绝不记正文。

## 9. 开发与测试

```powershell
cd desktop; .\.venv\Scripts\python -m pip install pytest
.\.venv\Scripts\python -m pytest -q     # 121+ 测试
```
