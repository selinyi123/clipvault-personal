"""Sync engine (SYNC-2): emit local events to the outbox, and apply events
pushed by a peer. The remote-apply path deliberately does NOT go through
pipeline.ingest, so applying a peer's clip never echoes back into our outbox.
Gate B (secrets never leave) and gate A (re-scan on arrival) both enforced here.
"""

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone

from clipvault.core import normalize, secret_guard
from clipvault.core.models import CONTENT_TYPES, MEMORY_KINDS, Clip
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.obsidian_queue_repo import ObsidianQueueRepo
from clipvault.store.outbox_repo import OutboxRepo
from clipvault.store.peers_repo import PeersRepo
from clipvault.store.unit_of_work import unit_of_work

log = logging.getLogger("clipvault.sync")

_CLIP_ID_RE = re.compile(r"^[0-9A-Za-z]{1,128}$")
_KNOWN_EVENT_KINDS = frozenset(
    ("clip_new", "clip_meta", "memory_upsert", "memory_delete")
)
_MEMORY_SOURCES = frozenset(("manual", "derived", "obsidian_import", "github_import"))
_MAX_MEMORY_LABEL_BYTES = 4 * 1024

SYNC_PULL_EVENT_LIMIT = 100
# SQLite rows are decoded before wire budgeting. Fetch a small internal page so
# several near-limit escaped payloads cannot be materialised at once; repeated
# pull pages still preserve the public <=100-event protocol contract.
SYNC_PULL_FETCH_LIMIT = 8
# Android accepts at most 7 MiB for the complete pull response. Reserve 64 KiB
# for the response envelope, commas, cursor fields, and bounded event metadata.
# The remaining page/event budget still covers a valid 1 MiB clip whose every
# input byte becomes a six-byte JSON control-character escape.
SYNC_PULL_HTTP_RESPONSE_BYTES = 7 * 1024 * 1024
SYNC_PULL_RESPONSE_ENVELOPE_BYTES = 64 * 1024
SYNC_PULL_RESPONSE_BYTES = SYNC_PULL_HTTP_RESPONSE_BYTES - SYNC_PULL_RESPONSE_ENVELOPE_BYTES


class SyncPullEventTooLarge(ValueError):
    """A single outbox event cannot fit within the pull response budget."""

    def __init__(self, seq: int, event_bytes: int, max_bytes: int):
        self.seq = seq
        self.event_bytes = event_bytes
        self.max_bytes = max_bytes
        super().__init__(
            f"sync event seq={seq} is {event_bytes} bytes, exceeds pull budget {max_bytes}"
        )


