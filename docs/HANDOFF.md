# ClipVault Personal — HANDOFF（项目记忆）

本文件是 repo 记忆。不在本文件或所链接 docs 中记录的事 = 没发生。

## Current Project State

| Field | Value |
|---|---|
| Project | ClipVault Personal |
| Mode | Personal / non-commercial |
| Primary platform | Windows Desktop (Python 3.12) |
| Mobile platform | Android (Kotlin) |
| Knowledge base | Obsidian |
| Repo | github.com/selinyi123/clipvault-personal（**public** — Owner 2026-06-18 裁定保持公开；源码仓库不含个人数据，运行时备份用独立 private 仓库），Release **v1.0.0** 含双端安装包 |
| Backup | GitHub private repo (JSONL only) |
| Realtime sync | LAN / Tailscale WebSocket (SYNC-1) |
| Source of truth | SQLite local store (DB-1) |
| Current slice | **全部 12 切片完成（v1.0）。Android app 已编译并产出 APK。剩余唯一项：真机运行体验确认（Owner）** |
| Last updated | 2026-06-13 (S001–S012 完成；桌面 128 测试绿；Android core VEC-1 100/100 + app assembleDebug 成功；Builder=Claude Fable 5) |

## Product Constraints（全部 Active）

| Constraint | Status |
|---|---|
| Desktop is primary node | Active (ADR-0001) |
| Android is capture + keyboard entry; no background clipboard read | Active |
| Obsidian is primary knowledge base | Active |
| GitHub is backup (JSONL only), not realtime sync | Active (ADR-0003) |
| Keyboard is companion IME; never logs ordinary typing | Active (ADR-0004) |
| Secrets never enter Obsidian/GitHub/sync/FTS/memory | Active (ADR-0006) |
| Suggestions are deterministic in v1 | Active (ADR-0007) |
| Self-use comfort beats commercial completeness | Active |

## Completed Slices

