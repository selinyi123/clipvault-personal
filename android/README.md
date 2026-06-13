# ClipVault Personal — Android

Kotlin。两部分：

- **`core/`** — 纯 Kotlin/JVM：`Normalize` / `Classifier` / `SecretGuard` / `Models`，
  与桌面端逐条对应。**通过与 Python 完全相同的 `contracts/vectors/*.json` 测试向量**
  （VEC-1，两端唯一仲裁）。无 Android 依赖，只需 JDK 即可验证。
- **`app/`** — Android 应用与输入法：Share Target、手动保存、Quick Settings Tile、Room 缓存、
  Compose 历史/搜索/配对、HTTP 同步客户端（push/pull + WorkManager）、Keyboard Personal（IME）。

## core 跨平台一致性验证

### 方式 A：本仓库已用 kotlinc 实测通过（2026-06-13）

```
JDK 21 + kotlinc 2.0.21 + org.json
编译 core/src + 测试运行器 → java VectorCheckKt contracts/vectors
结果：VEC-1 OK: 100 vectors passed (norm=22 cls=40 sg=38)
```

即 Kotlin 端的规范化/分类/Secret Guard 与 Python 端对同一批向量结果**完全一致**。

### 方式 B：Gradle（需联网拉依赖）

```bash
cd android
gradle :core:test        # 仅需 JDK + Gradle + Maven Central，无需 Android SDK
```

## app 构建与真机验证（需 Android SDK + 设备）

1. 用 **Android Studio** 打开 `android/`（首次会提示生成 Gradle wrapper / 安装 SDK）。
2. 配置 Android SDK（compileSdk 34，minSdk 26）。
3. Build → 在真机/模拟器安装。
4. 启用输入法：系统设置 → 语言和输入法 → 启用 “ClipVault”。
5. 配对：桌面 Web UI 点「配对设备」得到一次性码 → App「配对」里填桌面 IP + 码。
6. 验证路径：
   - 任意 App 分享文本 → ClipVault → 历史出现、同步到桌面。
   - 通知栏 Quick Settings 「Save to ClipVault」保存当前剪贴板。
   - 切到 ClipVault 键盘 → 点最近内容一键粘贴 / 保存剪贴板 / 切回。

## 隐私不变量（与桌面一致）

- 输入法**永不记录普通键入**；只有显式点击「保存剪贴板」才写库（ime/ 无网络调用）。
- 捕获即过 Secret Guard（gate A）；密钥本地隔离，**不进 outbox、不同步、不入全文**（gate B）。
- Android 10+ 禁止后台读剪贴板——本应用不申请、不轮询；采集靠分享/手动/输入法显式动作。

## 构建（已实测）

本仓库已用 Gradle 8.10.2 + JDK 21 + Android SDK(platform-34/build-tools-34.0.0) 实测：

```
gradle :core:test        → VectorTest 1 test, 0 failures（VEC-1 经 JUnit/Gradle 路径再证）
gradle :app:assembleDebug → BUILD SUCCESSFUL，产出 app/build/outputs/apk/debug/app-debug.apk（~9.2MB）
```

项目自带 Gradle wrapper（`gradlew` / `gradlew.bat`），Android Studio 可直接打开。
`local.properties` 的 `sdk.dir` 为机器相关项，未提交——首次构建请指向你的 Android SDK。

## 状态

- core VEC-1：**已通过（100/100）**，且经 Gradle `:core:test` 再次确认。
- app：**整体编译通过并产出可安装 APK**（Share/Tile/Room/Compose/Sync/IME 全部编译）。
- **唯一剩余**：把 APK 装到手机、启用输入法、配对，做设备端体验确认（需物理设备，由 Owner 完成）。
