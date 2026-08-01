# ClipVault v2 日用候选手工验收单

> 状态：待 Owner 执行。本文只记录真实设备与真实应用证据；任何单元测试、
> 模拟 transport、未签名 APK/DLL、echo/Direct-only 回退或源码截图都不能代替本表。

## 1. 候选身份

每轮验收先填写一次，所有证据必须属于同一候选：

```text
candidate_id:
candidate_commit:
source_candidate_dir:
source_manifest_path / sha256:
build_receipt_path / sha256:
workflow_run_url:
android_ime_path:
android_ime_sha256:
android_ime_apksigner_report_path / sha256:
android_runtime_path:
android_runtime_sha256:
android_runtime_apksigner_report_path / sha256:
android_signing_cert_sha256:
desktop_executable_path / sha256:
windows_package_path:
windows_package_sha256:
windows_installer_path / sha256:
windows_authenticode_report_path / sha256:
windows_signing_subject_and_thumbprint:
librime_version_and_commit:
rime_schema_dictionary_lock:
tested_by:
started_at_utc:
completed_at_utc:
evidence_location:
```

路径必须指向 readiness 执行时仍存在的真实文件或证据目录。readiness 会要求
`candidate_commit` 等于当前仓库 `HEAD`，验证原始 CI bundle 的构建回执、工作流
URL、release manifest 和锁文件，并重新计算每个签名产物及验证报告的 SHA-256。
两个 Android 报告必须来自 `apksigner verify --verbose --print-certs`；Windows
报告必须符合 `contracts/v2_windows_authenticode_evidence.schema.json`，覆盖 Desktop、
安装器与 Windows IME 包内四个自有二进制。移动、删除或替换任何文件后，旧 evidence
自动失效。

禁止在本文中记录配对密钥、短信正文、OTP、Bearer token、私钥或恢复码。
截图中的手机号、账号、窗口标题和验证码必须遮盖；OTP 只记录长度、来源类别、
事件状态与延迟。

## 2. 前置门禁

- [ ] `tools/v2_daily_readiness.py --automated-only --candidate-dir <CI bundle>` 通过。
- [ ] Android IME 与 Runtime 是两个独立、同一 Owner 签名的 package。
- [ ] IME APK 权限清单不含 INTERNET、SMS、通知监听或剪贴板后台采集能力。
- [ ] Windows 包含 x64 Host、x64 TSF DLL 与 x86 TSF DLL，全部来自同一候选。
- [ ] Windows DLL、EXE、安装器和 Android APK 已用 Owner 正式证书签名。
- [ ] 仓库自有许可证已经由 Owner 确定；第三方 NOTICE、源码/重链接材料、词典来源可交付。
- [ ] OTP restricted-permission 发行路径若启用，已有对应商店审核/企业分发依据；否则保持关闭。

任何一项未满足时，可以继续内部测试，但结果只能称“开发候选”，不能称“正式日用候选”。

## 3. Android 主输入法

测试设备至少包括：

```text
Android 15 / 16 KiB page-size device or emulator:
Android 16 / API 36 device or emulator:
OEM physical phone and ROM:
Android 17 test device/emulator (OTP capture only, when available):
```

### 3.1 安装与系统入口

- [ ] 清洁安装 IME 与 Runtime；系统只显示一个 ClipVault 主输入法入口。
- [ ] 设置页能引导启用和选择输入法，不要求授予网络/SMS 权限给 IME。
- [ ] 升级安装保留允许保留的设置；卸载后无失效输入法入口。
- [ ] Runtime 未安装、被强杀、数据库锁定或网络断开时，中文基本输入仍可用。
- [ ] IME 进程被系统回收后，重新打开输入框不会丢失已上屏正文或崩溃目标 App。

### 3.2 输入矩阵

在下列目标中至少各完成一次连续输入、退格、翻页、选词、换行/发送、光标修正：

- [ ] AOSP/系统记事应用。
- [ ] Chrome 或 Edge。
- [ ] 微信或同类聊天应用。
- [ ] Obsidian 或同类多行编辑器。
- [ ] 终端/代码编辑器。
- [ ] 邮箱、网址、手机号、纯数字、多行、搜索、发送与密码输入框。

