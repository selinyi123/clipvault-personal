"""Endpoint logic (API-1). Pure-ish: takes a service + connection, returns
(status_code, json_obj). The HTTP plumbing lives in server.py so this stays
directly unit-testable.
"""

from datetime import datetime, timedelta, timezone
import re

from clipvault import __version__
from clipvault.core import secret_guard
from clipvault.core import suggest as suggest_core
from clipvault.service import ClipVaultService
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.memory_repo import (
    KINDS as MEMORY_KINDS,
    MemoryRepo,
    SecretMemoryError,
)
from clipvault.store.peers_repo import PeersRepo
from clipvault.store.unit_of_work import unit_of_work
from clipvault.sync import engine as sync_engine
from clipvault.sync.pairing import Pairing, hash_token

_SUGGEST_WINDOW_DAYS = 30
_DEVICE_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,80}$")
_DEVICE_NAME_MAX_CHARS = 80
_SYNC_PUSH_EVENT_LIMIT = 100
_OUTBOX_BASE_SEQ_MAX = 9_223_372_036_854_775_807


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
        # Secret previews must not leak exact content length (CONTRACTS §4.3).
        "length": None if redact else len(clip.content),
    }


def _memory_dict(m) -> dict:
    return {
        "id": m.id, "kind": m.kind, "text": m.text, "label": m.label,
        "pinned": m.pinned, "use_count": m.use_count,
        "last_used_at": m.last_used_at, "source": m.source,
    }


def _bad_param(name: str, message: str) -> tuple[int, dict]:
    return 400, {"error": {"code": "bad_request", "message": f"{name}: {message}"}}


