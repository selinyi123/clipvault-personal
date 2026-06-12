# 输入法工具对比与功能提炼

## 1. 参考输入法产品

| # | 产品 | 类型 | 可参考功能 |
|---:|---|---|---|
| 1 | Microsoft SwiftKey | 商业输入法 | Android 与 Windows Cloud Clipboard 同步 |
| 2 | Samsung Keyboard | 系统输入法 | Galaxy 生态剪切板/跨设备联动 |
| 3 | Gboard | 系统输入法 | 剪切板面板、智能候选、语音 |
| 4 | QQ 输入法 | 商业输入法 | 云剪贴、PC/手机复制粘贴 |
| 5 | 搜狗输入法 | 商业输入法 | 剪贴板、常用语、词库 |
| 6 | 百度输入法 | 商业输入法 | AI 写作、云同步、剪切板 |
| 7 | 讯飞输入法 | 商业输入法 | 语音、剪贴板、AI 处理 |
| 8 | Yandex Keyboard | 商业输入法 | 剪贴板、本地数据提示 |
| 9 | Facemoji Keyboard | 商业输入法 | 素材、剪贴板、快捷发送 |
| 10 | Fleksy | 商业输入法 | mini-app、手势 |
| 11 | Grammarly Keyboard | 商业输入法 | 改写、语法、AI 建议 |
| 12 | Typewise | 商业输入法 | 隐私、离线 AI |
| 13 | CleverType AI Keyboard | AI 输入法 | 改写、翻译、AI 助手 |
| 14 | Clipboard - Paste Keyboard | 剪切板键盘 | 剪切板 + 键盘 + 同步 |
| 15 | Phraseboard | 短语键盘 | 常用短语分类粘贴 |
| 16 | TextExpander Keyboard | Snippet 键盘 | 片段扩展、模板 |
| 17 | Apple Keyboard + Universal Clipboard | 系统生态 | Apple 设备跨端复制 |
| 18 | Windows + Phone Link | 系统生态 | Android/Windows 跨端复制 |
| 19 | AnySoftKeyboard | 开源输入法 | 开源、多语言、隐私 |
| 20 | FlorisBoard | 开源输入法 | 现代 UI、Smartbar |
| 21 | HeliBoard | 开源输入法 | 离线、无联网权限 |
| 22 | OpenBoard | 开源输入法 | AOSP 派生 |
| 23 | FUTO Keyboard | 隐私输入法 | 离线预测、隐私 |
| 24 | Unexpected Keyboard | 开源输入法 | 程序员符号输入 |
| 25 | Thumb-Key | 开源输入法 | 极简滑动输入 |
| 26 | Simple Keyboard | 开源输入法 | 极简、无联网权限 |
| 27 | Fossify Keyboard | 开源输入法 | 可审计、安全 |
| 28 | CleverKeys | 开源输入法 | 剪切板历史、手势 |
| 29 | Hacker’s Keyboard | 开源输入法 | Ctrl、Tab、方向键 |
| 30 | Fcitx5 for Android | 开源输入法框架 | 插件化、剪切板、RIME |
| 31 | Trime / 同文输入法 | RIME Android 前端 | YAML 配置、输入方案 |
| 32 | AOSP LatinIME | Android 开源键盘 | 系统键盘基础 |
| 33 | RIME / librime | 输入法引擎 | YAML、跨平台 |
| 34 | OpenBangla Keyboard | 开源输入法 | 多输入方式 |

## 2. 最重要参考对象

```text
SwiftKey
微信输入法
豆包输入法
Gboard
TextExpander
Phraseboard
RIME / Trime
Fcitx5 for Android
```

## 3. 输入法路线结论

```text
ClipVault 不应该做完整输入法；
应该做 Android 输入法级知识面板。

ClipVault Keyboard Personal =
剪切板面板
+ 常用词
+ 常用短语
+ 常用 Prompt
+ 常用命令
+ 一键粘贴
+ 一键入库
+ 续词推荐
```
