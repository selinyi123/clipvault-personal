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

## app 构建与设备验证（需 Android SDK）

1. 用 **Android Studio** 打开 `android/`（首次会提示生成 Gradle wrapper / 安装 SDK）。
2. 配置 Android SDK（compileSdk 34，minSdk 26）。
3. 日常安装、启动、调试、logcat 和自动化验证一律默认使用 Android Emulator / AVD。
   在仓库根目录运行
   `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\android-test.ps1`；
   如安装了多个 AVD，再附加 `-AvdName <名称>`。`Bypass` 只作用于这个子进程，不会修改
   用户级或系统级执行策略。脚本只接受 `emulator-####`，不会回退到已连接的真机。
4. 启用输入法：系统设置 → 语言和输入法 → 启用 “ClipVault”。
5. 配对：先确保桌面端与 Android 为当前兼容版本；桌面 Web UI 点「配对设备」得到
   一次性码 → App「配对」里填桌面 IP + 码。重新配对会协商 Android 本地 outbox 的
   首个保留序号，已确认并清空过旧事件时也不会让后续显式保存永久卡住。
6. 验证路径：
   - 任意 App 分享文本 → ClipVault → 历史出现、同步到桌面。
   - 通知栏 Quick Settings 「Save to ClipVault」保存当前剪贴板。
   - 切到 ClipVault 键盘 → 点最近内容一键粘贴 / 保存剪贴板 / 切回。

如需显示模拟器窗口，可传入 `-ShowEmulator`；默认使用无窗口模式。设备测试可按需加
`-RunInstrumentationTests`。真机仅用于 Owner 明确执行的残余人工 QA / 发布门禁，Codex
不得因为 `adb devices` 中出现真机就对其安装、启动、读 logcat 或执行任何其他操作。

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
- **剩余发布门禁**：由 Owner 在明确控制的物理设备上安装 APK、启用输入法、配对并完成
  设备端体验确认；模拟器结果不能替代这项真机证据。
