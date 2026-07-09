"""Sync engine (SYNC-2): emit local events to the outbox, and apply events
pushed by a peer. The remote-apply path deliberately does NOT go through
pipeline.ingest, so applying a peer's clip never echoes back into our outbox.
Gate B (secrets never leave) and gate A (re-scan on arrival) both enforced here.
"""

import json
import logging
import sqlite3

from clipvault.core import secret_guard
from clipvault.core.models import Clip
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.outbox_repo import OutboxRepo
from clipvault.store.peers_repo import PeersRepo

log = logging.getLogger("clipvault.sync")

SYNC_PULL_EVENT_LIMIT = 100
# Keep one pull page comfortably below mobile heap-risk territory while still
# allowing at least one default max-size clip (config.max_clip_bytes = 1 MiB)
# plus JSON envelope overhead. Large histories continue via has_more/next_seq.
SYNC_PULL_RESPONSE_BYTES = 4 * 1024 * 1024


class SyncPullEventTooLarge(ValueError):
    """A single outbox event cannot fit within the pull response budget."""

    def __init__(self, seq: int, event_bytes: int, max_bytes: int):
        self.seq = seq
        self.event_bytes = event_bytes
        self.max_bytes = max_bytes
        super().__init__(
            f"sync event seq={seq} is {event_bytes} bytes, exceeds pull budget {max_bytes}"
        )


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

def emit_clip_new(conn, clip: Clip, when: str, *, commit: bool = True) -> int | None:
    """Publish a locally-created public clip. Gate B: secrets never emitted."""
    if clip.is_secret:
        return None
    return OutboxRepo(conn).append("clip_new", clip_to_data(clip), when, commit=commit)


def emit_clip_meta(conn, content_hash: str, patch: dict, ts: str, when: str) -> int:
    for field in ("pinned", "favorite", "deleted"):
        if field in patch:
            _set_meta_ts(conn, content_hash, field, ts)
    return OutboxRepo(conn).append(
        "clip_meta", {"content_hash": content_hash, "patch": patch, "ts": ts}, when
    )


def _memory_data_is_secret(data: dict) -> bool:
    from clipvault.store.memory_repo import memory_contains_secret

    text = data.get("text")
    if not isinstance(text, str):
        return False
    return memory_contains_secret(text, data.get("label"))


def emit_memory_upsert(conn, item, when: str) -> int | None:
    """Publish public Personal Memory to peers.

    This is an independent SG-1 exit gate: callers cannot bypass it by handing
    us a legacy or otherwise unvalidated MemoryItem.
    """
    data = {
        "kind": item.kind, "text": item.text, "label": item.label,
        "pinned": item.pinned, "use_count": item.use_count, "source": item.source,
    }
    if _memory_data_is_secret(data):
        log.error("secret memory blocked at sync outbox boundary")
        return None
    _set_mem_ts(conn, item.kind, item.text, when)
    return OutboxRepo(conn).append("memory_upsert", data, when)


def emit_memory_delete(conn, kind: str, text: str, ts: str, when: str) -> int | None:
    if _memory_data_is_secret({"text": text}):
        log.error("secret memory delete blocked at sync outbox boundary")
        return None
    _set_mem_ts(conn, kind, text, ts)
    return OutboxRepo(conn).append(
        "memory_delete", {"kind": kind, "text": text, "ts": ts}, when
    )


# --- meta LWW bookkeeping ---

def _get_meta_ts(conn, content_hash: str, field: str) -> str:
    row = conn.execute(
        "SELECT ts FROM clip_meta_ts WHERE content_hash = ? AND field = ?",
        (content_hash, field),
    ).fetchone()
    return row[0] if row else ""


def _set_meta_ts(conn, content_hash: str, field: str, ts: str) -> None:
    conn.execute(
        "INSERT INTO clip_meta_ts(content_hash, field, ts) VALUES (?,?,?) "
        "ON CONFLICT(content_hash, field) DO UPDATE SET ts=excluded.ts "
        "WHERE excluded.ts >= clip_meta_ts.ts",
        (content_hash, field, ts),
    )
    conn.commit()


def _get_mem_ts(conn, kind: str, text: str) -> str:
    row = conn.execute(
        "SELECT ts FROM memory_meta_ts WHERE kind = ? AND text = ?", (kind, text)
    ).fetchone()
    return row[0] if row else ""


