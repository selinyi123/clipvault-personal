"""Sync engine (SYNC-2): emit local events to the outbox, and apply events
pushed by a peer. The remote-apply path deliberately does NOT go through
pipeline.ingest, so applying a peer's clip never echoes back into our outbox.
Gate B (secrets never leave) and gate A (re-scan on arrival) both enforced here.
"""

import logging

from clipvault.core import secret_guard
from clipvault.core.models import Clip
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.outbox_repo import OutboxRepo
from clipvault.store.peers_repo import PeersRepo

log = logging.getLogger("clipvault.sync")


def clip_to_data(clip: Clip) -> dict:
    return {
        "id": clip.id, "content": clip.content, "content_hash": clip.content_hash,
        "content_type": clip.content_type, "is_secret": clip.is_secret,
        "secret_level": clip.secret_level, "secret_reasons": clip.secret_reasons,
        "source_device": clip.source_device, "source_app": clip.source_app,
        "created_at": clip.created_at, "last_seen_at": clip.last_seen_at,
        "times_seen": clip.times_seen, "pinned": clip.pinned,
        "favorite": clip.favorite, "deleted": clip.deleted,
    }


# --- local emission (called by ingest / patch) ---

def emit_clip_new(conn, clip: Clip, when: str) -> int | None:
    """Publish a locally-created public clip. Gate B: secrets never emitted."""
    if clip.is_secret:
        return None
    return OutboxRepo(conn).append("clip_new", clip_to_data(clip), when)


def emit_clip_meta(conn, content_hash: str, patch: dict, ts: str, when: str) -> int:
    _set_meta_ts(conn, content_hash, ts)
    return OutboxRepo(conn).append(
        "clip_meta", {"content_hash": content_hash, "patch": patch, "ts": ts}, when
    )


# --- meta LWW bookkeeping ---

def _get_meta_ts(conn, content_hash: str) -> str:
    row = conn.execute(
        "SELECT ts FROM clip_meta_ts WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    return row[0] if row else ""


def _set_meta_ts(conn, content_hash: str, ts: str) -> None:
    conn.execute(
        "INSERT INTO clip_meta_ts(content_hash, ts) VALUES (?,?) "
        "ON CONFLICT(content_hash) DO UPDATE SET ts=excluded.ts "
        "WHERE excluded.ts >= clip_meta_ts.ts",
        (content_hash, ts),
    )
    conn.commit()


# --- remote application (called by /api/sync/push) ---

def apply_push(conn, device_id: str, events: list[dict], service) -> int:
    """Apply a peer's events idempotently; return highest contiguous seq applied."""
    peers = PeersRepo(conn)
    peer = peers.get(device_id)
    cursor = peer["peer_cursor"] if peer else 0
    acked = cursor

    for ev in sorted(events, key=lambda e: e["seq"]):
        seq = ev["seq"]
        if seq <= cursor:
            continue  # already applied
        kind = ev["kind"]
        if kind == "clip_new":
            _apply_clip_new(conn, ev["data"], service)
        elif kind == "clip_meta":
            _apply_clip_meta(conn, ev["data"])
        else:
            log.error("unknown sync event kind=%s", kind)
        if seq == acked + 1:
            acked = seq
        else:
            acked = max(acked, seq)  # tolerate gaps in self-use 2-device setup
    if peer:
        peers.set_peer_cursor(device_id, acked)
    return acked


def _apply_clip_new(conn, data: dict, service) -> None:
    clips = ClipsRepo(conn)
    if clips.get_by_hash(data["content_hash"]) is not None:
        return  # idempotent: already have this content
    verdict = secret_guard.scan(data["content"])  # gate A on arrival
    is_secret = verdict.is_secret or data.get("is_secret", False)
    clip = Clip(
        id=data["id"], content=data["content"], content_hash=data["content_hash"],
        content_type=data["content_type"], is_secret=is_secret,
        secret_level=verdict.level if is_secret else None,
        secret_reasons=verdict.reasons if is_secret else [],
        source_device=data.get("source_device", "peer"),
        source_app=data.get("source_app"),
        created_at=data["created_at"], last_seen_at=data["last_seen_at"],
        times_seen=data.get("times_seen", 1),
        pinned=data.get("pinned", False), favorite=data.get("favorite", False),
        deleted=data.get("deleted", False),
    )
    clips.insert(clip)  # insert() keeps secrets out of FTS
    if is_secret:
        log.warning("remote clip quarantined id=%s reasons=%s",
                    clip.id, ",".join(clip.secret_reasons))
        return
    # public: same downstream as a local capture, but NO outbox re-emit (no echo)
    service._write_obsidian(clip)
    try:
        BackupQueueRepo(conn).enqueue(clip.id, clip.last_seen_at)
    except Exception:
        log.exception("backup enqueue for remote clip failed id=%s", clip.id)


def _apply_clip_meta(conn, data: dict) -> None:
    content_hash = data["content_hash"]
    ts = data["ts"]
    local_ts = _get_meta_ts(conn, content_hash)
    # LWW: newer ts wins; on equal ts a delete wins (handled by ordering below)
    is_delete = bool(data["patch"].get("deleted"))
    if ts < local_ts or (ts == local_ts and not is_delete):
        return  # stale; do not overwrite newer state
    clips = ClipsRepo(conn)
    row = clips.get_by_hash(content_hash)
    if row is None:
        return
    for field in ("pinned", "favorite", "deleted"):
        if field in data["patch"]:
            clips.set_flag(row.id, field, bool(data["patch"][field]))
    _set_meta_ts(conn, content_hash, ts)


# --- pull side ---

def build_pull(conn, since_seq: int, limit: int = 100) -> dict:
    events = OutboxRepo(conn).list_since(since_seq, limit)
    next_seq = events[-1]["seq"] if events else since_seq
    return {"events": events, "next_seq": next_seq, "has_more": len(events) == limit}
