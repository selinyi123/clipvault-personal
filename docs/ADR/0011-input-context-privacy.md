# ADR-0011: 输入上下文隐私会话闸门

状态：Accepted（2026-07-02）。落实 `KEYBOARD_PRIVACY` §2 与 KBD-4/KBD-5，不改变 L0–L4 语义。

## 背景

`InputMethodService` 的输入 View 会跨 editor 复用，而 `onStartInput()` 会随焦点字段变化反复调用。
仅在候选查询启动前检查 `EditorInfo` 不足以保护敏感字段：

- 普通字段已渲染的 ClipVault 候选可能在切到密码/incognito 字段后仍留在 View 中；
- 普通字段发起的异步 Room 查询可能在切换后回调，把旧结果重新填入敏感字段；
- Panel 的“保存剪贴板”虽是显式动作，但此前没有在敏感上下文短路，违反“不保存、不同步”。

三者都不是 SG-1 内容识别问题，而是输入会话生命周期的 TOCTOU 问题。

## 决策

1. 两套 IME 继续复用 `PrivacyAwareFilter` 判定密码变体、禁建议与
   `IME_FLAG_NO_PERSONALIZED_LEARNING`。
2. 新增纯状态 `ImePrivacySession`：每次 `onStartInput` 生成单调 generation token；
   `onFinishInput` 使当前会话失效并恢复 fail-closed。
3. 候选异步结果只有在 token 仍属于当前 generation 且当前允许个人数据时才能应用。
4. 进入敏感字段或结束输入时立即清空/替换已经渲染的候选；候选点击时再次检查当前会话。
5. Panel 显式保存必须在**读取剪贴板前**检查会话；worker 写入前再次检查同一 token。
   敏感上下文中保存按钮禁用，不产生 Room/outbox 写入。普通字段中的显式点击是授权点；若 worker
   启动前会话已经变化则取消，一旦 `saveExplicit` 已开始则不跨线程回滚该次用户授权的写入。
6. `ImePrivacySession` 不读取正文、不持久化、不联网；它只保存 generation 与布尔隐私状态。

## 后果

- 普通字段之间切换会使旧异步结果失效；Panel 从敏感/结束态返回普通字段时重新加载当前标签，
  Full Keyboard 回到待调取状态。未经过敏感/结束态的普通→普通切换可保留已经渲染的候选。
- 取消过时结果可能牺牲一次候选刷新，但隐私优先于短暂 UI 完整性。
- host-JVM 单测覆盖 fail-closed、敏感阻断、generation 失效与恢复；真实 View 清理、按钮状态和
  `InputConnection` 行为仍需 device/emulator instrumented gate。

## 明确不做

- 不记录 editor 正文、composing text 或按键流；不增加 typed-text 学习。
- 不在 IME 内增加网络请求。
- 不把设备 UI 脚手架伪装成已执行的真机证据。

## 关联

- [ADR-0008](0008-v1-as-runtime.md)
- [KEYBOARD_PRIVACY](../KEYBOARD_PRIVACY.md)
- [CONTRACTS_KEYBOARD](../CONTRACTS_KEYBOARD.md) KBD-4/KBD-5
- [INSTRUMENTED_QA_BACKLOG](../INSTRUMENTED_QA_BACKLOG.md)