def _set_mem_ts(conn, kind: str, text: str, ts: str) -> None:
    conn.execute(
        "INSERT INTO memory_meta_ts(kind, text, ts) VALUES (?,?,?) "
        "ON CONFLICT(kind, text) DO UPDATE SET ts=excluded.ts "
        "WHERE excluded.ts >= memory_meta_ts.ts",
        (kind, text, ts),
    )
    conn.commit()


# --- remote application (called by /api/sync/push) ---

def _apply_one(conn, ev: dict, service) -> None:
    """Apply a single peer event. A malformed or unknown event is logged and
    treated as an unprocessable no-op (the caller still acks it) so one bad event
    from a version-skewed/buggy peer cannot crash the whole push batch and wedge
    sync — consistent with how unknown event kinds are already tolerated."""
    kind = ev.get("kind")
    data = ev.get("data")
    if not isinstance(data, dict):
        log.error("malformed sync event (data not object) kind=%s", kind)
        return
    try:
        if kind == "clip_new":
            _apply_clip_new(conn, data, service)
        elif kind == "clip_meta":
            _apply_clip_meta(conn, data)
        elif kind == "memory_upsert":
            _apply_memory_upsert(conn, data)
        elif kind == "memory_delete":
            _apply_memory_delete(conn, data)
        else:
            log.error("unknown sync event kind=%s", kind)
    except KeyError as exc:
        log.error("malformed sync event kind=%s missing key %s", kind, exc)
    except sqlite3.IntegrityError as exc:
        # A peer can send a seq-valid but semantically invalid event, for
        # example a clip_new with a duplicate id and different content hash.
        # Treat it like the other malformed seq-valid events above: ack as a
        # no-op so one permanently bad item cannot wedge sync forever, but do
        # not hide transient database failures such as locks or IO errors.
        log.error("malformed sync event kind=%s integrity error %s", kind, exc.__class__.__name__)


def apply_push(conn, device_id: str, events: list[dict], service) -> int:
    """Apply a peer's events idempotently; return highest contiguous seq applied.

    A gap must not advance the ack cursor. Otherwise the sender may delete an
    unacknowledged event and permanently lose it. Out-of-order/gapped events are
    still safe to apply because every event kind is idempotent. A malformed event
    (not an object, or no integer seq) cannot be ordered or acked, so it is
    dropped with a log; a structurally-bad-but-seq-valid event is acked as an
    unprocessable no-op (see _apply_one) so it does not wedge the batch.
    """
    peers = PeersRepo(conn)
    peer = peers.get(device_id)
    cursor = peer["peer_cursor"] if peer else 0
    acked = cursor

    orderable = []
    for ev in events:
        if isinstance(ev, dict) and isinstance(ev.get("seq"), int) and not isinstance(ev.get("seq"), bool):
            orderable.append(ev)
        else:
            log.error("sync event without integer seq, dropped")

    seen_batch_seqs = set()
    for ev in sorted(orderable, key=lambda e: e["seq"]):
        seq = ev["seq"]
        if seq <= cursor:
            continue  # already applied
        if seq in seen_batch_seqs:
            log.error("duplicate sync event seq=%d from device=%s, dropped", seq, device_id)
            continue
        seen_batch_seqs.add(seq)
        _apply_one(conn, ev, service)
        if seq == acked + 1:
            acked = seq
        elif seq > acked + 1:
            log.warning("sync gap from device=%s cursor=%d saw=%d", device_id, acked, seq)
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


def _apply_memory_upsert(conn, data: dict) -> None:
    from clipvault.store.memory_repo import MemoryRepo, SecretMemoryError

    try:
        MemoryRepo(conn).upsert(
            data["kind"], data["text"], label=data.get("label"),
            source=data.get("source", "manual"), pinned=data.get("pinned", False),
            use_count=data.get("use_count", 0),
        )
    except SecretMemoryError:
        # Treat a secret-shaped remote event as an acknowledged quarantine
        # no-op. Retrying it forever cannot make it safe and would wedge sync.
        log.error("remote secret memory rejected")


def _apply_memory_delete(conn, data: dict) -> None:
    from clipvault.store.memory_repo import MemoryRepo
    kind, text, ts = data["kind"], data["text"], data.get("ts", "")
    # LWW (CONTRACTS §5.2): a stale delete must not remove a locally newer item.
    if ts < _get_mem_ts(conn, kind, text):
        return
    repo = MemoryRepo(conn)
    item = repo.by_kind_text(kind, text)
    if item is not None:
        repo.soft_delete(item.id)
    _set_mem_ts(conn, kind, text, ts)