必测文本：

```text
nihao                    -> 你好
jintianxiawuwomenqukaihui -> 今天下午我们去开会（允许同义候选，但须长句可连续完成）
xi'an                    -> 西安
中文 + English123 + URL + 常用符号
```

- [ ] Shift 一次性大写、双击 Caps Lock、数字层、常用符号、空格和连续退格可用。
- [ ] 横竖屏切换、分屏、不同 DPI/字号后按键和候选仍可触达。
- [ ] 物理键盘能完成中文组字、候选选择、翻页、确认与取消。
- [ ] 首次冷启动部署时，按键只在本地内存暂存；准备完成后重放，超时则原文安全上屏，无字符丢失/重复。
- [ ] 强制终止 Rime/IME 后，正在组字的内容按合同保全或明确取消，不出现悄然丢字。
- [ ] 生产 workflow 的 `clipvault-android-device` runner 记录唯一设备序列、型号、API/page size，
  `NativeRimeDeviceTest` 六项全部执行且 failures/errors/skipped 均为 0；无设备或 queued 不能记为通过。

### 3.3 隐私与候选面

- [ ] 普通文本框中 Rime 候选与 ClipVault 工具栏分层显示，异步刷新不会错选。
- [ ] 密码框不显示 ClipVault/OTP/剪贴板/Memory；输入法仍能输入所需字符。
- [ ] `IME_FLAG_NO_PERSONALIZED_LEARNING` 与敏感应用名单启用 private/no-learning schema。
- [ ] 切换到密码/敏感 App 时，上一 App 的 Runtime 候选和延迟 Binder 回包不会再显示。
- [ ] Runtime 候选点击前会处理现有组字，正文只上屏一次。
- [ ] Android 11+ Inline Autofill 建议可显示并点击；IME 无法读取、记录或中继受保护正文。
- [ ] 完成一段普通输入后，Room、普通 outbox、日志、崩溃报告与历史中没有键流或正文。

## 4. Windows 原生输入法

### 4.1 安装与架构

- [ ] 清洁账户安装后 TSF profile 可见；x64 与 x86 COM server 指向同一候选包。
- [ ] 覆盖升级、修复安装、卸载、重启、重新安装均无失效 profile 或旧 DLL 路径。
- [ ] x64 Host 登录后异步预热；逐键路径不运行部署或联网。
- [ ] 强杀 Host 不会使 Notepad、Office、浏览器、Terminal 或 Electron 宿主崩溃。
- [ ] Host 自动重启后建立新 epoch/session，旧 candidate ID 与乱序请求被拒绝。

### 4.2 应用矩阵

在下列环境完成 `nihao -> 你好`、长句、退格、分页、数字选词、确认与取消：

- [ ] Windows 11 Notepad。
- [ ] Office x64。
- [ ] Office 或其他 Win32 x86 编辑器。
- [ ] Edge/Chrome。
- [ ] Electron 应用。
- [ ] WPF 应用。
- [ ] Windows Terminal。
- [ ] 系统搜索/开始菜单。
- [ ] Microsoft Store/AppContainer 文本框（若发行范围包含）。

- [ ] 100%/150%/200% DPI、多显示器和屏幕边缘处候选窗定位正确。
- [ ] 冷 Host、半帧/超时、强杀 Host 时，不丢失已经显示的组字；当前普通字母按恢复策略透传或重放一次。
- [ ] DLL 依赖审计确认不导入 librime、Python、SQLite、WinHTTP/WinINet 或同步代码。
- [ ] 非当前用户与远程 Named Pipe 客户端无法连接；当前用户/SYSTEM 正常。
- [ ] Desktop Runtime 异步发布的 ClipVault 候选与 Rime 分栏；按键路径不等待 Python、数据库或网络。
- [ ] 切入密码/无痕/敏感上下文后旧快照和迟到回包立即失效；强杀 Desktop Runtime 后中文输入不受影响。
- [ ] 点击 Memory/剪贴板候选只上屏一次，且普通按键、preedit、完整目标正文不回传 Desktop Runtime。

