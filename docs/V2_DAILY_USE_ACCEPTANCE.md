# ClipVault v2 日用验收合同

> 状态：执行中（2026-08-01）。本文定义“可以正式日常使用”的最小证据集；
> 单元测试、合成通道、未注册 DLL、仅能英文直输的回退键盘或未签名构建均不能单独满足本合同。

## 1. 完成定义

ClipVault v2 只有在同一候选版本满足以下四条产品闭环时，才可以称为日用候选：

1. Android 是系统可选的主输入法，默认中文路径由真实 librime 驱动；引擎或 Runtime 故障时仍可直接输入。
2. Windows 是正式注册的 TSF 文本服务，中文引擎位于外置 Host；Host 崩溃不能拖垮宿主应用，并可建立新会话恢复。
3. Personal Memory、剪切板和系统 Inline Autofill 与 Rime 候选保持独立身份和隐私边界；密码、无痕与敏感应用中不显示 ClipVault 数据。
4. OTP 只经过显式授权、目标绑定、端到端认证加密的临时通道；不进入剪切板、数据库、普通 outbox、日志或崩溃报告，消费或到期后销毁。

签名、安装、升级、卸载和真实设备/应用矩阵仍是 Owner 门禁。自动化证据通过后，只能报告“自动化门禁通过、等待 Owner 验收”，不能自动宣称稳定发布。

## 2. Android 日用门禁

| ID | 必须满足 | 自动化证据 | 设备证据 |
|---|---|---|---|
| `V2D-A01` | 默认引擎是真实 librime；Direct adapter 只作故障回退 | native/JNI build、候选/上屏测试、APK 原生库清单 | `nihao -> 你好` |
| `V2D-A02` | 全拼、长句、退格、翻页、稳定 ID 选词、取消/确认 | Engine V2 vectors + app tests | 常用应用输入矩阵 |
| `V2D-A03` | IME 安装包不声明 INTERNET、SMS 或通知读取权限 | merged-manifest/APK permission test | 安装后系统权限页 |
| `V2D-A04` | Runtime/Companion 与 IME 为不同 package；只通过签名级、限时本地 IPC 交换已过滤快照 | manifest/permission/IPC tests | 强杀 Runtime 后继续输入 |
| `V2D-A05` | 支持 Android 11+ Inline Autofill；IME 只托管受系统保护的 suggestion View，不读取正文 | XML/API source tests | OTP/密码管理器建议点击填充 |
| `V2D-A06` | API 35 16KB page size、arm64-v8a/x86_64 ELF 与 APK ZIP 对齐 | 16KB checker + clean build | 16KB emulator/device smoke |
| `V2D-A07` | 普通键入不进入日志、Room、同步、学习或分析 | privacy/source/unit tests | 输入后痕迹核查 |
| `V2D-A08` | 密码、无痕和敏感应用清空 ClipVault surface；Rime/直接输入仍可用 | privacy tests | 密码框与应用切换 |
| `V2D-A09` | 面向 2026-08-31 之后的 Google Play 新应用/更新时，`compileSdk`/`targetSdk` 至少为 API 36，并通过 Android 16 行为回归 | merged manifest、SDK level assertion + clean build | Android 16 emulator/device smoke |
| `V2D-A10` | 主键盘至少覆盖 Shift/Caps、数字、常用符号、空格、连续退格、换行/发送动作、输入法切换与物理键盘；旋转或 Runtime 失效后不丢失基本输入能力 | keyboard state/editor-action tests | 横竖屏、文本/邮箱/网址/数字/多行输入矩阵 |
| `V2D-A11` | 正式候选只暴露一个主 IME 入口；拥有网络权限的 Runtime APK 不再导出旧 Panel/FullKeyboard IME，Panel 迁入独立 IME 内部工具页 | release-manifest service assertion | 系统输入法列表无重复/联网旧入口 |

