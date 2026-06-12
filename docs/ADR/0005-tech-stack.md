# ADR-0005: 技术栈 — Desktop Python，Android Kotlin

状态：Accepted（2026-06-12）

## 决策
- Desktop：Python 3.12 + FastAPI + uvicorn + SQLite(FTS5, WAL) + pywin32（剪切板监听）+ git CLI（subprocess）。Web UI 用原生 JS/htmx，不引前端框架。
- Android：Kotlin + Jetpack Compose + Room + OkHttp WebSocket + WorkManager + InputMethodService + Keystore。
- 包管理：桌面 uv + pyproject.toml；Android 标准 Gradle。

## 备选与否决
| 备选 | 否决理由 |
|---|---|
| C#/.NET 桌面 | 剪切板 API 更原生，但用户与 Builder 的 Python 生产力更高；性能预算（<500 clip/天）远用不到 .NET 的优势 |
| Rust + Tauri | 自用工具不值得这个复杂度 |
| Flutter 双端 | IME（InputMethodService）必须原生 Kotlin，跨端框架反而碍事 |
| Electron UI | 一个本地 Web 页够了 |

## 已知风险与对策
- pywin32 消息泵脆弱 → watcher 内置 500ms 轮询降级（CFG-1）。
- Python 进程常驻 → 计划任务开机自启 + 单实例锁；打包（PyInstaller）推迟到 P2，自用直接 venv 运行。