## 5. OTP Relay

OTP 只在功能已显式启用且平台适配器通过自动化向量后测试；否则整节保持未通过。

### 5.1 授权与捕获

- [ ] 手机和目标电脑逐设备配对；默认关闭 OTP Relay。
- [ ] 启用时展示数据范围、目标设备、TTL 和撤销入口。
- [ ] Android 15 通知 OTP 遮蔽/可信 companion 行为有真实证据。
- [ ] Android 17 标准 SMS/RCS、Companion 豁免和延迟行为有真实证据。
- [ ] 若使用受限 SMS 权限，实际分发渠道的权限声明/审核结果已记录。
- [ ] 默认 Runtime 不授予 SMS 权限：点击“等待并确认下一条短信验证码”后仅等待一条，
  系统对话框拒绝时不读取/中继；同意后只中继规范化 OTP。
- [ ] User Consent 会话在 120 秒超时、锁屏、旋转/重建、进程终止、目标设备变化和重复结果时
  均失效，且不会在重启后恢复为已授权。
- [ ] 撤销授权或解除设备关联后，新 OTP 不再捕获或中继。

### 5.2 临时传输与上屏

- [ ] 在线手机收到 OTP 后，只向选定电脑发送 AEAD envelope；错误 target/sender/session/sequence/tag 全部拒绝。
- [ ] 电脑离线时不写普通 outbox；重连后过期 OTP 不补传。
- [ ] 默认弹窗不抢焦点、不进入通知中心历史、不使用系统剪贴板。
- [ ] 点击“输入到当前框”通过受信 TSF context 插入，且不自动按 Enter/提交表单。
- [ ] armed 自动填入只在原 process/window/document/context 未变化且 claim 未过期时执行。
- [ ] 抢焦点、锁屏、远程会话、屏幕共享、错误窗口、多条 OTP 与重放场景全部 fail closed。
- [ ] 消费、忽略或到期后两端清除应用层内存；日志/数据库/文件/dump 中无 OTP 正文或可恢复 envelope。

只记录以下脱敏结果：

```text
event_hash_prefix:
code_length:
source_class:
capture_to_present_ms:
present_to_consume_ms:
final_state: consumed | expired | dismissed | rejected
```

## 6. 7 天日用稳定性

- [ ] Android 连续设为默认键盘至少 7 天。
- [ ] Windows 连续启用 TSF 输入法至少 7 天。
- [ ] 期间网络断开、设备休眠、锁屏、切换网络、Runtime/Host 重启均已覆盖。
- [ ] 无目标 App 崩溃、无正文丢失/重复、无敏感候选串场、无 OTP 持久化。
- [ ] 已记录崩溃率、Host/IME 重启次数、P50/P95 冷启动与候选延迟；记录中不含键入正文。
- [ ] 所有 P0/P1 问题关闭，P2 问题有 Owner 接受的规避或延期决定。

## 7. 最终裁决

```text
automated_gates: pass | fail
android_manual: pass | fail
windows_manual: pass | fail
otp_manual: pass | fail | disabled
seven_day_daily_use: pass | fail
signing_and_license: pass | fail
owner_decision: approve | reject
owner_name:
decision_at_utc:
evidence_location:
```

只有全部发行范围内的行通过并由 Owner 明确 `approve`，才可进入正式 v2 发布门禁。
若 OTP 尚未通过，必须在产品构建、设置和文案中保持默认关闭/不可用，不能用“实验功能”绕过安全门禁。

最终把脱敏结果按
[`contracts/v2_daily_owner_evidence.schema.json`](../contracts/v2_daily_owner_evidence.schema.json)
写入 `artifacts/v2-daily/owner-evidence.json`。可以复制
[`V2_DAILY_USE_OWNER_EVIDENCE.example.json`](V2_DAILY_USE_OWNER_EVIDENCE.example.json)
作为起点，但示例中的零哈希与 `false` 只能表示未通过，不能作为发行证据。
