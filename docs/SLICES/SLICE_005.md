# Slice 005 — Android Capture App + Kotlin Core (VEC-1 cross-platform proof)

> Architect: Claude Fable 5 | 2026-06-13 | 状态：规格冻结
> 对应版本：v0.2。Android 采集端 + **Kotlin core 通过与 Python 同一套测试向量**（VEC-1 仲裁）。

## 1. 目标与可验证边界

- **可在本机充分验证（JDK+Gradle，无需 Android SDK）**：`android/core` 纯 Kotlin/JVM 模块，
  实现 NORM-1/CLS-1/SG-1(+1.1) 的 Kotlin 版，**加载 `contracts/vectors/*.json` 并断言通过**。
  这是双端一致性的硬证明。
- **写全代码、运行期需用户设备**：`android/app` 的 Compose UI、Share Target、手动保存、
  QS Tile、Room、历史/搜索、Sync 客户端、IME（S009）。需 Android SDK 编译、真机/模拟器运行。
  作为审阅级源码交付，真机验证为唯一需用户参与的一步。

## 2. 允许触碰的文件

```text
android/settings.gradle.kts, build.gradle.kts, gradle.properties
android/core/** （kotlin jvm：Normalize/Classifier/SecretGuard/Models + 向量测试）
android/app/** （android application 源码：Compose/Room/Share/QSTile/Sync/IME 骨架）
android/README.md
docs/{HANDOFF,GATES}.md
```

## 3. 实现要求

1. **android/core（kotlin jvm，可测）**：
   - `Normalize.kt`（NORM-1：CRLF→LF、NFC、rstrip；sha256）。
   - `Classifier.kt`（CLS-1：7 类，与 Python 同序同规则）。
   - `SecretGuard.kt`（SG-1：硬规则 + 熵启发 + SG-1.1 已知格式排除）。
   - `Models.kt`（Clip/SecretVerdict 数据类）。
   - 测试：读取仓库根 `contracts/vectors/{normalization,classifier,secret_guard}.json`，
     逐例断言（与 Python `test_vectors.py` 等价）。
2. **android/app（android application 源码，骨架编译目标）**：
   - Share Target（ACTION_SEND text/plain → CaptureActivity）。
   - 手动保存当前剪切板（前台读 ClipboardManager）+ Quick Settings Tile。
   - Room：clips 缓存 + sync_outbox（与 DB-1 子集一致）。
   - Compose 历史列表 + 搜索；Sync 客户端（OkHttp/HttpURLConnection push/pull，WorkManager 兜底）。
   - 复用 core 的 normalize/secret/classify（捕获即过闸门 A，密钥本地隔离）。

## 4. 验收门禁

- J1.（本机可验证）`android/core` 编译通过。
- J2.（本机可验证）core 向量测试：normalization/classifier/secret_guard 三个 JSON 全部通过
  （与 Python 同一文件、同一期望）。
- J3. app 源码完整：Share Target、手动保存、QS Tile、Room、Compose 历史、Sync 客户端、
  捕获过 Secret Guard（代码审阅级；真机运行验证留给 Owner）。
- J4. 隐私：app 捕获路径只在显式动作时写库；无后台剪切板轮询（平台禁止，ADR-0004）。
- J5. README 写明：core 如何跑测试（JDK+Gradle）、app 如何在 Android Studio 打开/构建/真机验证。

## 5. 验证命令

```text
android> gradle :core:test        # J1/J2（仅需 JDK + Gradle + Maven Central）
# app 需在 Android Studio 中配置 Android SDK 后构建与真机运行
```
