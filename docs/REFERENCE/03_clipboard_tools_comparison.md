# 剪切板工具对比与功能提炼

## 1. 参考工具

| # | 工具 | 类型 | 可参考能力 |
|---:|---|---|---|
| 1 | CopyQ | 高级剪切板 | tabs、搜索、脚本、编辑 |
| 2 | Ditto | Windows 剪切板 | 历史、搜索、固定 |
| 3 | ClipboardFusion | 剪切板增强 | 宏、文本转换、同步 |
| 4 | ClipClip | Windows 剪切板 | 文件夹、搜索、片段 |
| 5 | ClipAngel | Windows 开源 | 多格式、收藏、编辑 |
| 6 | Windows Clipboard History | 系统功能 | Win+V、固定、同步 |
| 7 | PowerToys Advanced Paste | 智能粘贴 | Markdown/JSON 转换、OCR |
| 8 | Maccy | macOS 剪切板 | 极简快速搜索 |
| 9 | Raycast Clipboard History | macOS 剪切板 | 本地加密、敏感忽略 |
| 10 | Alfred Clipboard | macOS 效率工具 | workflow 和 snippets |
| 11 | Paste | Apple 生态 | 视觉时间线、跨设备 |
| 12 | PastePal | Apple 生态 | collections、Share Extension |
| 13 | PasteNow | Apple 生态 | 剪切板同步 |
| 14 | Clipy | macOS 开源 | 简单历史 |
| 15 | Flycut | 开发者剪切板 | 代码片段历史 |
| 16 | Pano | GNOME 剪切板 | 图像、链接、代码、文件预览 |
| 17 | Obsidian Web Clipper | Obsidian 官方 | 网页高亮、剪藏 |
| 18 | MarkDownload | Markdown 剪藏 | 网页转 Markdown |
| 19 | Readwise Reader + Obsidian | 阅读知识库 | 高亮同步 |
| 20 | Pieces for Developers | 开发者记忆 | 代码片段、上下文记忆 |

## 2. 最终提炼能力

```text
历史记录
搜索
固定/收藏
多格式保存
脚本能力
内容转换
Markdown 剪藏
Obsidian 入库
代码片段管理
本地加密
敏感内容忽略
跨端同步
```

## 3. 我们需要的功能

```text
1. 桌面剪切板监听
2. Android 手动保存剪切板
3. Android 分享到 ClipVault
4. 双端剪切板同步
5. SQLite 本地历史
6. 全文搜索
7. 规则分类
8. Prompt / Code / URL / Log / Command 识别
9. Obsidian Markdown 入库
10. GitHub 私有仓库备份
11. Secret Guard
12. 收藏 / Snippet
13. 使用频率统计
14. 最近历史
15. 跨设备粘贴
```

## 4. 对比结论

```text
剪切板工具不懂知识库；
知识库工具不监听剪切板；
AI 工具不够本地优先；
Git 备份工具不懂敏感内容。

ClipVault 的价值是：
跨端剪切板同步 + 智能分类 + Obsidian 自动入库 + GitHub 私有备份 + 输入法级调用 + 个人常用词记忆。
```
