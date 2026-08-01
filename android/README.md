# ClipVault Personal — Android

OTP Relay 的 Android Runtime、审批构建、权限边界和真机门禁见
[`../docs/ANDROID_OTP_RELAY.md`](../docs/ANDROID_OTP_RELAY.md)。独立 `ime-app` 在所有变体中都不申请
Internet、SMS 或通知监听权限。

Android v2 由两个独立安装包组成，权限边界不能合并：

- `app/`：ClipVault Runtime。负责显式保存、Room、同步、Personal Memory 与设置，拥有网络权限。
- `ime-app/`：独立日用输入法 `com.clipvault.ime`。自有 Android IME 壳 + `librime`，不申请网络、短信、通知监听或剪贴板权限。
- `ime-engine/`：平台中立 Engine Protocol V2 和 Direct 故障降级。
- `rime-engine-android/`：项目维护的 librime JNI、Rime 会话与受控输入方案。
- `../shared-input/rime/`：Android/Windows 共用且唯一的 ClipVault schema、`default.yaml` 与中文标点来源。
- `core/`：与桌面端共享黄金向量的纯 Kotlin 规范化、分类与 Secret Guard。

当前实际 Android 壳是 ClipVault 自有、最小化的 `InputMethodService`。Fcitx5 Android、YuyanIme 和 Trime 只作为构建、交互与兼容性研究基准；不得把研究结论写成当前生产依赖。

## v2 输入法边界

- `compileSdk` / `targetSdk`：36；`minSdk`：26。
- 普通与无痕文本框均使用本地 librime；无痕和敏感应用选择 `enable_user_dict: false` 的中文方案。
- 邮箱、网址、手机号与数字字段在原生 Rime 会话启动时强制 `ascii_mode`，避免中文标点污染专用字段。
- 普通文本键盘提供显式“中/英”切换；切换前先完成当前组字，英文模式走本地 Direct 引擎且不留痕。
- 密码框、用户配置的敏感应用和策略读取失败时，Runtime 候选 fail closed；基本中文输入仍然可用。
- Rime 部署与维护在 Application 后台线程完成。首个编辑器若尚未就绪，只在会话内暂存可见组字，READY 后重放；不会悄悄把预期中文提交成拉丁文本。
- Runtime 只通过签名级 Binder 返回 Snapshot V1：最多 8 项，ID 128 B、label 64 B、正文 16 KiB、
  总帧 64 KiB，并带 publisher epoch、snapshot generation、输入会话 generation 与 30 秒过期时间。
  IME 从不发送按键、组字或当前输入框正文；超时、错配、重复 ID、进程重启和队列饱和都清空该 surface。
- 两个 APK 正式安装时必须由同一证书签名，否则 `com.clipvault.permission.RUNTIME_SNAPSHOT` 桥接会被系统拒绝；基本输入不依赖 Runtime。
- Runtime 正式 APK 物理不包含旧 Panel/Full IME 的 manifest 声明、服务类、输入法 XML 或
  librime payload；系统选择器中只应出现独立 `ClipVault 键盘`。历史实现只保留在 Git 历史中。
- Runtime 默认包可由用户显式启动一次 SMS User Consent 会话。它依赖锁定的官方
  `play-services-auth-api-phone`，等待一条短信并展示系统逐条确认对话框；不申请
  `READ_SMS`/`RECEIVE_SMS`，拒绝、超时、锁屏、目标变化或进程退出都会取消会话。

## 日常开发验证

Windows PowerShell：

```powershell
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
cd android
.\gradlew.bat :core:test :ime-engine:test :rime-engine-android:testDebugUnitTest `
  :ime-app:testDebugUnitTest :app:testDebugUnitTest --no-daemon
```

普通的 `assembleDebug` 可以验证 Kotlin 壳，但没有显式 native 属性时允许产生 Direct-only 工程构建，**不能作为 v2 发布候选证据**。

## fail-closed production 构建

本机需准备锁定的 librime 1.16.1、双 ABI 静态构建、fcitx5-android 0.1.3 的受审 native 预编译依赖，以及 Apache-2.0 的 `rime-pinyin-simp` 数据。统一入口：

```powershell
cd android
.\scripts\build-v2-ime.ps1
```

该入口强制真实 native release，并验证：

- IME 中 `arm64-v8a` 与 `x86_64` 的 JNI / C++ runtime；Runtime 中不存在旧 IME 类与声明；
- 所需 Rime schema、词典和 OpenCC 资产；
- API 36；
- 无 INTERNET / SMS / Notification Listener 权限；
- APK 中恰好一个 `InputMethodService`；
- APK 与每个 ELF 的 16 KB 对齐；
- canonical Rime schema、标点、production lock、NOTICE 与所有依赖许可证均进入 APK。

签名后再使用同一验证器比对 Runtime 证书：

```powershell
.\scripts\verify-v2-ime-apk.ps1 -Apk .\ClipVault-IME.apk -RuntimeApk .\ClipVault-Runtime.apk
```

## 安装与启用

1. 先安装同证书的 Runtime APK 与 IME APK。
2. 启动 `ClipVault IME 设置`，等待状态显示“中文引擎：已就绪”。
3. 点击“打开系统输入法设置”，只启用 `ClipVault 键盘`。
4. 点击“选择 ClipVault 输入法”，在内置测试框输入 `nihao`，选择“你好”。
5. 在设置页维护敏感应用包名；这些应用和未知包身份中不显示 ClipVault 第二候选行。

API 36 x86_64 与 Android 15/API 35 16 KB AVD 的原生自动测试已经通过；真实手机、常用应用、
同证书双 APK、安装升级/卸载和连续日用仍属于发布门禁。编译成功或未签名 APK 不等于稳定发布。

生产 workflow 另外要求带 `clipvault-android-device` 标签、恰好连接一台已授权设备的
self-hosted runner 执行 `NativeRimeDeviceTest`。无匹配 runner 会保持阻塞；runner 无设备、
多设备、测试被跳过或缺少长句/`xi'an`/分页/取消恢复断言都会 fail closed。

## 隐私不变量

- 不记录普通键入、原始按键、组字或完整上屏正文。
- IME 无网络，网络、数据库与同步都在 Runtime 外部进程。
- 普通内容只有用户显式保存才进入 Runtime；密码、Secret 与 OTP 不进入普通候选或历史。
- Android 10+ 不做后台剪贴板轮询。
