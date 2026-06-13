"""Endpoint logic (API-1). Pure-ish: takes a service + connection, returns
(status_code, json_obj). The HTTP plumbing lives in server.py so this stays
directly unit-testable.
"""

from clipvault import __version__
from clipvault.core import secret_guard
from clipvault.pipeline import ingest as pipeline
from clipvault.service import ClipVaultService
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo


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


class Api:
    def __init__(self, service: ClipVaultService):
        self.service = service
        self.conn = service.conn
        self.clips = ClipsRepo(self.conn)

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