def _normalize_device_name(value) -> str:
    if value is None:
        return "device"
    if not isinstance(value, str):
        raise ValueError("device_name must be a string")
    name = value.strip()
    if not name:
        return "device"
    if len(name) > _DEVICE_NAME_MAX_CHARS:
        raise ValueError(f"device_name must be at most {_DEVICE_NAME_MAX_CHARS} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise ValueError("device_name must not contain control characters")
    return name


def _int_param(params: dict, name: str, default: int, *, min_value: int, max_value: int) -> int:
    """Parse integer query params without breaking existing high-limit callers.

    Older API behavior clamped values above the route max. Preserve that
    compatibility, but reject non-integers and values below the minimum.
    """
    raw = params.get(name, str(default)) or str(default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"must be an integer >= {min_value}")
    if value < min_value:
        raise ValueError(f"must be >= {min_value}")
    return min(value, max_value)


# Hosts that make the server reachable only from this machine. A loopback bind
# means a paired phone on the LAN cannot reach /api/pair or /api/sync/*, so the
# Web UI surfaces this when minting a pairing code (the default since v1.5.16 is
# loopback for safety; the user opts in to LAN exposure via config.host).
_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


def _lan_reachable(host: str) -> bool:
    return host not in _LOOPBACK_HOSTS


class Api:
    def __init__(self, service: ClipVaultService, pairing: Pairing | None = None):
        self.service = service
        self.conn = service.conn
        self.clips = ClipsRepo(self.conn)
        self.memory = MemoryRepo(self.conn)
        self.peers = PeersRepo(self.conn)
        self.pairing = pairing or Pairing()

    def health(self) -> tuple[int, dict]:
        try:
            self.conn.execute("SELECT 1").fetchone()
            db_ok = True
        except Exception:
            db_ok = False
        return 200, {"status": "ok", "version": __version__, "db_ok": db_ok}

    def list_clips(self, params: dict) -> tuple[int, dict]:
        secret = params.get("secret") in ("1", "true", "True")
        try:
            limit = _int_param(params, "limit", 50, min_value=1, max_value=200)
        except ValueError as exc:
            return _bad_param("limit", str(exc))
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
        clip = self.clips.get(clip_id)
        if clip is None:
            return 404, {"error": {"code": "not_found", "message": clip_id}}
        applied: dict[str, bool] = {}
        for field in ("pinned", "favorite", "deleted"):
            if field in body:
                value = body[field]
                if not isinstance(value, bool):
                    return 400, {
                        "error": {
                            "code": "bad_request",
                            "message": f"{field} must be boolean",
                        }
                    }
                applied[field] = value
        if not applied:
            return 400, {"error": {"code": "bad_request", "message": "no settable flag"}}
        now = _now_iso()
        with unit_of_work(self.conn):
            # Re-read after acquiring the writer lock.  The pre-check above
            # preserves the existing cheap 404 path, while this snapshot owns
            # the state comparison and all effects of the command.
            current = self.clips.get(clip_id)
            if current is None:
                return 404, {"error": {"code": "not_found", "message": clip_id}}
            deletion_changed = (
                "deleted" in applied
                and current.deleted != applied["deleted"]
            )
            quarantined = sync_engine.clip_requires_local_quarantine(current)
            for field, value in applied.items():
                self.clips.set_flag(clip_id, field, value, commit=False)
            if quarantined:
                # Gate C: a legacy is_secret=0 row may match newer Secret Guard
                # rules.  In particular, undelete must not reintroduce it into
                # FTS before the Owner explicitly releases it.
                self.clips.remove_from_search_index(clip_id, commit=False)
            else:
                # Emit metadata and its field timestamps in this same command.
                # Gate B keeps every secret flag mutation local-only.
                sync_engine.emit_clip_meta(
                    self.conn,
                    current.content_hash,
                    applied,
                    now,
                    now,
                    commit=False,
                )
                # Backup is a recovery snapshot, not a cosmetic flag mirror.
                if deletion_changed:
                    BackupQueueRepo(self.conn).reenqueue(
                        clip_id, now, commit=False
                    )
        return 200, {"id": clip_id, "applied": applied}

    def release_clip(self, clip_id: str) -> tuple[int, dict]:
        if self.service.release_clip(clip_id):
            return 200, {"id": clip_id, "released": True}
        return 404, {"error": {"code": "not_found_or_not_secret", "message": clip_id}}

    # --- memory (S007) ---

    def list_memory(self, params: dict) -> tuple[int, dict]:
        try:
            limit = _int_param(params, "limit", 200, min_value=1, max_value=500)
        except ValueError as exc:
            return _bad_param("limit", str(exc))
        items = self.memory.list(
            kind=params.get("kind") or None,
            query=params.get("q") or None,
            limit=limit,
        )
        return 200, {"memory": [_memory_dict(m) for m in items]}

    def create_memory(self, body: dict) -> tuple[int, dict]:
        kind = body.get("kind")
        text = body.get("text")
        label = body.get("label")
        pinned = body.get("pinned", False)
        if kind not in MEMORY_KINDS:
            return 400, {"error": {"code": "bad_kind", "message": f"kind in {MEMORY_KINDS}"}}
        if not isinstance(text, str) or not text.strip():
            return 400, {"error": {"code": "bad_request", "message": "text required"}}
        if label is not None and not isinstance(label, str):
            return 400, {"error": {
                "code": "bad_request", "message": "label must be a string or null"
            }}
        if not isinstance(pinned, bool):
            return 400, {"error": {
                "code": "bad_request", "message": "pinned must be boolean"
            }}
        try:
            sync_engine.validate_memory_upsert_payload({
                "kind": kind,
                "text": text.strip(),
                "label": label,
                "pinned": pinned,
                "use_count": 0,
                "source": "manual",
            })
        except ValueError:
            return 400, {"error": {
                "code": "bad_request",
                "message": "memory fields exceed limits or are invalid",
            }}
        now = _now_iso()
        try:
            with unit_of_work(self.conn):
                item = self.memory.upsert(
                    kind,
                    text,
                    label=label,
                    pinned=pinned,
                    commit=False,
                )
                sync_engine.emit_memory_upsert(
                    self.conn, item, now, commit=False
                )
        except SecretMemoryError:
            return 422, {"error": {
                "code": "secret_rejected",
                "message": "Personal Memory rejected by Secret Guard",
            }}
        except (ValueError, UnicodeError):
            return 400, {"error": {
                "code": "bad_request",
                "message": "memory fields exceed limits or are invalid",
            }}
        return 201, {"memory": _memory_dict(item)}

    def delete_memory(self, item_id: str) -> tuple[int, dict]:
        now = _now_iso()
        with unit_of_work(self.conn):
            item = self.memory.get(item_id)
            if item is None or not self.memory.soft_delete(
                item_id, commit=False
            ):
                return 404, {
                    "error": {"code": "not_found", "message": item_id}
                }
            sync_engine.emit_memory_delete(
                self.conn,
                item.kind,
                item.text,
                now,
                now,
                commit=False,
            )
        return 200, {"id": item_id, "deleted": True}

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
        try:
            limit = _int_param(params, "limit", 10, min_value=1, max_value=50)
        except ValueError as exc:
            return _bad_param("limit", str(exc))
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

    # --- pairing + sync (S006, SYNC-2) ---

    def list_peers(self) -> tuple[int, dict]:
        """Management (loopback-only): paired devices, without token hashes."""
        return 200, {"peers": self.peers.list_peers()}

    def unpair(self, device_id: str) -> tuple[int, dict]:
        """Management (loopback-only): revoke a paired device. Its bearer token
        stops authenticating immediately (lost/compromised-device recovery)."""
        if not self.peers.unpair(device_id):
            return 404, {"error": {"code": "not_found", "message": device_id}}
        return 200, {"device_id": device_id, "unpaired": True}

    def mint_pair_code(self) -> tuple[int, dict]:
        """Web UI (loopback) mints a one-time code to show the user."""
        host = self.service.config.host
        reachable = _lan_reachable(host)
        resp = {
            "code": self.pairing.mint_code(),
            "ttl_seconds": self.pairing.ttl,
            "lan_reachable": reachable,
        }
        if not reachable:
            resp["hint"] = (
                f"server.host 当前绑定回环（{host}），局域网设备无法配对或同步。"
                "请在可信网络下把 config.toml 的 [server] host 改为 0.0.0.0 并重启 ClipVault。"
            )
        return 200, resp

    def pair(self, body: dict) -> tuple[int, dict]:
        code = str(body.get("code", ""))
        device_id = body.get("device_id")
        if not isinstance(device_id, str) or not device_id:
            return 400, {"error": {"code": "bad_request", "message": "device_id required"}}
        if not _DEVICE_ID_RE.fullmatch(device_id):
            return 400, {"error": {"code": "bad_request", "message": "device_id must use 1-80 URL-safe characters"}}
        try:
            device_name = _normalize_device_name(body.get("device_name"))
        except ValueError as exc:
            return 400, {"error": {"code": "bad_request", "message": str(exc)}}
        outbox_base_seq = None
        if "outbox_base_seq" in body:
            outbox_base_seq = body["outbox_base_seq"]
            if (
                isinstance(outbox_base_seq, bool)
                or not isinstance(outbox_base_seq, int)
                or not 1 <= outbox_base_seq <= _OUTBOX_BASE_SEQ_MAX
            ):
                return 400, {"error": {
                    "code": "bad_request",
                    "message": (
                        "outbox_base_seq must be an integer between 1 and "
                        f"{_OUTBOX_BASE_SEQ_MAX}"
                    ),
                }}
        if self.pairing.is_rate_limited():
            return 429, {"error": {"code": "rate_limited", "message": "too many attempts, try again shortly"}}
        def persist_token(token: str) -> None:
            # A pairing response must represent a durable peer row. A nested
            # SAVEPOINT would let an outer rollback consume the one-time code
            # after this callback returns, so fail closed on a dirty connection.
            if self.conn.in_transaction:
                raise RuntimeError("pairing requires an idle database connection")
            with unit_of_work(self.conn):
                self.peers.upsert_pair(
                    device_id,
                    device_name,
                    hash_token(token),
                    _now_iso(),
                    peer_cursor=(
                        outbox_base_seq - 1
                        if outbox_base_seq is not None
                        else None
                    ),
                    commit=False,
                )

        token = self.pairing.redeem(code, persist_token=persist_token)
        if token is None:
            return 403, {"error": {"code": "bad_code", "message": "invalid or expired code"}}
        response = {"token": token, "server_device": self.service.config.device_id}
        if outbox_base_seq is not None:
            response["outbox_base_seq"] = outbox_base_seq
        return 200, response

    def _auth_device(self, token: str | None) -> dict | None:
        if not token:
            return None
        return self.peers.by_token_hash(hash_token(token))

    def auth_ok(self, token: str | None) -> bool:
        return self._auth_device(token) is not None

    def sync_push(self, token: str | None, body: dict) -> tuple[int, dict]:
        peer = self._auth_device(token)
        if peer is None:
            return 401, {"error": {"code": "unauthorized", "message": "bad token"}}
        device_id = peer["device_id"]
        events = body.get("events", [])
        if not isinstance(events, list):
            return 400, {"error": {"code": "bad_request", "message": "events must be an array"}}
        if len(events) > _SYNC_PUSH_EVENT_LIMIT:
            return 400, {"error": {
                "code": "bad_request",
                "message": f"events must contain at most {_SYNC_PUSH_EVENT_LIMIT} items",
            }}
        acked = sync_engine.apply_push(self.conn, device_id, events, self.service)
        self.peers.touch_last_seen(device_id, _now_iso())
        return 200, {"acked_upto": acked}

    def sync_pull(self, token: str | None, params: dict) -> tuple[int, dict]:
        peer = self._auth_device(token)
        if peer is None:
            return 401, {"error": {"code": "unauthorized", "message": "bad token"}}
        device_id = peer["device_id"]
        try:
            since = _int_param(params, "since_seq", 0, min_value=0, max_value=9_223_372_036_854_775_807)
        except ValueError as exc:
            return _bad_param("since_seq", str(exc))
        try:
            result = sync_engine.build_pull(self.conn, since)
        except sync_engine.SyncPullEventTooLarge as exc:
            return 413, {"error": {
                "code": "sync_event_too_large",
                "message": (
                    f"sync event seq={exc.seq} exceeds the pull response budget "
                    f"({exc.event_bytes}>{exc.max_bytes} bytes)"
                ),
            }}
        self.peers.set_my_acked(device_id, since)
        self.peers.touch_last_seen(device_id, _now_iso())
        return 200, result

    def status(self) -> tuple[int, dict]:
        counts = self.clips.counts()
        pending = len(BackupQueueRepo(self.conn).pending_clip_ids())
        last_backup = self.conn.execute(
            "SELECT MAX(backed_up_at) FROM clips"
        ).fetchone()[0]
        return 200, {
            "version": __version__,
            "clips_total": counts["total"],
            "quarantined": counts["secret"],
            "backup_pending": pending,
            "last_backup_at": last_backup,
            # Aggregate-only queue health. Never expose clip ids, content,
            # Vault paths, or stored error details through the status API.
            "obsidian_retry": self.service.obsidian_retry_stats(),
            "lan_reachable": _lan_reachable(self.service.config.host),
            "sync": {
                **self.peers.summary(),
                "blocked_pull": sync_engine.pull_blocked_summary(self.conn),
            },
        }