class MalformedSyncEvent(ValueError):
    """A seq-valid peer event that is unsafe or impossible to apply."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_utc_timestamp(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _valid_content_hash(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _valid_memory_text(value) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value == value.strip()
        and len(value.encode("utf-8")) <= normalize.DEFAULT_MAX_CLIP_BYTES
    )


def _validate_clip_meta(data: dict) -> None:
    content_hash = data.get("content_hash")
    patch = data.get("patch")
    ts = data.get("ts")
    if not _valid_content_hash(content_hash):
        raise MalformedSyncEvent("invalid clip metadata hash")
    if not _valid_utc_timestamp(ts):
        raise MalformedSyncEvent("invalid clip metadata timestamp")
    if not isinstance(patch, dict) or not patch:
        raise MalformedSyncEvent("invalid clip metadata patch")
    if any(field not in ("pinned", "favorite", "deleted") for field in patch):
        raise MalformedSyncEvent("invalid clip metadata field")
    if any(not isinstance(value, bool) for value in patch.values()):
        raise MalformedSyncEvent("invalid clip metadata value")


def _validate_memory_upsert(data: dict) -> None:
    kind = data.get("kind")
    text = data.get("text")
    label = data.get("label")
    pinned = data.get("pinned", False)
    use_count = data.get("use_count", 0)
    source = data.get("source", "manual")
    if not isinstance(kind, str) or kind not in MEMORY_KINDS:
        raise MalformedSyncEvent("invalid memory kind")
    if not _valid_memory_text(text):
        raise MalformedSyncEvent("invalid memory text")
    if label is not None and (
        not isinstance(label, str)
        or len(label.encode("utf-8")) > _MAX_MEMORY_LABEL_BYTES
    ):
        raise MalformedSyncEvent("invalid memory label")
    if not isinstance(pinned, bool):
        raise MalformedSyncEvent("invalid memory pinned flag")
    if (
        not isinstance(use_count, int)
        or isinstance(use_count, bool)
        or use_count < 0
        or use_count > 2_147_483_647
    ):
        raise MalformedSyncEvent("invalid memory use count")
    if (
        not isinstance(source, str)
        or source not in _MEMORY_SOURCES
        or _has_control_chars(source)
    ):
        raise MalformedSyncEvent("invalid memory source")


def _validate_memory_delete(data: dict) -> None:
    kind = data.get("kind")
    text = data.get("text")
    if not isinstance(kind, str) or kind not in MEMORY_KINDS:
        raise MalformedSyncEvent("invalid memory kind")
    if not _valid_memory_text(text):
        raise MalformedSyncEvent("invalid memory text")
    if not _valid_utc_timestamp(data.get("ts")):
        raise MalformedSyncEvent("invalid memory timestamp")


def _validated_remote_clip(data: dict, *, max_bytes: int) -> Clip:
    """Validate the wire contract before content reaches SQLite or Markdown."""

    required = ("id", "content", "content_hash", "content_type", "created_at", "last_seen_at")
    if any(key not in data for key in required):
        raise MalformedSyncEvent("missing required clip field")

    clip_id = data["id"]
    content = data["content"]
    content_hash = data["content_hash"]
    content_type = data["content_type"]
    if not isinstance(clip_id, str) or _CLIP_ID_RE.fullmatch(clip_id) is None:
        raise MalformedSyncEvent("invalid clip id")
    if not isinstance(content, str):
        raise MalformedSyncEvent("invalid clip content type")
    if normalize.normalize(content) != content or normalize.reject_reason(content, max_bytes):
        raise MalformedSyncEvent("invalid normalized clip content")
    if not _valid_content_hash(content_hash) or normalize.content_hash(content) != content_hash:
        raise MalformedSyncEvent("invalid clip content hash")
    if content_type not in CONTENT_TYPES:
        raise MalformedSyncEvent("invalid clip content type")
    if not _valid_utc_timestamp(data["created_at"]) or not _valid_utc_timestamp(data["last_seen_at"]):
        raise MalformedSyncEvent("invalid clip timestamp")
    if data["last_seen_at"] < data["created_at"]:
        raise MalformedSyncEvent("invalid clip timestamp order")

    for field in ("is_secret", "pinned", "favorite", "deleted"):
        if field in data and not isinstance(data[field], bool):
            raise MalformedSyncEvent(f"invalid {field} type")
    times_seen = data.get("times_seen", 1)
    if (
        not isinstance(times_seen, int)
        or isinstance(times_seen, bool)
        or times_seen < 1
        or times_seen > 2_147_483_647
    ):
        raise MalformedSyncEvent("invalid times_seen")
    source_device = data.get("source_device", "peer")
    source_app = data.get("source_app")
    if (
        not isinstance(source_device, str)
        or not source_device
        or len(source_device) > 256
        or _has_control_chars(source_device)
    ):
        raise MalformedSyncEvent("invalid source_device")
    if source_app is not None and (
        not isinstance(source_app, str)
        or len(source_app) > 1024
        or _has_control_chars(source_app)
    ):
        raise MalformedSyncEvent("invalid source_app")

    verdict = secret_guard.scan(content)  # gate A on arrival
    is_secret = verdict.is_secret or data.get("is_secret", False)
    return Clip(
        id=clip_id,
        content=content,
        content_hash=content_hash,
        content_type=content_type,
        is_secret=is_secret,
        secret_level=verdict.level if verdict.is_secret else ("suspect" if is_secret else None),
        secret_reasons=verdict.reasons if verdict.is_secret else (["REMOTE-SECRET"] if is_secret else []),
        source_device=source_device,
        source_app=source_app,
        created_at=data["created_at"],
        last_seen_at=data["last_seen_at"],
        times_seen=times_seen,
        pinned=data.get("pinned", False),
        favorite=data.get("favorite", False),
        deleted=data.get("deleted", False),
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
    safe_kind = kind if isinstance(kind, str) and kind in _KNOWN_EVENT_KINDS else "unknown"
    data = ev.get("data")
    if not isinstance(data, dict):
        log.error("malformed sync event category=%s data_not_object", safe_kind)
        return
    try:
        if kind == "clip_new":
            _apply_clip_new(conn, data, service)
        elif kind == "clip_meta":
            _validate_clip_meta(data)
            _apply_clip_meta(conn, data)
        elif kind == "memory_upsert":
            _validate_memory_upsert(data)
            _apply_memory_upsert(conn, data)
        elif kind == "memory_delete":
            _validate_memory_delete(data)
            _apply_memory_delete(conn, data)
        else:
            log.error("unknown sync event kind")
    except KeyError:
        log.error("malformed sync event category=%s missing_required_field", safe_kind)
    except MalformedSyncEvent as exc:
        # Do not log payload values.  A seq-valid malformed event is acked as a
        # no-op so it cannot wedge all later events from the peer.
        log.error("malformed sync event category=%s reason=%s", safe_kind, exc)
    except sqlite3.IntegrityError as exc:
        # A peer can send a seq-valid but semantically invalid event, for
        # example a clip_new with a duplicate id and different content hash.
        # Treat it like the other malformed seq-valid events above: ack as a
        # no-op so one permanently bad item cannot wedge sync forever, but do
        # not hide transient database failures such as locks or IO errors.
        log.error(
            "malformed sync event category=%s integrity error %s",
            safe_kind,
            exc.__class__.__name__,
        )
    except (TypeError, ValueError, AttributeError, OverflowError, sqlite3.ProgrammingError) as exc:
        # Final fail-closed boundary for a shape the explicit validators missed.
        # Never include exception messages: adapters may embed peer-controlled
        # values in them.
        log.error(
            "malformed sync event category=%s error=%s",
            safe_kind,
            exc.__class__.__name__,
        )


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
    clip = _validated_remote_clip(data, max_bytes=service.config.max_clip_bytes)
    clips = ClipsRepo(conn)
    backup_queue = BackupQueueRepo(conn)
    obsidian_queue = ObsidianQueueRepo(conn)
    now = _utc_now()

    # A new remote clip and its durable downstream intents are atomic.  Replay
    # also repairs missing queue rows left by older versions without echoing the
    # event into our local sync outbox.
    with unit_of_work(conn):
        existing = clips.get_by_hash(clip.content_hash)
        if existing is None:
            clips.insert(clip, commit=False)  # secrets stay out of FTS
            target = clip
        else:
            target = existing
        if not target.is_secret:
            backup_queue.enqueue(target.id, now, commit=False)
            if not target.deleted:
                obsidian_queue.enqueue(target.id, now, commit=False)

    if target.is_secret:
        log.warning("remote clip quarantined id=%s reasons=%s",
                    target.id, ",".join(target.secret_reasons))
        return
    if not target.deleted and not target.obsidian_path:
        # External IO occurs after the atomic DB commit.  The queue remains
        # durable if the process stops before or during this best-effort attempt.
        service.write_obsidian_or_queue(target)


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
    fetch_limit = min(limit, SYNC_PULL_FETCH_LIMIT)
    raw_events = OutboxRepo(conn).list_since(since_seq, fetch_limit)
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
        "has_more": stopped_by_budget or len(raw_events) == fetch_limit,
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
        for event in outbox.list_since(int(row["my_acked_seq"]), SYNC_PULL_FETCH_LIMIT):
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
