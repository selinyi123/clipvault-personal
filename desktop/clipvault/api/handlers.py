"""Endpoint logic (API-1). Pure-ish: takes a service + connection, returns
(status_code, json_obj). The HTTP plumbing lives in server.py so this stays
directly unit-testable.
"""

from datetime import datetime, timedelta, timezone

from clipvault import __version__
from clipvault.core import secret_guard
from clipvault.core import suggest as suggest_core
from clipvault.service import ClipVaultService
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.memory_repo import KINDS as MEMORY_KINDS, MemoryRepo

_SUGGEST_WINDOW_DAYS = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clip_dict(clip, *, redact: bool) -> dict:
    content = secret_guard.redact_preview(clip.content) if redact else clip.content
    return {
        "id": clip.id,
        "content": content,
        "content_type": clip.content_type,
        "is_secret": clip.is_secret,
        "secret_level": clip.secret_level,
        "secret_reasons": clip.secret_reasons,
        "created_at": clip.created_at,
        "last_seen_at": clip.last_seen_at,
        "times_seen": clip.times_seen,
        "pinned": clip.pinned,
        "favorite": clip.favorite,
        "source_app": clip.source_app,
        "obsidian_path": clip.obsidian_path,
        "length": len(clip.content),
    }


def _memory_dict(m) -> dict:
    return {
        "id": m.id, "kind": m.kind, "text": m.text, "label": m.label,
        "pinned": m.pinned, "use_count": m.use_count,
        "last_used_at": m.last_used_at, "source": m.source,
    }


