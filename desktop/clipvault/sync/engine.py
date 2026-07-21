"""Sync engine (SYNC-2): emit local events to the outbox, and apply events
pushed by a peer. The remote-apply path deliberately does NOT go through
pipeline.ingest, so applying a peer's clip never echoes back into our outbox.
Gate B (secrets never leave) and gate A (re-scan on arrival) both enforced here.
"""

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone

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
    ("clip_new", "clip_meta", "memory_upsert", "memory_delete", "privacy_noop")
)
_PRIVACY_NOOP_TIMESTAMP = "1970-01-01T00:00:00Z"
_MEMORY_SOURCES = frozenset(("manual", "derived", "obsidian_import", "github_import"))
_MAX_MEMORY_LABEL_BYTES = 4 * 1024
_CLIP_NEW_PAYLOAD_FIELDS = frozenset(
    (
        "id", "content", "content_hash", "content_type", "is_secret",
        "secret_level", "secret_reasons", "source_device", "source_app",
        "created_at", "last_seen_at", "times_seen", "pinned", "favorite",
        "deleted",
    )
)
_CLIP_META_PAYLOAD_FIELDS = frozenset(("content_hash", "patch", "ts"))
_MEMORY_UPSERT_PAYLOAD_FIELDS = frozenset(
    ("kind", "text", "label", "pinned", "use_count", "source")
)
_MEMORY_DELETE_PAYLOAD_FIELDS = frozenset(("kind", "text", "ts"))
_MAX_META_TIMESTAMP = "9999-12-31T23:59:59Z"
_MAX_META_LOCAL_FENCE = f"{_MAX_META_TIMESTAMP}#local"
_MAX_MEMORY_TIMESTAMP = _MAX_META_TIMESTAMP
_MAX_MEMORY_LOCAL_FENCE = f"{_MAX_MEMORY_TIMESTAMP}#local"

SYNC_PULL_EVENT_LIMIT = 100
# SQLite rows are decoded before wire budgeting. Fetch a small internal page so
# several near-limit escaped payloads cannot be materialised at once; repeated
# pull pages still preserve the public <=100-event protocol contract.
SYNC_PULL_FETCH_LIMIT = 8
# Status diagnostics may skip legacy quarantined events, but must remain
# bounded even if an outbox contains a long run of rows that cannot be sent.
SYNC_PULL_STATUS_SCAN_LIMIT = 64
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


def validate_memory_upsert_payload(data: dict) -> None:
    """Validate a locally generated Memory payload without touching SQLite."""

    try:
        _validate_memory_upsert(data)
    except (MalformedSyncEvent, UnicodeError) as exc:
        raise ValueError("invalid memory upsert payload") from exc


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


def clip_requires_local_quarantine(clip: Clip) -> bool:
    """Return whether a clip must remain behind Secret Guard Gate B.

    Persisted ``is_secret`` is not sufficient for rows created by an older
    detector.  Re-scan those rows under the current rules, except when the
    Owner explicitly released the clip from quarantine.
    """

    return clip.is_secret or (
        not clip.released and secret_guard.scan(clip.content).is_secret
    )


# --- local emission (called by ingest / patch) ---

def emit_clip_new(conn, clip: Clip, when: str, *, commit: bool = True) -> int | None:
    """Publish a locally-created public clip. Gate B: secrets never emitted."""
    if clip_requires_local_quarantine(clip):
        return None
    return OutboxRepo(conn).append("clip_new", clip_to_data(clip), when, commit=commit)


def emit_clip_meta(
    conn,
    content_hash: str,
    patch: dict,
    ts: str,
    when: str,
    *,
    commit: bool = True,
) -> int | None:
    """Publish metadata for an existing public clip.

    Gate B is re-checked here so a caller cannot leak the hash or state of a
    quarantined clip.  The default path keeps timestamp bookkeeping and the
    outbox append atomic; callers that already own a unit of work pass
    ``commit=False``.
    """

    try:
        _validate_clip_meta(
            {"content_hash": content_hash, "patch": patch, "ts": ts}
        )
    except MalformedSyncEvent as exc:
        raise ValueError(str(exc)) from exc
    if not _valid_utc_timestamp(when):
        raise ValueError("invalid clip metadata event timestamp")

    if commit:
        with unit_of_work(conn):
            return _emit_clip_meta_uncommitted(
                conn, content_hash, dict(patch), ts, when
            )
    return _emit_clip_meta_uncommitted(
        conn, content_hash, dict(patch), ts, when
    )