def _apply_clip_meta(conn, data: dict) -> None:
    content_hash = data["content_hash"]
    ts = data["ts"]
    patch = data["patch"]
    clips = ClipsRepo(conn)
    row = clips.get_by_hash(content_hash)
    if row is None:
        return
    # Per-field LWW (v1.8): each field's newest ts wins independently, so a newer
    # change to one field is never masked by an older change to another. On an
    # exact ts tie a delete wins (SYNC-2 delete-wins semantics).
    deleted_changed = False
    for field in ("pinned", "favorite", "deleted"):
        if field not in patch:
            continue
        local_ts = _get_meta_ts(conn, content_hash, field)
        is_delete = field == "deleted" and bool(patch[field])
        if ts < local_ts or (ts == local_ts and not is_delete):
            continue  # stale for this field
        clips.set_flag(row.id, field, bool(patch[field]))
        _set_meta_ts(conn, content_hash, field, ts)
        if field == "deleted":
            deleted_changed = True
    # Re-back-up only when the deletion state changed (e.g. a peer's deletion) so
    # restore.py doesn't resurrect it (GHB-1.1). Cosmetic flags are not mirrored.
    # Secrets are never backed up, so skip them.
    if deleted_changed and not row.is_secret:
        BackupQueueRepo(conn).reenqueue(row.id, ts)


# --- pull side ---

def _event_wire_size(event: dict) -> int:
    return len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def build_pull(conn, since_seq: int, limit: int = SYNC_PULL_EVENT_LIMIT,
               max_bytes: int = SYNC_PULL_RESPONSE_BYTES) -> dict:
    raw_events = OutboxRepo(conn).list_since(since_seq, limit)
    events = []
    next_seq = since_seq
    used_bytes = 0
    stopped_by_budget = False
    for event in raw_events:
        if event["kind"] in ("memory_upsert", "memory_delete") and _memory_data_is_secret(event["payload"]):
            # Legacy rows may predate the Memory SG-1 gate. Do not send them,
            # but advance next_seq over the quarantined event so peers do not
            # request it forever.
            log.error("legacy secret memory blocked at sync pull seq=%s", event["seq"])
            next_seq = event["seq"]
            continue
        event_bytes = _event_wire_size(event)
        if event_bytes > max_bytes:
            if events:
                stopped_by_budget = True
                break
            log.error(
                "sync pull event too large seq=%s bytes=%s max_bytes=%s",
                event["seq"], event_bytes, max_bytes,
            )
            raise SyncPullEventTooLarge(event["seq"], event_bytes, max_bytes)
        if events and used_bytes + event_bytes > max_bytes:
            stopped_by_budget = True
            break
        events.append(event)
        used_bytes += event_bytes
        next_seq = event["seq"]
    return {
        "events": events,
        "next_seq": next_seq,
        "has_more": stopped_by_budget or len(raw_events) == limit,
    }


def pull_blocked_summary(conn, max_bytes: int = SYNC_PULL_RESPONSE_BYTES) -> dict | None:
    """Return content-safe status if any peer is blocked by an oversized pull event.

    This is for local status/UI diagnostics only. It must not expose clip text,
    payload fields, bearer tokens, hostnames, or device identifiers.
    """
    peer_rows = conn.execute("SELECT my_acked_seq FROM sync_peers").fetchall()
    if not peer_rows:
        return None

    blocked = []
    outbox = OutboxRepo(conn)
    for row in peer_rows:
        for event in outbox.list_since(int(row["my_acked_seq"]), SYNC_PULL_EVENT_LIMIT):
            if event["kind"] in ("memory_upsert", "memory_delete") and _memory_data_is_secret(event["payload"]):
                continue
            event_bytes = _event_wire_size(event)
            if event_bytes > max_bytes:
                blocked.append({
                    "seq": event["seq"],
                    "event_bytes": event_bytes,
                })
            break

    if not blocked:
        return None

    first = min(blocked, key=lambda item: item["seq"])
    return {
        "code": "sync_event_too_large",
        "blocked_devices": len(blocked),
        "first_seq": first["seq"],
        "event_bytes": first["event_bytes"],
        "max_bytes": max_bytes,
    }
