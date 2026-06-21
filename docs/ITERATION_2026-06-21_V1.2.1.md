# ClipVault Personal — 2026-06-21 v1.2.1 修复与后续设计

## 1. 本轮修复范围

本轮目标是收敛 v1.2.x 的可靠性和安全闭环，不扩大到 v2 中文输入底座实现。

已修复：

1. Secret API 预览不再返回真实 `length`，避免隔离区条目通过长度侧信道泄露信息。
2. 用户显式释放 secret clip 后，除 Obsidian 与 backup 外，也重新进入 sync outbox。
3. 桌面 `apply_push()` 只返回最高连续 ack；遇到 seq gap 时不越过缺口。
4. Android `SyncWorker` 只清理桌面明确 ack 的 outbox seq；若 ack 小于本批最大 seq，则保留后续事件并重试。
5. Backup worker 只有在本地 git commit 成功后才标记 queue done / clip backed_up；并会在后续周期重试既有未 push 的 commit。
6. 桌面版本号提升到 `1.2.1`。
7. 删除了临时权限探测文件 `tmp-delete-me.txt`。

补充测试：

- secret redaction 不返回真实长度；
- secret release 会产生 `clip_new` outbox 事件；
- gapped sync push 不推进 ack；
- backup commit 失败时 queue 保持 pending；
- 上次 push 失败后的本地 commit 可在后续周期重试 push。

## 2. 外部调研摘要

### 2.1 可借鉴但不应整体复用

- Ditto：Windows 经典剪切板管理器，强在本地历史、热键、托盘体验；弱在不覆盖 Android/Obsidian/个人词库同步。
- Maccy：macOS 轻量剪切板管理器，轮询间隔、历史搜索、快捷粘贴值得参考；平台不可复用。
- Espanso：跨平台文本扩展器，隐私本地优先、文件配置、应用级配置、脚本扩展值得参考；GPL 许可和项目目标不同，不应嵌入代码。

### 2.2 Android 输入法底座

- librime：BSD-3、跨平台 C++、中文音码/形码能力强，仍是 ClipVault 中文输入引擎首选。
- Trime：成熟 Android Rime 前端，适合作 spike 参考；GPL-3.0，不应 fork 进主 APK。
- fcitx5-android：LGPL-2.1，支持插件、RIME 插件、候选视图、剪贴板管理，是长期回退路线。
- HeliBoard：隐私键盘与 AOSP/OpenBoard 经验值得参考，但 GPL 许可使其更适合作 UI/隐私参考，而不是代码底座。

### 2.3 Android 平台边界

Android 10+ 限制后台访问剪贴板：除默认 IME 或前台 app 外，应用不能读取剪贴板。因此 ClipVault 继续采用 Share Target / 手动保存 / QS Tile / IME 显式保存是正确方向。

### 2.4 Secret Guard 后续增强

现有 regex + entropy 是 v1 可解释方案。后续可借鉴 gitleaks/trufflehog 一类 secret scanner 的规则集演化方式，但不要直接照搬大型扫描器；ClipVault 需要的是剪切板实时低延迟、低误伤的本地规则。

## 3. 下一关键节点建议

### v1.2.2 — 安全与运行闭环

目标：把 v1.2.1 代码修复转成可验证安装包。

- 本地跑桌面 pytest。
- Android clean build + core vector test。
- 重新打包 Desktop exe / installer / Android APK。
- 发布 GitHub Release v1.2.1 或 v1.2.2。
- Android token 从普通 SharedPreferences 迁到 Keystore-backed 存储；若不用 deprecated EncryptedSharedPreferences，则实现小型 AES-GCM wrapper。
- Android clip id 改为真正 ULID，或正式修订合同。

### v1.3 — Runtime 体验增强

目标：不做完整中文输入法，但让现有 companion IME 更好用。

- 统一 ClipVaultFacade：recent clips / memory / prompt / command / path 全部走 facade。
- 增加本地候选缓存与 UI 分组。
- 加入 password field / private mode 检测，敏感输入场景不显示推荐。
- 桌面 Web UI 增加同步状态与 backup 状态可见化。

### v2.1 — 中文输入底座 build PoC

目标：不是直接做完整输入法，而是验证成本。

- PoC A：NDK 编译最小 librime，JNI 输入拼音，返回候选。
- PoC B：fcitx5-android 插件方式，让 ClipVault 候选进入候选流。
- 裁决指标：许可风险、构建复杂度、候选混排控制权、用户安装复杂度、后续维护成本。

### v2.2 — CandidateMixer

目标：把 ClipVault 的剪切板、词库、Prompt、命令、路径候选混入输入候选栏。

- 输入引擎候选和 ClipVault 候选统一成 `Candidate`。
- PrivacyAwareFilter：密码框、银行卡、OTP、疑似密钥上下文全部禁用 ClipVault 候选。
- Ranking：prefix / recent / frequency / pinned / app-context / content-type 加权。
- 只记录统计事件，不记录普通键入正文。

## 4. 当前风险清单

1. Android `build.gradle.kts` 版本号仍需人工 bump 到 `1.2.1` / versionCode 6。本轮尝试通过 GitHub 工具更新该文件被安全检查拦截，未强行绕过。
2. 本轮没有在运行环境执行 pytest / Gradle / Android emulator；需要 Owner 或 CI 执行验证。
3. 当前直接提交到 `main`，未走 PR 流程。后续建议恢复分支 + PR + CI gate。