| Slice | Commit | Files changed | Tests | Result |
|---|---|---|---|---|
| S001 Core Pipeline | 3cc4e78 | desktop/clipvault/{core,store,pipeline,obsidian}/** + tests + contracts/vectors/*.json + tools/gen_vectors.py | 32 passed / 0 failed (pytest) | **PASS**（A1–A10 全过） |
| S002 Desktop Service | 002c606 | desktop/clipvault/{config,service,instance_lock,main}.py + watcher/** + tests | 48 passed / 0 failed（累计） | **PASS**（B1–B8 全过，B8 真实剪切板验证见下） |
| S003 GitHub Backup Worker | d2a8a2a | desktop/clipvault/backup/** + backup_queue_repo/clips_repo 扩展 + main 接线 + tools/restore.py + tests | 57 passed / 0 failed（累计） | **PASS**（C1–C8 全过，含恢复演练 C6） |
| S004 Local API + Web UI | 75eabab | desktop/clipvault/api/**（server/handlers/webui）+ clips_repo/service 扩展 + main 接线 + tests | 69 passed / 0 failed（累计） | **PASS**（D1–D10 全过；live smoke 验证真实 socket 服务，修复跨线程 SQLite bug） |
| S007 Personal Memory | bfedef2 | memory_repo + memory/importers + models.MemoryItem + api 端点/路由 + webui 词库页 + service.promote_clip + tests | 81 passed / 0 failed（累计） | **PASS**（E1–E9 全过；live smoke 验证 memory CRUD/promote 路由） |
| S010 Suggestion Engine | ed594e9 | core/suggest（纯）+ config 权重 + clips_repo.suggest_candidates + /api/suggest + /api/memory/{id}/use + tests | 92 passed / 0 failed（累计） | **PASS**（F1–F10 全过；含 SUG-1.1 pinned 硬置顶；live smoke 验证 /api/suggest） |
| S011 Context Action Engine | 3d02733 | core/actions（纯规则）+ service.promote(kind) + /api/clips/{id}/actions + promote kind + webui chip + tests | 106 passed / 0 failed（累计） | **PASS**（G11-1..6 全过；纯规则无 AI） |
| S006 双端同步服务端 | 4b9a524 | sync/{pairing,engine} + outbox_repo/peers_repo + migration 0002 + /api/pair + /api/sync/push,pull + bearer 鉴权 + ingest/patch emit + webui 配对 + tests | 117 passed / 0 failed（累计） | **PASS**（H1–H10 全过；live smoke 验证 pair→push→pull 无回声；HTTP 传输 D-007/SYNC-2） |
| S012 桌面加固+文档 | （见 git log: feat: S012） | outbox.prune_acked + peers.min_my_acked + main 周期裁剪 + INSTALL.md + GATES v1.0 标注 + tests | 121 passed / 0 failed（累计） | **PASS**（I1–I4 全过；I5 文档齐；7 天稳定性为运行期观察项） |

| S005 Android Capture + Kotlin Core | （见 git log: feat: S005） | android/core（Kotlin NORM/CLS/SG + VEC 测试）+ android/app（Room/Share/QSTile/Sync/Compose）+ Gradle + README | **core VEC-1: 100/100 passed**（kotlinc 实测）；app 源码完整 | **PASS J1/J2**；J3 源码交付，真机运行验证留 Owner |
| S009 Keyboard Personal（IME 源码） | （并入 S005 提交） | ime/ClipVaultKeyboardService（companion IME 面板：最近/词库/短语/Prompt/命令，一键粘贴/保存/切回，无按键记录、无网络） | 源码完整 | 真机运行验证留 Owner（隐私不变量见代码注释） |
| S008 Memory→Android 同步 | （见 git log: feat: S008） | engine emit/apply memory_upsert/delete + handlers/importers emit + Android memory 表/DAO/apply + IME 词库面板 + tests | 128 passed / 0 failed（累计） | **PASS K1–K6,K8**（桌面侧）；K7 Android 源码完整 |

> **桌面端 v1.0 功能完整**（capture/classify/secret-guard/Obsidian/GitHub-backup/Web-UI/memory/
> suggestions/context-actions/双端同步/恢复），121 测试。**Android core 与 Python 跨平台一致性已证（VEC-1 100/100）**，
> app + IME 源码完整。剩余增量：S008（memory 同步到 Android）、Android 真机运行验证（需 SDK+设备，唯一需 Owner 的一步）。

> 注：切片顺序按"可在本机充分测试"优先重排——先做桌面/Python 侧（S007→S010→S011→S006→S012），
> Android 侧（S005/S008/S009）需工具链，置后并单独处理真机验证。

## Current Contracts

| Contract | Location | Frozen? |
|---|---|---|
| Clip object | CONTRACTS §1 | **Yes (v1)** |
| Normalization NORM-1 | CONTRACTS §2 | **Yes (v1)** |
| Classifier CLS-1 | CONTRACTS §3 | **Yes (v1)** |
| Secret Guard SG-1 | CONTRACTS §4 | **Yes (v1)** |
| Sync SYNC-1 / PAIR-1 | CONTRACTS §5 | Yes (v1)，S006 开工前允许 Builder 提修订 |
| Obsidian OBS-1 | CONTRACTS §6 | **Yes (v1)** |
| GitHub backup GHB-1 | CONTRACTS §7 | **Yes (v1)** |
| Test vectors VEC-1 | CONTRACTS §8 + contracts/vectors/ | 框架冻结；向量文件由 S001 创建 |
| SQLite DB-1 | CONTRACTS §9 | **Yes (v1)** |
| REST API-1 | CONTRACTS §10 | **Yes (v1)**，框架改 stdlib（D-006），端点不变 |
| Suggest SUG-1 | CONTRACTS §11 | Yes (v1)，S010 开工前允许提修订 |
| Config CFG-1 | CONTRACTS §12 | **Yes (v1)** |

## Open Disagreements

| ID | Raised by | Topic | Options | Status |
|---|---|---|---|---|
| D-001 | Builder | SG-ENTROPY 熵规则会误报 git hash / UUID / base64 图片头（恰为合同要求的负例，合同自相矛盾） | a) 提高熵阈值 b) 增加已知格式排除 | **RULED: MODIFY** — 采用 b，已写入 CONTRACTS §4.2 SG-1.1；阈值不动以保灵敏度 |
| D-002 | Builder | 环境无 uv | a) 安装 uv b) 改用 venv+pip | **RULED: ACCEPT b** — 验证命令改为 `.venv\Scripts\python -m pytest`；不为自用工具引入额外安装步骤 |
| D-003 | Builder | S001 白名单外新增 core/ulid.py 与 tools/gen_vectors.py | a) 引第三方 ULID 库 b) 自实现 26 行 ULID + 向量生成器入库 | **RULED: ACCEPT b** — 零运行时依赖；生成器含对实现的自校验，留库便于复现 |
| D-004 | Architect | 剪切板监听方案 | a) pywin32 消息窗（ADR-0005 原案）b) ctypes + GetClipboardSequenceNumber 500ms 轮询 | **RULED: MODIFY → b** — 零依赖、消除消息泵脆弱点，500ms 远低于 1s 门禁；已写入 SLICE_002 §2 |
| D-005 | Builder | PowerShell/Notepad 写的 config.toml 带 UTF-8 BOM，tomllib 解析失败（B8 实测发现） | a) 文档要求无 BOM b) 用 utf-8-sig 读取 | **RULED: ACCEPT b** — 自用舒适度优先，容错真实 Windows 工具链 |
| D-006 | Architect | S004 Web UI/API 框架 | a) FastAPI+uvicorn（ADR-0005 原案）b) stdlib http.server | **RULED: MODIFY → b** — 单用户 localhost，保持零运行时依赖、规避 pip 代理不稳定；API-1 端点语义不变。单线程 HTTPServer + 连接在服务线程内创建（避免跨线程 SQLite，live smoke 验证发现并修复） |
| D-007 | Architect | 同步传输（S006） | a) WebSocket（SYNC-1 原案，stdlib 无 WS 服务端）b) HTTP push/pull | **RULED: MODIFY → b（SYNC-2）** — 复用 http.server 零依赖，自用双端秒级延迟可接受；事件日志语义不变。已写入 CONTRACTS §5。绑定改为配置 host（管理路由仍 handler 层 loopback-only，sync/pair 走 token/code） |
| D-008 | Architect | SUG-1 pinned 语义：PRODUCT_SPEC 说"永远置顶"但 SUG-1 只给 +3.0 加权，极高频项可越过 | a) 维持加权 b) pinned 作硬置顶层 | **RULED: MODIFY → b** — 已写入 CONTRACTS §11 SUG-1.1；排序键 (pinned,score,last_used) |

## Raw Verification Results

| Date | Slice | Command/Test | Result | Notes |
|---|---|---|---|---|
| 2026-06-13 | S001 | `desktop> .venv\Scripts\python -m pytest -v` | **32 passed, 0 failed** (0.08s, Python 3.11.9, pytest 9.0.3) | 含 22 个 NORM、40 个 CLS、38 个 SG 向量用例；3 个 OBS-1 golden 逐字节比对；core 纯度静态检查 |
| 2026-06-13 | S001 | `python tools/gen_vectors.py`（含实现自校验） | 100 cases written, 0 mismatches | 向量为两端唯一仲裁，Kotlin 端（S005）须通过同一文件 |
| 2026-06-13 | S002 | `desktop> .venv\Scripts\python -m pytest -q` | **48 passed, 0 failed** (0.19s) | S001 32 + S002 16（config/service/watcher/lock） |
| 2026-06-13 | S002 B8 | 真实剪切板：Set-Clipboard → `main --once` ×2 | new→duplicate，times_seen=2；Obsidian 文件 frontmatter 完整；source_app 捕获为真实前台进程 | 日志仅含 id/type/len/hash8/app，无正文（G6 ✓）；缺 config 退出码 2（B1 ✓） |
| 2026-06-13 | S003 | `desktop> .venv\Scripts\python -m pytest -q` | **57 passed, 0 failed** (2.9s) | 含 C6 恢复演练（restore.py 从 JSONL 重建库，hash 集合与原库公开部分全等）；本地裸仓库验证 push，不碰真实 GitHub |
| 2026-06-13 | S004→S012（桌面累计） | `desktop> .venv\Scripts\python -m pytest -q` | **121 passed, 0 failed** | S004 API/WebUI、S007 memory、S010 suggest、S011 actions、S006 sync、S012 prune；含多处 live socket smoke |
| 2026-06-13 | S005（Android core） | `kotlinc android/core + java VectorCheckKt contracts/vectors`（JDK21/kotlin2.0.21） | **VEC-1 OK: 100 vectors passed (norm=22 cls=40 sg=38)** | Kotlin 端与 Python 端对同一向量逐例一致；跨平台契约成立 |
| 2026-06-13 | Android 整体构建 | `gradle :core:test :app:assembleDebug`（Gradle 8.10.2 + JDK21 + SDK platform-34） | **BUILD SUCCESSFUL**：core:test 1 test 0 failures；**产出 app-debug.apk (~9.2MB)** | 整个 Android app（UI/Room/Share/QSTile/Sync/IME）编译通过；core VEC-1 经 Gradle/JUnit 路径再证 |
| 2026-06-18 | Android 模拟器实测 | ATD x86_64/API34 headless 启动 → 安装 APK → 启动/IME/Share 验证 | 修 KSP/Room 后：**MainActivity 前台无崩溃；default_input_method=ClipVaultKeyboardService；Share 采集出 1 条 clip（content/hash/type/ULID/UTC 正确，isSecret=0）** | 真机级运行验证；release APK 同样启动无崩溃且 IME 可激活 |

## Architect Decisions Log

| Date | Decision |
|---|---|
| 2026-06-12 | 初始架构冻结：ADR-0001…0007；CONTRACTS v1；GATES 全版本；ROADMAP S001–S012 |
| 2026-06-13 | 发布 Release v1.0.0：Desktop exe（PyInstaller 单文件，验证独立运行）+ Android apk（签名 release，apksigner verify OK）。安装包走 GitHub Releases 不入 git（keystore/assets 在 .toolchain gitignored，未入库） |
| 2026-06-18 | 审阅裁决：①仓库可见性 **保持 PUBLIC**（Owner 定；安全扫描确认源码仓库无 keystore/密钥/真实路径/个人数据，仅 commit 作者邮箱公开可见，Owner 接受）。②修 memory_delete LWW（migration 0003 memory_meta_ts；CONTRACTS §5.2 在桌面 hub 落实，129 tests 绿）。③apply_push 空洞处理与 RESEARCH 文档暂不动（Owner 选择）。**Android 对称 LWW 经核实无需做**：Android `memory()` DAO 仅两处调用——IME 面板只读 list、Sync 仅应用远程事件；手机端无本地 memory 编辑、不 emit memory 事件（记忆为桌面权威、手机纯消费，符合 ADR-0001），故"旧删除覆盖手机较新编辑"的场景不存在，桌面端修复即完整。 |
| 2026-06-18 | 版本对齐 + 重新打包 v1.0.1：__version__ 由 0.1.0 对齐为 1.0.1；桌面 exe 含 migration 0003 重建；APK 重建重签；GitHub Release v1.0.1 刷新双端安装包，使发布产物与最终代码一致 |
| 2026-06-18 | **Android 真机级验证（headless 模拟器，ATD x86_64/API34，Hyper-V 加速）→ 抓到并修复一个发布级崩溃**：app/build.gradle.kts 用 `annotationProcessor(room-compiler)`，但 Kotlin 项目该配置无效，Room 不生成 `AppDatabase_Impl` → 启动即 FATAL（"AppDatabase_Impl does not exist"）。"能编译成 APK"未能发现，**只有真机运行才暴露**。修复：根 + app 加 KSP 插件（com.google.devtools.ksp 2.0.21-1.0.25），Room 改 `ksp(...)`。重建后模拟器实测：app 启动无崩溃（MainActivity 前台）、Room DB 正常打开、**IME 注册并被设为活动输入法**、**Share Target 采集→normalize/hash/classify/secret-guard→Room 落库**（捕获文本 hash/分类/ULID/UTC 正确）。**v1.0.0/v1.0.1 的 APK 含此崩溃，已发 v1.0.2 修复版并下架旧 APK**。versionCode 2 / versionName 1.0.2 |
| 2026-06-12 | 偏离原 ChatGPT 方案的修正：①GitHub 备份去掉 Markdown 镜像只存 JSONL；②密钥排除出 FTS 索引；③同步明确为事件日志复制；④Android 采集以 Share Target 为主路径（平台限制）；⑤IME 推荐只查本地缓存；⑥新增配对鉴权；⑦原 Slice001 拆为 S001–S004 |

## Next Slice Candidate

S005 — Android Capture App：Kotlin/Compose 工程、Room、Share Target、手动保存、QS Tile、历史 UI、
Kotlin 端通过 contracts/vectors/*.json。**前置：需 Android 工具链（JDK + Android SDK + Gradle）**，
开工前先探测/安装环境（D-007 待定）。桌面线 v0.1 已完整（S001–S004），可独立运行使用。