def _emit_clip_meta_uncommitted(
    conn,
    content_hash: str,
    patch: dict,
    ts: str,
    when: str,
) -> int | None:
    """Apply the validated local metadata event inside the caller's UoW."""

    clip = ClipsRepo(conn).get_by_hash(content_hash)
    if clip is None:
        log.error("clip metadata sync blocked for missing local clip")
        return None
    if clip_requires_local_quarantine(clip):
        log.error("secret clip metadata blocked at sync outbox boundary")
        return None
    fields = tuple(
        field for field in ("pinned", "favorite", "deleted") if field in patch
    )
    effective_ts, stored_ts = _next_local_meta_ts(
        conn, content_hash, fields, ts
    )
    for field in fields:
        _set_meta_ts(conn, content_hash, field, stored_ts, commit=False)
    return OutboxRepo(conn).append(
        "clip_meta",
        {"content_hash": content_hash, "patch": patch, "ts": effective_ts},
        when,
        commit=False,
    )


def _memory_data_is_secret(data: dict) -> bool:
    from clipvault.store.memory_repo import memory_contains_secret

    text = data.get("text")
    if not isinstance(text, str):
        return False
    return memory_contains_secret(text, data.get("label"))


def _memory_key_is_secret(conn, kind: str, text: str) -> bool:
    """Re-scan a Memory key plus any persisted label at every exit/apply gate."""

    from clipvault.store.memory_repo import MemoryRepo, memory_contains_secret

    existing = MemoryRepo(conn).by_kind_text(kind, text)
    label = existing.label if existing is not None else None
    return memory_contains_secret(text, label)


def emit_memory_upsert(
    conn,
    item,
    when: str,
    *,
    commit: bool = True,
) -> int | None:
    """Publish public Personal Memory to peers.

    This is an independent SG-1 exit gate: callers cannot bypass it by handing
    us a legacy or otherwise unvalidated MemoryItem.
    """
    data = {
        "kind": item.kind, "text": item.text, "label": item.label,
        "pinned": item.pinned, "use_count": item.use_count, "source": item.source,
    }
    validate_memory_upsert_payload(data)
    if not _valid_utc_timestamp(when):
        raise ValueError("invalid memory upsert event timestamp")
    if _memory_data_is_secret(data):
        log.error("secret memory blocked at sync outbox boundary")
        return None
    if commit:
        with unit_of_work(conn):
            return emit_memory_upsert(conn, item, when, commit=False)
    _, stored_ts = _next_local_mem_ts(conn, item.kind, item.text, when)
    _set_mem_ts(conn, item.kind, item.text, stored_ts, commit=False)
    return OutboxRepo(conn).append(
        "memory_upsert", data, when, commit=False
    )


def emit_memory_delete(
    conn,
    kind: str,
    text: str,
    ts: str,
    when: str,
    *,
    commit: bool = True,
) -> int | None:
    try:
        _validate_memory_delete({"kind": kind, "text": text, "ts": ts})
    except MalformedSyncEvent as exc:
        raise ValueError(str(exc)) from exc
    if not _valid_utc_timestamp(when):
        raise ValueError("invalid memory delete event timestamp")
    if _memory_key_is_secret(conn, kind, text):
        log.error("secret memory delete blocked at sync outbox boundary")
        return None
    if commit:
        with unit_of_work(conn):
            return emit_memory_delete(
                conn, kind, text, ts, when, commit=False
            )
    wire_ts, stored_ts = _next_local_mem_ts(conn, kind, text, ts)
    _set_mem_ts(conn, kind, text, stored_ts, commit=False)
    return OutboxRepo(conn).append(
        "memory_delete",
        {"kind": kind, "text": text, "ts": wire_ts},
        when,
        commit=False,
    )


# --- meta LWW bookkeeping ---

def _get_meta_ts(conn, content_hash: str, field: str) -> str:
    row = conn.execute(
        "SELECT ts FROM clip_meta_ts WHERE content_hash = ? AND field = ?",
        (content_hash, field),
    ).fetchone()
    return row[0] if row else ""