`V2D-A09` 对应 Google Play 已公布的 2026 年目标 API 门禁：
[Android 16 / API 36 target requirement](https://developer.android.com/google/play/requirements/target-sdk)。
本地开发 APK 可以先行验证，但不能用 `targetSdk 34` 构建冒充正式商店发行候选。

## 3. Windows 日用门禁

| ID | 必须满足 | 自动化证据 | 设备证据 |
|---|---|---|---|
| `V2D-W01` | x64 TSF DLL 可注册、激活、取消注册且无残留 | register/unregister dry run + registry assertions | 当前用户实际切换 |
| `V2D-W02` | 外置 Host 使用真实 librime 完成组字、候选、分页、选词和上屏 | MSVC `/W4 /WX` + CTest | Notepad/浏览器/Terminal |
| `V2D-W03` | 候选窗跟随 TSF 文本位置，支持键盘选择、翻页、取消/确认 | native UI tests | DPI/多屏/常用应用 |
| `V2D-W04` | TSF DLL 不导入 Python、librime、SQLite 或网络库 | import/dependency audit | 强杀 Host 时宿主不崩溃 |
| `V2D-W05` | Pipe 仅当前用户和 SYSTEM，可拒绝远程客户端；协议错配、乱序、旧 epoch 均 fail closed | ACL/protocol/recovery tests | Host 重启后新会话恢复 |
| `V2D-W06` | x86/x64 对应宿主可用；ARM64 至少有明确发行设计和构建门 | build matrix | Office 32/64 与系统应用 |
| `V2D-W07` | 安装、覆盖升级、卸载和重装保留正确 TSF 注册状态 | installer tests | 清洁账户演练 |
| `V2D-W08` | 激活时异步预热 Host；逐键路径不启动部署且连接等待预算不超过 30ms，Host 不可用时按键透传或本地回退 | cold-host latency/recovery tests | 冷启动与强杀 Host 连续输入 |

## 4. OTP Relay 日用门禁

| ID | 必须满足 | 自动化证据 | 双设备证据 |
|---|---|---|---|
| `V2D-O01` | 捕获授权绑定 session、sender、target、来源和短期 grant | mismatch/expiry tests | 撤权后不再捕获 |
| `V2D-O02` | 使用经审阅的 AEAD；nonce、AAD、event、sequence、TTL 和设备身份全部认证 | 标准向量、tamper/replay tests | 手机到已配对电脑 |
| `V2D-O03` | 未配对、撤销、错误目标、锁屏、过期和重放均拒绝 | security tests | 逐场景演练 |
| `V2D-O04` | 在线即送；离线不进入普通 outbox，不在恢复网络后补传 | persistence/import audit | 断网/重连/重启 |
| `V2D-O05` | Windows 默认非激活提示或输入法候选；点击后用 TSF 插入，不经过剪切板 | sink/claim tests | 当前输入框直接填入 |
| `V2D-O06` | 自动填入只允许预先 armed 的 process/window/document/context，且绝不自动提交表单 | context race tests | 抢焦点、锁屏、过期 |
| `V2D-O07` | 明文、密文包和可恢复副本不写数据库、文件、日志、遥测或 dump 配置 | static/runtime artifact audit | 消费/到期后的痕迹核查 |
| `V2D-O08` | Android 明文捕获只在 Companion Runtime 中经独立显式授权启用；SMS 权限发行需通过 Play restricted-permission review，通知监听/Android 17 companion exemption 需真机证据；IME 仍无 SMS/网络权限 | manifest、role/association/capture policy tests | Android 15/17 SMS、RCS、通知与撤权矩阵 |

合成 capture、合成 channel 和内存 transport 只能证明状态机，必须从生产 package/构建物理排除，且不得由生产 package 根导出。`production_ready` 布尔值或 Python 私有注册表都不能充当安全审批。

## 5. 统一回归与停止条件

候选版本至少执行：

```text
Foundation contracts
Android unit + API 36 assemble + Android 15 16KB and Android 16 emulator smoke
Windows MSVC Debug/Release + CTest + protocol conformance
OTP unit/security + persistence audit
Android/Windows privacy and dependency boundary checks
Owner signed-artifact and manual daily-use matrix
```

以下任一情况都必须保持 `BLOCKED`：

- Android APK 默认仍只使用 Direct adapter，或 IME package 仍拥有网络/SMS 权限；
- Windows 只通过 echo engine、DLL 尚未实际注册，或候选 UI/Host 恢复未验证；
- OTP 仍使用 synthetic crypto/transport，或没有真实平台授权捕获与 TSF sink；
- Android/Windows 正式构建入口仍从 `spikes/` 加载生产源码、依赖工作区外未锁定目录，
  或不能在记录的固定第三方源码/二进制锁上 clean build；
- 真实发布构建未签名、第三方引擎/词典来源未冻结，或 Owner 设备矩阵无证据；
- 对外分发包含 Fork/链接第三方输入法组件的构建时，仓库自有代码许可证仍未由 Owner 明确、
  或 LGPL/GPL/词典/资源的源码与重链接义务尚未形成可交付证据；
- 为了“完成”而放宽普通键入不留痕、Secret Guard、密码框抑制、OTP 单次/短时/目标绑定等不变式。

## 6. 当前执行快照（2026-08-01）

本节是工作证据，不是稳定声明：

- Android：Engine Protocol V2 production shell 与 Direct 故障回退已构建；API 35 16KB 模拟器上系统 IME 可见，屏幕点击 `a/b/c` 已写入真实目标输入框；真实 librime native harness 已完成 `nihao -> 你好`，但 native 默认接入、Inline Autofill 和独立无网络 IME package 尚未全部闭环。
- Windows：项目自有 TSF DLL、当前用户 ACL Named Pipe 与外置 Host 已用 MSVC 构建；真实 librime Host 已完成 `nihao -> candidate -> stable ID -> commit`，候选 UI、Host 恢复和实际注册/应用矩阵仍在执行。
- OTP：内存状态机及 production-shaped ports 已通过定向测试，并修复授权目标与 ACK 生命周期审计问题；真实平台 AEAD、传输、Android 捕获和 Windows TSF sink 尚未完成，因此仍禁止跨设备日用。
