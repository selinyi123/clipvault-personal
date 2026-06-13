# Slice 011 — Context Action Engine (Desktop, rules)

> Architect: Claude Fable 5 | 2026-06-13 | 状态：规格冻结
> 对应版本：v0.7（桌面侧）。按内容类型给出"下一步动作"芯片，纯规则、无 AI、无网络。

## 1. 目标

实现豆包式"对当前内容给出下一步动作"，但 v1 纯规则（PRODUCT_SPEC §5.6）：
content_type → 推荐动作 chips。动作映射到既有操作（promote 到指定 memory kind / copy /
release）。AI 动作（摘要/解释）属 P2，本片不做。

## 2. 允许触碰的文件

```text
desktop/clipvault/core/actions.py        # 纯规则 recommend()
desktop/clipvault/service.py             # promote_clip 接受可选 kind 覆盖
desktop/clipvault/api/handlers.py        # GET /api/clips/{id}/actions；promote 读 kind
desktop/clipvault/api/server.py          # 路由
desktop/clipvault/api/webui/app.js       # clip 卡片渲染推荐动作 chip
desktop/tests/**
docs/{HANDOFF,CONTRACTS}.md
```

## 3. 实现要求

1. **core/actions.py（纯，零 IO）**：
   - `@dataclass Action(action, label, kind=None)`，action ∈ promote|copy|release。
   - `recommend(content_type, is_secret) -> list[Action]`：
     - is_secret → `[release]` 唯一（不提供其他动作）。
     - command → 保存为常用命令(promote,command)；prompt → 归档为 Prompt(promote,prompt)；
       url/path → 保存到词库(promote,path)；code/error_log/text → 加入词库(promote,phrase)。
     - 非密钥末尾恒加 copy。
2. **service.promote_clip(clip_id, kind=None)**：kind 给定则用之（须合法），否则按 content_type 自动映射。
3. **handlers**：
   - `GET /api/clips/{id}/actions` → `{actions:[{action,label,kind}]}`（按该 clip 实际类型/密钥态）。
   - `POST /api/clips/{id}/promote` 读取 body/query 的可选 kind，传入 service。
4. **server**：路由 GET /api/clips/{id}/actions。
5. **Web UI**：clip 卡片用 recommend 的首个 promote 动作 label 替换"加入词库"按钮文案与目标 kind。

## 4. 验收门禁

- G11-1. 每种 content_type 返回预期 chips；末尾含 copy。
- G11-2. 密钥 clip 仅返回 release，无 copy/promote。
- G11-3. promote 带 kind=command 覆盖生效；非法 kind 被拒（400/ValueError）。
- G11-4. GET actions 端点对真实 clip 返回正确 chips（公开 vs 隔离）。
- G11-5. core/actions.py 无 IO（并入静态检查）。
- G11-6. 全部规则有测试。

## 5. 验证命令

```text
desktop> .venv\Scripts\python -m pytest -q
```
