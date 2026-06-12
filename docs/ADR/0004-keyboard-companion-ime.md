# ADR-0004: Keyboard Personal 是伴随式 IME（companion IME）

状态：Accepted（2026-06-12）

## 背景
不做拼音引擎（Non-goal），但 Android 的 IME 是全有或全无：成为当前输入法才能读剪切板、commitText。中文用户不可能把无拼音的键盘设为默认。

## 决策
ClipVault Keyboard 是用户按需切入的第二输入法：
- 通过系统输入法切换键（或 IME picker）临时切入；
- 面板完成粘贴/保存后，一键切回上一个输入法（`switchToPreviousInputMethod`）；
- 不实现任何文字输入引擎，键面即面板。

## 平台事实（写死，不要反复重新调研）
- Android 10+：只有前台应用或**当前默认 IME** 能读 ClipboardManager。
- 切入 ClipVault Keyboard 期间它就是当前 IME → 可读剪切板、可 commitText。
- 切走后立即失去剪切板访问 → Android 端不存在"后台监听"路线，采集主路径是 Share Target。

## 隐私不变量（G2 门禁）
- ime/ 模块零网络依赖（构建期依赖清单检查）。
- composing text / 按键流不写入任何存储；唯一写路径是用户显式点击"保存"。

## 后果
- 体验上多一次"切输入法"动作。缓解：切回键放在面板固定位置；这是平台约束下的最优解。