class Api:
    def __init__(self, service: ClipVaultService):
        self.service = service
        self.conn = service.conn
        self.clips = ClipsRepo(self.conn)
        self.memory = MemoryRepo(self.conn)

    def health(self) -> tuple[int, dict]:
        try:
            self.conn.execute("SELECT 1").fetchone()
            db_ok = True
        except Exception:
            db_ok = False
        return 200, {"status": "ok", "version": __version__, "db_ok": db_ok}

    def list_clips(self, params: dict) -> tuple[int, dict]:
        secret = params.get("secret") in ("1", "true", "True")
        limit = min(int(params.get("limit", "50") or "50"), 200)
        clips = self.clips.list_clips(
            query=params.get("q") or None,
            content_type=params.get("type") or None,
            secret=secret,
            limit=limit,
            before_id=params.get("before_id") or None,
        )
        return 200, {"clips": [_clip_dict(c, redact=secret) for c in clips]}

    def create_clip(self, body: dict) -> tuple[int, dict]:
        content = body.get("content")
        if not isinstance(content, str) or not content.strip():
            return 400, {"error": {"code": "bad_request", "message": "content required"}}
        outcome = self.service.handle_clipboard_text(content, body.get("source_app"))
        if outcome.clip is None:
            return 422, {"error": {"code": outcome.status, "message": "clip rejected"}}
        return 201, {"status": outcome.status,
                     "clip": _clip_dict(outcome.clip, redact=outcome.clip.is_secret)}

    def patch_clip(self, clip_id: str, body: dict) -> tuple[int, dict]:
        if self.clips.get(clip_id) is None:
            return 404, {"error": {"code": "not_found", "message": clip_id}}
        applied = {}
        for field in ("pinned", "favorite", "deleted"):
            if field in body:
                self.clips.set_flag(clip_id, field, bool(body[field]))
                applied[field] = bool(body[field])
        if not applied:
            return 400, {"error": {"code": "bad_request", "message": "no settable flag"}}
        return 200, {"id": clip_id, "applied": applied}

    def release_clip(self, clip_id: str) -> tuple[int, dict]:
        if self.service.release_clip(clip_id):
            return 200, {"id": clip_id, "released": True}
        return 404, {"error": {"code": "not_found_or_not_secret", "message": clip_id}}

    # --- memory (S007) ---

    def list_memory(self, params: dict) -> tuple[int, dict]:
        items = self.memory.list(
            kind=params.get("kind") or None,
            query=params.get("q") or None,
            limit=min(int(params.get("limit", "200") or "200"), 500),
        )
        return 200, {"memory": [_memory_dict(m) for m in items]}

    def create_memory(self, body: dict) -> tuple[int, dict]:
        kind = body.get("kind")
        text = body.get("text")
        if kind not in MEMORY_KINDS:
            return 400, {"error": {"code": "bad_kind", "message": f"kind in {MEMORY_KINDS}"}}
        if not isinstance(text, str) or not text.strip():
            return 400, {"error": {"code": "bad_request", "message": "text required"}}
        item = self.memory.upsert(kind, text, label=body.get("label"),
                                  pinned=bool(body.get("pinned", False)))
        return 201, {"memory": _memory_dict(item)}

    def delete_memory(self, item_id: str) -> tuple[int, dict]:
        if self.memory.soft_delete(item_id):
            return 200, {"id": item_id, "deleted": True}
        return 404, {"error": {"code": "not_found", "message": item_id}}

    def promote_clip(self, clip_id: str, body: dict | None = None) -> tuple[int, dict]:
        kind = (body or {}).get("kind")
        if kind is not None and kind not in MEMORY_KINDS:
            return 400, {"error": {"code": "bad_kind", "message": f"kind in {MEMORY_KINDS}"}}
        item = self.service.promote_clip(clip_id, kind)
        if item is None:
            return 404, {"error": {"code": "not_found_or_secret", "message": clip_id}}
        return 201, {"memory": _memory_dict(item)}

    def clip_actions(self, clip_id: str) -> tuple[int, dict]:
        clip = self.clips.get(clip_id)
        if clip is None:
            return 404, {"error": {"code": "not_found", "message": clip_id}}
        from clipvault.core import actions as action_rules
        chips = action_rules.recommend(clip.content_type, clip.is_secret)
        return 200, {"actions": [
            {"action": a.action, "label": a.label, "kind": a.kind} for a in chips
        ]}

    def use_memory(self, item_id: str) -> tuple[int, dict]:
        if self.memory.get(item_id) is None:
            return 404, {"error": {"code": "not_found", "message": item_id}}
        self.memory.bump_use(item_id, _now_iso())
        return 200, {"id": item_id, "used": True}

    # --- suggestions (S010, SUG-1) ---

    def suggest(self, params: dict, weights=None) -> tuple[int, dict]:
        prefix = params.get("prefix", "")
        app = params.get("app") or None
        limit = min(int(params.get("limit", "10") or "10"), 50)
        w = weights or self.service.config.weights()
        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=_SUGGEST_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")

        cands: list[suggest_core.Candidate] = []
        for m in self.memory.list(limit=500):
            cands.append(suggest_core.Candidate(
                id=m.id, kind=m.kind, text=m.text, label=m.label, pinned=m.pinned,
                use_count=m.use_count, last_used_at=m.last_used_at, origin="memory",
            ))
        for c in self.clips.suggest_candidates(since):
            cands.append(suggest_core.Candidate(
                id=c.id, kind=c.content_type, text=c.content[:200], pinned=c.pinned,
                use_count=c.times_seen, last_used_at=c.last_seen_at,
                source_app=c.source_app, origin="clip",
            ))
        ranked = suggest_core.rank(cands, prefix, app, w, now, limit)
        return 200, {"suggestions": [
            {"id": c.id, "kind": c.kind, "text": c.text, "origin": c.origin,
             "score": round(s, 4)}
            for c, s in ranked
        ]}

    def status(self) -> tuple[int, dict]:
        counts = self.clips.counts()
        pending = len(BackupQueueRepo(self.conn).pending_clip_ids())
        last_backup = self.conn.execute(
            "SELECT MAX(backed_up_at) FROM clips"
        ).fetchone()[0]
        return 200, {
            "clips_total": counts["total"],
            "quarantined": counts["secret"],
            "backup_pending": pending,
            "last_backup_at": last_backup,
        }
