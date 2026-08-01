# ClipVault v2 许可证与第三方分发门禁

> 当前裁决：`internal_only`。仅允许 Owner 控制的本机/内部日常使用，
> 不授予第三方许可证，也不允许公开或外部分发。本文是治理核对表，
> 不构成法律意见。

## 当前项目许可证状态

`THIRD_PARTY_MANIFEST.yaml` 必须保持以下三元组：

```text
project_license.status: internal_only
license_file: null
distribution_allowed: false
```

这是已经完成的 Owner 产品治理裁决，不再是“等待选择许可证”。它允许
foundation/source readiness 以及内部日用 Owner evidence 继续推进，但不创建
根 `LICENSE`，不授予第三方权利，也不把 readiness 的 `ready` 解释为公开发行
许可。

若未来需要向客户、合作方、商店、公共仓库或其他外部主体交付，必须先由
Owner 单独裁决并原子切换为：

```text
project_license.status: approved
license_file: <仓库内真实许可证文件>
distribution_allowed: true
```

不得通过仅修改 `distribution_allowed`、Owner evidence 布尔值或发行文案来绕过
文件型许可证门禁。

## 自动化已经固定的证据

- Android 与 Windows 使用 librime `1.16.1`，提交
  `de4700e9f6b75b109910613df907965e3cbe0567`。
- 双端基础词典固定为 rime-pinyin-simp 提交
  `0c6861ef7420ee780270ca6d993d18d4101049d0`，下载归档 SHA-256 为
  `46f37114a7929ecc01003a236803c8b1e5198382e6a21f83fae036604a6b08bf`。
- Android 原生归档、ABI、工具链、打包许可证路径与哈希由
  `android/rime-engine-android/RIME_PRODUCTION_LOCK.json` 固定；其中四个
  native prebuilt 的精确对应源码与 NOTICE 闭包仍须 Owner 复核。
- SMS User Consent 依赖由
  `android/app/PLAY_SERVICES_SMS_USER_CONSENT_LOCK.json` 固定。
- Windows 官方 librime 包、来源、哈希和运行边界由
  `windows/ime/rime/RIME_SDK_LOCK.json` 固定；该 lock 明确不枚举官方
  `rime.dll` 的传递组成。
- 共享 Rime 方案与词典允许文件由
  `shared-input/rime/RIME_ASSET_LOCK.json` 固定。
- 根目录 `THIRD_PARTY_MANIFEST.yaml` 汇总生产候选实际使用的主要组件；
  包内实际二进制组成仍须在最终候选上复核。

纯标准库一致性检查：

```powershell
python tools/validate_v2_third_party.py
```

该命令校验 JSON-compatible YAML、四份生产 lock、repository license review
copy、打包路径、NOTICE 和项目许可证状态。它只接受以下三种一致状态：

- `owner_decision_required`、`license_file: null`、
  `distribution_allowed: false`（尚未裁决，fail closed）；
- `internal_only`、`license_file: null`、`distribution_allowed: false`
  （已裁决内部使用，分发仍 fail closed）；
- `approved`、指向仓库内真实文件的 `license_file`、
  `distribution_allowed: true`（未来外部分发模式）。

状态、许可证文件和分发布尔值的任意错误组合都会阻塞。检查通过只表示仓库内
证据一致，不替 Owner 签名、验证具体候选或批准公开分发。

## 内部日用 Owner evidence

在 `internal_only` 状态下，`license_and_notices_approved: true` 仅表示 Owner 已
针对同一不可变候选完成内部使用范围的第三方许可和 NOTICE 复核。它不能解释为
对外许可证、再分发授权或商店发布批准。内部日用仍必须完成签名、本机安装、
Android/Windows/OTP 手工矩阵和七天日用证据。

## 未来外部分发前必须完成

1. 为 ClipVault 自有代码选择许可证或书面分发条款，并把真实文件提交到仓库。
2. 原子更新 `THIRD_PARTY_MANIFEST.yaml`、许可证文件和
   `THIRD_PARTY_NOTICES.md`；不得只改一个布尔值。
3. 批准词典、Rime 方案、图标、字体和其他资源的来源及再分发范围。
4. 批准 Android 静态链接依赖和 Windows 官方 librime 二进制所需的完整
   NOTICE/许可证集合；不能只随包放置 librime 自身许可证。
5. 如最终组合产生 LGPL 对应源码、重链接或反向工程允许义务，确认交付载体、
   保存期限和下载位置。
6. 将许可证裁决、签名身份和最终 artifact SHA-256 绑定到同一个不可变候选。

## 外部分发包检查

```text
□ 根许可证文件与实际分发条款一致
□ THIRD_PARTY_MANIFEST.yaml 的 commit/hash 与构建日志一致
□ Android APK/AAB 内 NOTICE 与逐项许可证文本存在
□ Windows 安装包内 NOTICE、librime 及传递依赖许可证存在
□ 词典 AUTHORS/LICENSE 与精确词典文件同时交付
□ 对应源码、补丁、构建说明或重链接材料（如适用）可取得
□ APK、DLL、EXE 和安装器签名身份与 Owner evidence 一致
□ 未把参考用途 GPL 项目源码混入候选
□ 未包含来源不明的增强词库、模型、字体、图片或皮肤
```

只要 `project_license.status: internal_only`，上述外部分发门禁就保持关闭。