def _next_local_meta_ts(
    conn,
    content_hash: str,
    fields: tuple[str, ...],
    candidate_ts: str,
) -> tuple[str, str]:
    """Return wire and persisted logical timestamps for a local patch.

    Wall-clock timestamps can repeat within one second or move backwards.  A
    local mutation must nevertheless sort after every previously recorded
    value for the fields it changes, otherwise a peer's per-field LWW apply can
    discard the newer user action. One timestamp is shared by a multi-field
    patch. Wire and persisted values are identical except at the fixed-format
    ceiling, where the persisted local fence remains Desktop-internal.
    """

    max_ts = max(
        (_get_meta_ts(conn, content_hash, field) for field in fields),
        default="",
    )
    if not max_ts or candidate_ts > max_ts:
        return candidate_ts, candidate_ts
    if max_ts.startswith(_MAX_META_TIMESTAMP):
        # The fixed-width v1 wire format has no representable successor.  Wire
        # time saturates, while the local-only suffix is a persistent Owner
        # fence: a replayed plain maximum timestamp sorts before it and cannot
        # undo a later local action. Android consumes our outbox in seq order
        # and never reads this Desktop-internal clock value.
        return _MAX_META_TIMESTAMP, _MAX_META_LOCAL_FENCE
    parsed = datetime.strptime(max_ts, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    advanced = (parsed + timedelta(seconds=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return advanced, advanced


def _set_meta_ts(
    conn,
    content_hash: str,
    field: str,
    ts: str,
    *,
    commit: bool = True,
) -> None:
    conn.execute(
        "INSERT INTO clip_meta_ts(content_hash, field, ts) VALUES (?,?,?) "
        "ON CONFLICT(content_hash, field) DO UPDATE SET ts=excluded.ts "
        "WHERE excluded.ts >= clip_meta_ts.ts",
        (content_hash, field, ts),
    )
    if commit:
        conn.commit()


def _get_mem_ts(conn, kind: str, text: str) -> str:
    row = conn.execute(
        "SELECT ts FROM memory_meta_ts WHERE kind = ? AND text = ?", (kind, text)
    ).fetchone()
    return row[0] if row else ""


def _next_local_mem_ts(
    conn,
    kind: str,
    text: str,
    candidate_ts: str,
) -> tuple[str, str]:
    """Return wire and persisted clocks for a local Memory mutation.

    Memory delete uses last-writer-wins timestamps, so every explicit local
    create, update, or delete must sort after the previously recorded clock
    even when the wall clock repeats or moves backwards. At the fixed-format
    ceiling, a Desktop-only suffix fences replayed plain-maximum deletes while
    the wire timestamp remains valid for ordered Android consumers.
    """

    current = _get_mem_ts(conn, kind, text)
    if not current or candidate_ts > current:
        return candidate_ts, candidate_ts
    if current.startswith(_MAX_MEMORY_TIMESTAMP):
        return _MAX_MEMORY_TIMESTAMP, _MAX_MEMORY_LOCAL_FENCE
    parsed = datetime.strptime(current, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    advanced = (parsed + timedelta(seconds=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return advanced, advanced


def _set_mem_ts(
    conn,
    kind: str,
    text: str,
    ts: str,
    *,
    commit: bool = True,
) -> None:
    conn.execute(
        "INSERT INTO memory_meta_ts(kind, text, ts) VALUES (?,?,?) "
        "ON CONFLICT(kind, text) DO UPDATE SET ts=excluded.ts "
        "WHERE excluded.ts >= memory_meta_ts.ts",
        (kind, text, ts),
    )
    if commit:
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
        elif kind == "privacy_noop":
            if data or ev.get("ts") != _PRIVACY_NOOP_TIMESTAMP:
                raise MalformedSyncEvent("invalid privacy noop")
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
        # Foreground sync only wakes the dedicated worker. The queue intent is
        # already durable, so a slow Vault never blocks the single API thread.
        service.dispatch_obsidian_work(target)


def _apply_memory_upsert(conn, data: dict) -> None:
    from clipvault.store.memory_repo import (
        MemoryRepo,
        SecretMemoryError,
        memory_contains_secret,
    )

    try:
        with unit_of_work(conn):
            repo = MemoryRepo(conn)
            existing = repo.by_kind_text(data["kind"], data["text"])
            if existing is not None and (
                existing.deleted
                or memory_contains_secret(existing.text, existing.label)
            ):
                # A legacy peer event may update only a live, public item. It
                # cannot sanitize, mutate, or revive a Desktop tombstone or a
                # row quarantined by the current Secret Guard.
                return
            if existing is None and _get_mem_ts(
                conn, data["kind"], data["text"]
            ):
                # A clock without a fact row is still a durable tombstone (for
                # example, a delete that arrived before a gapped upsert).
                return
            repo.upsert(
                data["kind"],
                data["text"],
                label=data.get("label"),
                source=data.get("source", "manual"),
                pinned=data.get("pinned", False),
                use_count=data.get("use_count", 0),
                revive_deleted=False,
                commit=False,
            )
    except SecretMemoryError:
        # Treat a secret-shaped remote event as an acknowledged quarantine
        # no-op. Retrying it forever cannot make it safe and would wedge sync.
        log.error("remote secret memory rejected")


def _apply_memory_delete(conn, data: dict) -> None:
    from clipvault.store.memory_repo import MemoryRepo
    kind, text, ts = data["kind"], data["text"], data.get("ts", "")
    with unit_of_work(conn):
        if _memory_key_is_secret(conn, kind, text):
            log.error("remote secret memory delete rejected")
            return
        # Read only after the writer lock. A stale delete must not remove a
        # locally newer item, including a maximum-timestamp local fence.
        if ts < _get_mem_ts(conn, kind, text):
            return
        repo = MemoryRepo(conn)
        item = repo.by_kind_text(kind, text)
        if item is not None:
            repo.soft_delete(item.id, commit=False)
        _set_mem_ts(conn, kind, text, ts, commit=False)


def _apply_clip_meta(conn, data: dict) -> None:
    content_hash = data["content_hash"]
    ts = data["ts"]
    patch = data["patch"]
    with unit_of_work(conn):
        # Read only after BEGIN IMMEDIATE (or the caller's savepoint) so the
        # state comparison, LWW clocks, FTS effects, and backup intent share one
        # writer snapshot and either all commit or all roll back.
        clips = ClipsRepo(conn)
        row = clips.get_by_hash(content_hash)
        if row is None:
            return
        if clip_requires_local_quarantine(row):
            # A peer must never have learned this hash through a conforming
            # implementation.  Keep the quarantined row local and avoid
            # recording metadata timestamps or downstream intents.
            log.error("remote clip metadata blocked for quarantined local clip")
            return

        # Per-field LWW (v1.8): each field's newest ts wins independently, so a
        # newer change to one field is never masked by an older change to
        # another. On an exact ts tie a delete wins (SYNC-2 delete-wins).
        deleted_changed = False
        for field in ("pinned", "favorite", "deleted"):
            if field not in patch:
                continue
            local_ts = _get_meta_ts(conn, content_hash, field)
            value = bool(patch[field])
            is_delete = field == "deleted" and value
            if ts < local_ts or (ts == local_ts and not is_delete):
                continue  # stale for this field
            clips.set_flag(row.id, field, value, commit=False)
            _set_meta_ts(conn, content_hash, field, ts, commit=False)
            if field == "deleted" and row.deleted != value:
                deleted_changed = True

        # Re-back-up only on a real deletion-state transition. A newer replay
        # of the same value still advances its LWW clock but must not wake a
        # completed backup row again.
        if deleted_changed and not row.is_secret:
            BackupQueueRepo(conn).reenqueue(row.id, ts, commit=False)


# --- pull side ---

def _event_wire_size(event: dict) -> int:
    return len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _outbox_clip_event_is_blocked(conn, event: dict) -> bool:
    """Fail closed for legacy or malformed clip events at the Gate B boundary.

    A locally released row is the one exception to content re-scanning: release
    is an explicit Owner action that intentionally re-enters the public sync
    path even when the original text still resembles a secret.
    """

    payload = event.get("payload")
    if not isinstance(payload, dict):
        return True
    if event.get("kind") == "clip_new":
        if set(payload) != _CLIP_NEW_PAYLOAD_FIELDS:
            return True
        if payload.get("is_secret") is not False:
            return True
        if payload.get("secret_level") is not None:
            return True
        if payload.get("secret_reasons") != []:
            return True
        content = payload.get("content")
        if not isinstance(content, str):
            return True
        try:
            # Outgoing size is enforced separately by build_pull.  Use the
            # actual payload size here so a structurally valid legacy event is
            # checked for identity and secrecy before byte-budget handling.
            validation_max_bytes = max(
                normalize.DEFAULT_MAX_CLIP_BYTES,
                len(content.encode("utf-8")),
            )
            candidate = _validated_remote_clip(
                payload, max_bytes=validation_max_bytes
            )
        except (MalformedSyncEvent, UnicodeError):
            return True

        local = ClipsRepo(conn).get_by_hash(candidate.content_hash)
        if local is not None:
            if local.id != candidate.id or local.content != candidate.content:
                return True
            if local.is_secret:
                return True
            if local.released:
                # Release is an explicit Owner decision, but only the new
                # exact public snapshot receives that exception.
                return False
        return candidate.is_secret
    if event.get("kind") == "clip_meta":
        if set(payload) != _CLIP_META_PAYLOAD_FIELDS:
            return True
        try:
            _validate_clip_meta(payload)
        except MalformedSyncEvent:
            return True
        content_hash = payload["content_hash"]
        clip = ClipsRepo(conn).get_by_hash(content_hash)
        return clip is None or clip_requires_local_quarantine(clip)
    return True


def _outbox_event_is_blocked(conn, event: dict) -> bool:
    try:
        _event_wire_size(event)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return True
    if not _valid_utc_timestamp(event.get("created_at")):
        return True
    kind = event.get("kind")
    if kind in ("clip_new", "clip_meta"):
        return _outbox_clip_event_is_blocked(conn, event)
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return True
    try:
        if kind == "memory_upsert":
            if not set(payload).issubset(_MEMORY_UPSERT_PAYLOAD_FIELDS):
                return True
            _validate_memory_upsert(payload)
            return _memory_data_is_secret(payload) or _memory_key_is_secret(
                conn, payload["kind"], payload["text"]
            )
        if kind == "memory_delete":
            if set(payload) != _MEMORY_DELETE_PAYLOAD_FIELDS:
                return True
            _validate_memory_delete(payload)
            return _memory_key_is_secret(
                conn, payload["kind"], payload["text"]
            )
    except MalformedSyncEvent:
        return True
    # A downgraded or corrupted writer must not turn an unknown payload shape
    # into an implicit extension of the sync protocol.
    return True


def _first_sendable_outbox_event(
    conn,
    outbox: OutboxRepo,
    since_seq: int,
    *,
    scan_limit: int = SYNC_PULL_STATUS_SCAN_LIMIT,
) -> dict | None:
    """Find the next sendable event without an unbounded status scan."""

    cursor = since_seq
    remaining = scan_limit
    while remaining > 0:
        page_limit = min(SYNC_PULL_FETCH_LIMIT, remaining)
        page = outbox.list_since(cursor, page_limit)
        if not page:
            return None
        for event in page:
            cursor = int(event["seq"])
            remaining -= 1
            if _outbox_event_is_blocked(conn, event):
                continue
            return event
        if len(page) < page_limit:
            return None
    return None


def build_pull(conn, since_seq: int, limit: int = SYNC_PULL_EVENT_LIMIT,
               max_bytes: int = SYNC_PULL_RESPONSE_BYTES) -> dict:
    fetch_limit = min(limit, SYNC_PULL_FETCH_LIMIT)
    raw_events = OutboxRepo(conn).list_since(since_seq, fetch_limit)
    events = []
    next_seq = since_seq
    used_bytes = 0
    stopped_by_budget = False
    for event in raw_events:
        if _outbox_event_is_blocked(conn, event):
            # Legacy rows may predate the current Gate B checks. Advance over
            # the quarantined event without logging payload fields or hashes.
            kind = event.get("kind")
            subject = (
                "clip" if kind in ("clip_new", "clip_meta")
                else "memory" if kind in ("memory_upsert", "memory_delete")
                else "unknown"
            )
            log.error(
                "legacy secret or malformed %s event blocked at sync pull seq=%s",
                subject,
                event["seq"],
            )
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
        event = _first_sendable_outbox_event(
            conn,
            outbox,
            int(row["my_acked_seq"]),
        )
        if event is not None:
            event_bytes = _event_wire_size(event)
            if event_bytes > max_bytes:
                blocked.append({
                    "seq": event["seq"],
                    "event_bytes": event_bytes,
                })

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
