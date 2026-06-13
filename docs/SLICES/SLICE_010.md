# Slice 010 — Suggestion Engine (Desktop)

> Architect: Claude Fable 5 | 2026-06-13 | 状态：规格冻结
> 对应版本：v0.6（桌面侧）。确定性续词/常用推荐（SUG-1），无 ML、无网络。

## 1. 目标

实现 SUG-1 评分与排序：以 Personal Memory 项 + 近期高频 clip 为候选，按
前缀/子串匹配 + 频率(对数)×时间衰减 + pinned/app 加权排序。经 /api/suggest 暴露，
使用计数可回写。core 评分纯函数（零 IO），权重来自 CFG-1 [suggest]。

## 2. 允许触碰的文件

```text
desktop/clipvault/core/suggest.py        # 纯评分（Candidate/Weights/score/rank）
desktop/clipvault/config.py              # [suggest] 权重载入
desktop/clipvault/store/clips_repo.py    # suggest_candidates 查询
desktop/clipvault/api/handlers.py        # /api/suggest + /api/memory/{id}/use
desktop/clipvault/api/server.py          # 路由
desktop/tests/**
docs/{HANDOFF,CONTRACTS,CONTRACTS}.md
```

## 3. 实现要求

1. **core/suggest.py（纯，零 IO；可用 math/datetime）**：
   - `@dataclass Candidate(id, kind, text, label, pinned, use_count, last_used_at, source_app, origin)`
   - `@dataclass Weights(pinned=3.0, prefix=1.5, substr=0.6, freq=1.0, app=0.5, half_life_days=14.0)`
   - `score(c, query, app, w, now) -> float | None`：
     match：text 或 label 前缀(忽略大小写)→prefix；否则子串→substr；否则 None（剔除）。
     query 为空时 match 记 0（不剔除）。
     freq 项：`w.freq * ln(1+use_count) * exp(-days_since_last_use / half_life)`；无 last_used 记衰减=1。
     app 项：`w.app * (source_app == app ? 1 : 0)`。
     pinned 项：`w.pinned * pinned`。
   - `rank(cands, query, app, w, now, limit) -> list[(Candidate, score)]`：score DESC，平手 last_used_at DESC。
2. **config.py**：Config 增 suggest 权重字段，从 `[suggest]` 读取（缺省用 Weights 默认）。
3. **clips_repo.suggest_candidates(since_iso)**：非密钥非删除、(favorite=1 或 times_seen≥3)、
   last_seen_at≥since 的 clip。
4. **handlers**：
   - `GET /api/suggest?prefix=&app=&limit=10`：候选 = memory.list() ∪ clips.suggest_candidates(30d前)，
     转 Candidate，调 rank，返回 `{suggestions:[{id,kind,text,score,origin}]}`。
   - `POST /api/memory/{id}/use`：bump_use（点击回写）。
5. **server**：路由 GET /api/suggest、POST /api/memory/{id}/use。

## 4. 验收门禁

- F1. 纯前缀匹配排在子串匹配前；不匹配项被剔除。
- F2. query 为空时返回按 pinned+频率/衰减排序的"最近常用"，不剔除。
- F3. pinned 项显著靠前（权重 3.0）。
- F4. 频率相同情况下，最近使用的衰减更小、排更前；很久未用的被压低。
- F5. app 匹配的候选获得加成。
- F6. 平手时按 last_used_at DESC。
- F7. 候选集合并 memory + 高频 clip；低频 clip（times_seen<3 且非收藏）不进候选。
- F8. /api/memory/{id}/use 使 use_count+1。
- F9. core/suggest.py 无 IO import（并入 A10 静态检查）。
- F10. 权重改 config 生效（注入 Weights 验证）。

## 5. 验证命令

```text
desktop> .venv\Scripts\python -m pytest -q
```
