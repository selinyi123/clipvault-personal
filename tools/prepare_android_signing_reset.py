"""Prepare a one-shot Desktop snapshot for a freshly reinstalled Android app.

The Android signing reset cannot preserve the old app-private Room database.
After the old Android peer has been revoked, this Owner-only tool re-emits the
Desktop's *current* public/live snapshot through the existing SYNC-2 outbox.
Only after the snapshot commits should the replacement app pair and restore it
with its normal paginated pull loop.

Safety properties:

* dry-run is the default and opens the database read-only;
* ``--apply`` owns one SQLite transaction that replaces retained Desktop
  outbound history with the current snapshot (all rows or none);
* persisted secrets, current Secret Guard hits, deleted rows, and malformed or
  otherwise unsendable rows are never emitted;
* output contains aggregate counts only, never content, ids, hashes, labels,
  paths, device identifiers, or payloads;
* a final content-free sequence marker makes an empty snapshot ACKable;
* a caller-supplied UTC ``--run-id`` is stored as the existing outbox
  ``created_at`` value and in a content-free ignored local state file.  A
  repeated run is a proven no-op only while the matching outbox rows are still
  retained and exactly match the current snapshot.  The state file alone is
  never treated as proof after normal ACK pruning.

The Desktop service must remain stopped before ``--apply``. On Windows the CLI
also takes the normal ClipVault instance mutex and fails if the service is
still running. After apply, keep Desktop captures and outbound mutations frozen
until ``--verify-delivery`` succeeds; that verifier requires the durable outbox
high-water to remain exactly equal to the reseed end sequence.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "desktop"
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from clipvault.core import origin_metadata, secret_guard  # noqa: E402
from clipvault.core.models import Clip, MemoryItem  # noqa: E402
from clipvault.store import db  # noqa: E402
from clipvault.store.clips_repo import ClipsRepo  # noqa: E402
from clipvault.store.memory_repo import (  # noqa: E402
    MemoryRepo,
    memory_contains_secret,
)
from clipvault.store.outbox_repo import OutboxRepo  # noqa: E402
from clipvault.store.unit_of_work import unit_of_work  # noqa: E402
from clipvault.sync import engine as sync_engine  # noqa: E402


_CLIP_META_FIELDS = ("pinned", "favorite", "deleted")
_RESEED_MARKER_KIND = "privacy_noop"
_RESEED_MARKER_PAYLOAD: dict[str, object] = {}
DEFAULT_MAX_EVENTS = 100_000
DEFAULT_MAX_PAYLOAD_BYTES = 512 * 1024 * 1024
STATE_ROOT = REPO_ROOT / ".field-test-artifacts" / "android-signing-reset"
DEFAULT_STATE_FILE = STATE_ROOT / "prepare-state.json"
_MAX_STATE_FILE_BYTES = 64 * 1024
_RUN_ID_MAX_AGE = timedelta(hours=24)
_RUN_ID_MAX_FUTURE_SKEW = timedelta(minutes=5)


class SigningResetError(RuntimeError):
    """A content-safe failure that may be shown to the Owner."""


@dataclass
class SnapshotCounts:
    total: int = 0
    eligible: int = 0
    skipped_deleted: int = 0
    skipped_persisted_secret: int = 0
    skipped_current_secret: int = 0
    skipped_unsafe_origin: int = 0
    skipped_invalid: int = 0

    def safe_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "eligible": self.eligible,
            "skipped_deleted": self.skipped_deleted,
            "skipped_persisted_secret": self.skipped_persisted_secret,
            "skipped_current_secret": self.skipped_current_secret,
            "skipped_unsafe_origin": self.skipped_unsafe_origin,
            "skipped_invalid": self.skipped_invalid,
        }


@dataclass
class SnapshotPlan:
    clips: SnapshotCounts
    memory: SnapshotCounts
    fingerprints: Counter[str]
    payload_bytes: int

    @property
    def events(self) -> int:
        # clip_new + a full clip_meta snapshot for each clip, and one
        # memory_upsert for each live Memory row.  The final content-free marker
        # makes even an empty snapshot observable and ACKable by a fresh peer.
        return self.clips.eligible * 2 + self.memory.eligible + 1


@dataclass(frozen=True)
class PreparationResult:
    mode: str
    run_id: str | None
    schema_version: int
    paired_devices: int
    outbox_before: int
    outbox_after: int
    reseed_start_seq: int | None
    reseed_end_seq: int | None
    events_planned: int
    payload_bytes_planned: int
    max_events: int
    max_payload_bytes: int
    events_appended: int
    idempotent_noop: bool
    delivery_verified: bool
    clips: SnapshotCounts
    memory: SnapshotCounts

    def safe_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "paired_devices": self.paired_devices,
            "outbox_before": self.outbox_before,
            "outbox_after": self.outbox_after,
            "reseed_start_seq": self.reseed_start_seq,
            "reseed_end_seq": self.reseed_end_seq,
            "events_planned": self.events_planned,
            "payload_bytes_planned": self.payload_bytes_planned,
            "max_events": self.max_events,
            "max_payload_bytes": self.max_payload_bytes,
            "events_appended": self.events_appended,
            "idempotent_noop": self.idempotent_noop,
            "delivery_verified": self.delivery_verified,
            "clips": self.clips.safe_dict(),
            "memory": self.memory.safe_dict(),
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_run_id(value: str) -> tuple[str, datetime]:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError) as exc:
        raise SigningResetError(
            "run id must be a UTC timestamp formatted YYYY-MM-DDTHH:MM:SSZ"
        ) from exc
    return value, parsed


def _validate_run_id(value: str) -> str:
    value, parsed = _parse_run_id(value)
    now = _utc_now()
    if parsed > now + _RUN_ID_MAX_FUTURE_SKEW:
        raise SigningResetError("run id is too far in the future")
    if parsed < now - _RUN_ID_MAX_AGE:
        raise SigningResetError("run id is older than the allowed preparation window")
    return value


def _event_fingerprint(kind: str, payload: dict) -> str:
    """Return a content-obscuring semantic fingerprint for idempotency checks.

    ``emit_clip_meta`` may advance a Desktop-local logical timestamp on its
    first run.  The timestamp is irrelevant to the fresh Android ordered
    consumer, so it is deliberately excluded from the marker comparison while
    the current hash/flags remain covered.
    """

    canonical_payload = dict(payload)
    if kind == "clip_meta":
        canonical_payload.pop("ts", None)
    encoded = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(kind.encode("ascii") + b"\0" + encoded).hexdigest()


def _payload_storage_bytes(payload: dict) -> int:
    """Match OutboxRepo's JSON representation for a write-budget estimate."""

    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _clip_meta_payload(clip: Clip, run_id: str) -> dict:
    patch = {field: bool(getattr(clip, field)) for field in _CLIP_META_FIELDS}
    patch["deleted"] = False
    return {
        "content_hash": clip.content_hash,
        "patch": patch,
        "ts": run_id,
    }


def _memory_payload(item: MemoryItem) -> dict:
    return {
        "kind": item.kind,
        "text": item.text,
        "label": item.label,
        "pinned": item.pinned,
        "use_count": item.use_count,
        "source": item.source,
    }


def _eligible_clips(
    conn: sqlite3.Connection,
    counts: SnapshotCounts,
) -> Iterator[Clip]:
    repo = ClipsRepo(conn)
    rows = conn.execute("SELECT id FROM clips ORDER BY id").fetchall()
    for row in rows:
        counts.total += 1
        clip = repo.get(row[0])
        if clip is None:
            counts.skipped_invalid += 1
            continue
        if clip.deleted:
            counts.skipped_deleted += 1
            continue
        if clip.is_secret:
            counts.skipped_persisted_secret += 1
            continue
        # The signing-reset snapshot is intentionally stricter than the normal
        # explicit Owner-release exception: a *current* SG hit is never copied
        # into the replacement app.
        if secret_guard.scan(clip.content).is_secret:
            counts.skipped_current_secret += 1
            continue
        if not origin_metadata.origin_metadata_is_safe(
            clip.source_device, clip.source_app
        ):
            counts.skipped_unsafe_origin += 1
            continue
        payload = sync_engine.clip_to_data(clip)
        if (
            payload.get("is_secret") is not False
            or payload.get("secret_level") is not None
            or payload.get("secret_reasons") != []
        ):
            counts.skipped_invalid += 1
            continue
        try:
            # Reuse the wire contract's exact normalization/hash/size/type and
            # timestamp checks without persisting or logging the payload.
            validated = sync_engine._validated_remote_clip(  # noqa: SLF001
                payload,
                max_bytes=sync_engine.normalize.DEFAULT_MAX_CLIP_BYTES,
            )
        except (sync_engine.MalformedSyncEvent, UnicodeError, ValueError):
            counts.skipped_invalid += 1
            continue
        if validated.is_secret:
            counts.skipped_current_secret += 1
            continue
        counts.eligible += 1
        yield clip


def _eligible_memory(
    conn: sqlite3.Connection,
    counts: SnapshotCounts,
) -> Iterator[MemoryItem]:
    repo = MemoryRepo(conn)
    rows = conn.execute(
        "SELECT id FROM memory_items ORDER BY kind, text, id"
    ).fetchall()
    for row in rows:
        counts.total += 1
        item = repo.get(row[0])
        if item is None:
            counts.skipped_invalid += 1
            continue
        if item.deleted:
            counts.skipped_deleted += 1
            continue
        if memory_contains_secret(item.text, item.label):
            counts.skipped_current_secret += 1
            continue
        payload = _memory_payload(item)
        try:
            sync_engine.validate_memory_upsert_payload(payload)
        except (ValueError, UnicodeError):
            counts.skipped_invalid += 1
            continue
        counts.eligible += 1
        yield item


def build_snapshot_plan(
    conn: sqlite3.Connection,
    *,
    run_id: str,
) -> SnapshotPlan:
    """Scan one SQLite snapshot and return content-safe counts/fingerprints."""

    _validate_run_id(run_id)
    clip_counts = SnapshotCounts()
    memory_counts = SnapshotCounts()
    fingerprints: Counter[str] = Counter()
    payload_bytes = 0

    for clip in _eligible_clips(conn, clip_counts):
        clip_payload = sync_engine.clip_to_data(clip)
        meta_payload = _clip_meta_payload(clip, run_id)
        fingerprints[_event_fingerprint("clip_new", clip_payload)] += 1
        fingerprints[_event_fingerprint(
            "clip_meta", meta_payload
        )] += 1
        payload_bytes += _payload_storage_bytes(clip_payload)
        payload_bytes += _payload_storage_bytes(meta_payload)
    for item in _eligible_memory(conn, memory_counts):
        memory_payload = _memory_payload(item)
        fingerprints[_event_fingerprint("memory_upsert", memory_payload)] += 1
        payload_bytes += _payload_storage_bytes(memory_payload)
    fingerprints[
        _event_fingerprint(_RESEED_MARKER_KIND, _RESEED_MARKER_PAYLOAD)
    ] += 1
    payload_bytes += _payload_storage_bytes(_RESEED_MARKER_PAYLOAD)
    return SnapshotPlan(
        clip_counts,
        memory_counts,
        fingerprints,
        payload_bytes,
    )


def _validate_budget(max_events: int, max_payload_bytes: int) -> None:
    if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 1:
        raise SigningResetError("max events must be a positive integer")
    if (
        isinstance(max_payload_bytes, bool)
        or not isinstance(max_payload_bytes, int)
        or max_payload_bytes < 1
    ):
        raise SigningResetError("max payload bytes must be a positive integer")


def _enforce_budget(
    plan: SnapshotPlan,
    *,
    max_events: int,
    max_payload_bytes: int,
) -> None:
    _validate_budget(max_events, max_payload_bytes)
    if plan.events > max_events:
        raise SigningResetError("snapshot exceeds the explicit event budget")
    if plan.payload_bytes > max_payload_bytes:
        raise SigningResetError("snapshot exceeds the explicit payload-byte budget")


def _existing_run_fingerprints(
    conn: sqlite3.Connection,
    run_id: str,
) -> tuple[Counter[str], int, int | None, int | None]:
    fingerprints: Counter[str] = Counter()
    malformed = 0
    rows = conn.execute(
        "SELECT seq, CAST(kind AS BLOB), CAST(payload AS BLOB) "
        "FROM sync_outbox WHERE created_at = ? ORDER BY seq",
        (run_id,),
    ).fetchall()
    for _seq, kind_raw, payload_raw in rows:
        try:
            if not isinstance(kind_raw, bytes) or not isinstance(payload_raw, bytes):
                raise ValueError
            kind = kind_raw.decode("utf-8")
            payload = json.loads(payload_raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
            fingerprints[_event_fingerprint(kind, payload)] += 1
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            malformed += 1
    start_seq = int(rows[0][0]) if rows else None
    end_seq = int(rows[-1][0]) if rows else None
    return fingerprints, malformed, start_seq, end_seq


def _emit_snapshot(
    conn: sqlite3.Connection,
    *,
    run_id: str,
) -> int:
    clip_counts = SnapshotCounts()
    memory_counts = SnapshotCounts()
    appended = 0
    for clip in _eligible_clips(conn, clip_counts):
        if sync_engine.emit_clip_new(conn, clip, run_id, commit=False) is None:
            raise SigningResetError("eligible clip was rejected at the sync boundary")
        appended += 1
        patch = _clip_meta_payload(clip, run_id)["patch"]
        if sync_engine.emit_clip_meta(
            conn,
            clip.content_hash,
            patch,
            run_id,
            run_id,
            commit=False,
        ) is None:
            raise SigningResetError("eligible clip metadata was rejected at the sync boundary")
        appended += 1
    for item in _eligible_memory(conn, memory_counts):
        if sync_engine.emit_memory_upsert(
            conn, item, run_id, commit=False
        ) is None:
            raise SigningResetError("eligible memory was rejected at the sync boundary")
        appended += 1
    OutboxRepo(conn).append(
        _RESEED_MARKER_KIND,
        _RESEED_MARKER_PAYLOAD,
        run_id,
        commit=False,
    )
    appended += 1
    return appended


def _verify_sendable_range(
    conn: sqlite3.Connection,
    *,
    snapshot_start_seq: int,
    end_seq: int,
    expected_events: int,
) -> None:
    # A replacement app starts from cursor zero.  Verify that its complete
    # observable history contains only this freshly generated snapshot.  A
    # deleted/secret/oversized/malformed retained legacy row must not be hidden
    # by checking only the newly appended suffix.
    cursor = 0
    seen = 0
    while cursor < end_seq:
        page = sync_engine.build_pull(conn, cursor)
        next_cursor = int(page["next_seq"])
        if next_cursor <= cursor or next_cursor > end_seq:
            raise SigningResetError("snapshot outbox verification did not advance safely")
        for event in page["events"]:
            seq = event.get("seq")
            if (
                not isinstance(seq, int)
                or isinstance(seq, bool)
                or not snapshot_start_seq <= seq <= end_seq
            ):
                raise SigningResetError("snapshot outbox verification found an invalid sequence")
            seen += 1
        cursor = next_cursor
    if cursor != end_seq or seen != expected_events:
        raise SigningResetError("snapshot contains an event blocked by the sync boundary")


def prepare_snapshot(
    conn: sqlite3.Connection,
    *,
    apply: bool,
    run_id: str,
    max_events: int = DEFAULT_MAX_EVENTS,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> PreparationResult:
    """Plan or atomically replace outbound history with a snapshot.

    Callers own the connection.  A new apply requires zero paired peers: revoke
    the old app first, then pair the replacement only after this function has
    committed.  An exact retained-marker retry remains a no-op even if the new
    peer has subsequently paired.
    """

    run_id = _validate_run_id(run_id)
    _validate_budget(max_events, max_payload_bytes)
    schema = db.schema_version(conn)
    if schema != db.LATEST_SCHEMA_VERSION:
        raise SigningResetError("database schema is not current")

    if not apply:
        # A read-only connection cannot BEGIN IMMEDIATE.  One deferred read
        # transaction gives all aggregate/fingerprint queries one snapshot.
        owns_transaction = not conn.in_transaction
        if owns_transaction:
            conn.execute("BEGIN")
        try:
            plan = build_snapshot_plan(conn, run_id=run_id)
            _enforce_budget(
                plan,
                max_events=max_events,
                max_payload_bytes=max_payload_bytes,
            )
            paired = int(conn.execute("SELECT COUNT(*) FROM sync_peers").fetchone()[0])
            outbox_before = int(conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0])
            existing, malformed, marker_start, marker_end = (
                _existing_run_fingerprints(conn, run_id)
            )
            marker_contiguous = (
                marker_start is not None
                and marker_end is not None
                and marker_end - marker_start + 1 == plan.events
            )
            idempotent = (
                malformed == 0
                and bool(existing)
                and existing == plan.fingerprints
                and marker_contiguous
            )
            if idempotent:
                _verify_sendable_range(
                    conn,
                    snapshot_start_seq=marker_start,
                    end_seq=marker_end,
                    expected_events=plan.events,
                )
        finally:
            if owns_transaction:
                conn.rollback()
        return PreparationResult(
            mode="dry-run",
            run_id=run_id,
            schema_version=schema,
            paired_devices=paired,
            outbox_before=outbox_before,
            outbox_after=outbox_before,
            reseed_start_seq=marker_start if idempotent else None,
            reseed_end_seq=marker_end if idempotent else None,
            events_planned=plan.events,
            payload_bytes_planned=plan.payload_bytes,
            max_events=max_events,
            max_payload_bytes=max_payload_bytes,
            events_appended=0,
            idempotent_noop=idempotent,
            delivery_verified=False,
            clips=plan.clips,
            memory=plan.memory,
        )

    with unit_of_work(conn):
        paired = int(conn.execute("SELECT COUNT(*) FROM sync_peers").fetchone()[0])
        outbox_before = int(conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0])
        start_seq = OutboxRepo(conn).sequence_high_water()
        plan = build_snapshot_plan(conn, run_id=run_id)
        _enforce_budget(
            plan,
            max_events=max_events,
            max_payload_bytes=max_payload_bytes,
        )
        existing, malformed, marker_start, marker_end = (
            _existing_run_fingerprints(conn, run_id)
        )
        if existing or malformed:
            marker_contiguous = (
                marker_start is not None
                and marker_end is not None
                and marker_end - marker_start + 1 == plan.events
            )
            if malformed or existing != plan.fingerprints or not marker_contiguous:
                raise SigningResetError(
                    "run id already exists with a different or incomplete snapshot"
                )
            _verify_sendable_range(
                conn,
                snapshot_start_seq=marker_start,
                end_seq=marker_end,
                expected_events=plan.events,
            )
            return PreparationResult(
                mode="apply",
                run_id=run_id,
                schema_version=schema,
                paired_devices=paired,
                outbox_before=outbox_before,
                outbox_after=outbox_before,
                reseed_start_seq=marker_start,
                reseed_end_seq=marker_end,
                events_planned=plan.events,
                payload_bytes_planned=plan.payload_bytes,
                max_events=max_events,
                max_payload_bytes=max_payload_bytes,
                events_appended=0,
                idempotent_noop=True,
                delivery_verified=False,
                clips=plan.clips,
                memory=plan.memory,
            )

        # The old peer must be revoked first, and the replacement app must not
        # pair until this atomic snapshot is durable.  With zero peers, normal
        # maintenance has no ACK cursor with which to prune the new rows.
        if paired != 0:
            raise SigningResetError(
                "apply requires zero paired peers; revoke the old peer and pair the replacement app only after apply"
            )

        # Zero peers plus a stopped Desktop is the migration cut-over.  Remove
        # every retained outbound history row in this same transaction before
        # emitting the current snapshot.  SQLite AUTOINCREMENT history remains
        # intact, while a fresh client pulling from zero can no longer replay a
        # deleted, stale, oversized, or newly quarantined legacy event.
        conn.execute("DELETE FROM sync_outbox")
        appended = _emit_snapshot(conn, run_id=run_id)
        if appended != plan.events:
            raise SigningResetError("snapshot changed while the transaction was active")
        end_seq = OutboxRepo(conn).sequence_high_water()
        if end_seq - start_seq != appended:
            raise SigningResetError("snapshot outbox sequence is not contiguous")
        _verify_sendable_range(
            conn,
            snapshot_start_seq=start_seq + 1,
            end_seq=end_seq,
            expected_events=appended,
        )
        outbox_after = int(conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0])
        if outbox_after != appended:
            raise SigningResetError("snapshot outbox contains unexpected retained history")

    return PreparationResult(
        mode="apply",
        run_id=run_id,
        schema_version=schema,
        paired_devices=paired,
        outbox_before=outbox_before,
        outbox_after=outbox_after,
        reseed_start_seq=start_seq + 1,
        reseed_end_seq=end_seq,
        events_planned=plan.events,
        payload_bytes_planned=plan.payload_bytes,
        max_events=max_events,
        max_payload_bytes=max_payload_bytes,
        events_appended=appended,
        idempotent_noop=False,
        delivery_verified=False,
        clips=plan.clips,
        memory=plan.memory,
    )


def _open_read_only(path: Path) -> sqlite3.Connection:
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise SigningResetError("database must be an existing regular file")
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    except sqlite3.Error as exc:
        raise SigningResetError("database could not be opened read-only") from exc


def _open_for_apply(path: Path) -> sqlite3.Connection:
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise SigningResetError("database must be an existing regular file")
    try:
        return db.connect(path)
    except (OSError, sqlite3.Error, db.DatabaseStartupError) as exc:
        raise SigningResetError("database could not be opened for apply") from exc


def _database_token(path: Path) -> str:
    # This value is persisted only in the ignored local state file and is never
    # included in CLI output.  Path plus file identity prevents a state record
    # from silently suppressing a replacement database at the same location.
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise SigningResetError("database must be an existing regular file")
    try:
        stat = path.stat()
    except OSError as exc:
        raise SigningResetError("database identity is unavailable") from exc
    normalized = os.path.normcase(str(path.resolve())).encode("utf-8")
    identity = f"{stat.st_dev}:{stat.st_ino}".encode("ascii")
    return hashlib.sha256(normalized + b"\0" + identity).hexdigest()


def _assert_database_token(path: Path, expected: str) -> None:
    if _database_token(path) != expected:
        raise SigningResetError("database identity changed while opening")


def _validated_state_path(path: Path) -> Path:
    state_root = STATE_ROOT.resolve()
    candidate = path.resolve(strict=False)
    try:
        candidate.relative_to(state_root)
    except ValueError as exc:
        raise SigningResetError(
            "state file must remain under the ignored signing-reset artifact directory"
        ) from exc
    current = candidate.parent
    while current != state_root.parent:
        if current.exists() and current.is_symlink():
            raise SigningResetError("state file parent must not be a symbolic link")
        if current == state_root:
            break
        current = current.parent
    if candidate.exists() and (candidate.is_symlink() or not candidate.is_file()):
        raise SigningResetError("state file must be a regular file")
    return candidate


def _load_state(path: Path) -> dict | None:
    path = _validated_state_path(path)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > _MAX_STATE_FILE_BYTES:
            raise SigningResetError("state file is too large")
        data = json.loads(path.read_text(encoding="utf-8"))
    except SigningResetError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SigningResetError("state file is unreadable") from exc
    if not isinstance(data, dict) or set(data) != {
        "version", "run_id", "database_token", "result"
    }:
        raise SigningResetError("state file is invalid")
    if data["version"] != 1:
        raise SigningResetError("state file version is unsupported")
    if not isinstance(data["database_token"], str) or len(data["database_token"]) != 64:
        raise SigningResetError("state file is invalid")
    _parse_run_id(data.get("run_id"))
    if not isinstance(data.get("result"), dict):
        raise SigningResetError("state file is invalid")
    return data


def _counts_from_safe_dict(value: object) -> SnapshotCounts:
    fields = {
        "total",
        "eligible",
        "skipped_deleted",
        "skipped_persisted_secret",
        "skipped_current_secret",
        "skipped_unsafe_origin",
        "skipped_invalid",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise SigningResetError("state file is invalid")
    if any(
        isinstance(value[field], bool)
        or not isinstance(value[field], int)
        or value[field] < 0
        for field in fields
    ):
        raise SigningResetError("state file is invalid")
    return SnapshotCounts(**{field: value[field] for field in fields})


def _completed_result_from_state(
    state: dict,
    *,
    run_id: str,
    database_token: str,
) -> PreparationResult:
    if state["run_id"] != run_id or state["database_token"] != database_token:
        raise SigningResetError(
            "a different signing-reset run already owns the local state file"
        )
    result = state["result"]
    required = {
        "schema_version",
        "paired_devices",
        "outbox_before",
        "outbox_after",
        "reseed_start_seq",
        "reseed_end_seq",
        "events_planned",
        "payload_bytes_planned",
        "max_events",
        "max_payload_bytes",
        "clips",
        "memory",
    }
    if set(result) != required:
        raise SigningResetError("state file is invalid")
    integer_fields = required - {"clips", "memory"}
    if any(
        isinstance(result[field], bool)
        or not isinstance(result[field], int)
        or result[field] < 0
        for field in integer_fields
    ):
        raise SigningResetError("state file is invalid")
    start_seq = result["reseed_start_seq"]
    end_seq = result["reseed_end_seq"]
    if start_seq > end_seq:
        raise SigningResetError("state file is invalid")
    if result["events_planned"] > 0:
        if end_seq - start_seq + 1 != result["events_planned"]:
            raise SigningResetError("state file is invalid")
    elif start_seq != end_seq:
        raise SigningResetError("state file is invalid")
    return PreparationResult(
        mode="apply",
        run_id=run_id,
        schema_version=result["schema_version"],
        paired_devices=result["paired_devices"],
        outbox_before=result["outbox_before"],
        outbox_after=result["outbox_after"],
        reseed_start_seq=start_seq,
        reseed_end_seq=end_seq,
        events_planned=result["events_planned"],
        payload_bytes_planned=result["payload_bytes_planned"],
        max_events=result["max_events"],
        max_payload_bytes=result["max_payload_bytes"],
        events_appended=0,
        idempotent_noop=True,
        delivery_verified=False,
        clips=_counts_from_safe_dict(result["clips"]),
        memory=_counts_from_safe_dict(result["memory"]),
    )


def _write_state(
    path: Path,
    *,
    run_id: str,
    database_token: str,
    result: PreparationResult,
) -> None:
    path = _validated_state_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path = _validated_state_path(path)
    safe = result.safe_dict()
    state_result = {
        key: safe[key]
        for key in (
            "schema_version",
            "paired_devices",
            "outbox_before",
            "outbox_after",
            "reseed_start_seq",
            "reseed_end_seq",
            "events_planned",
            "payload_bytes_planned",
            "max_events",
            "max_payload_bytes",
            "clips",
            "memory",
        )
    }
    encoded = (
        json.dumps(
            {
                "version": 1,
                "run_id": run_id,
                "database_token": database_token,
                "result": state_result,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if len(encoded) > _MAX_STATE_FILE_BYTES:
        raise SigningResetError("state record is too large")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = None
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            fd = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except (OSError, UnicodeError) as exc:
        raise SigningResetError(
            "snapshot committed but local state recording failed; rerun the same run id"
        ) from exc
    finally:
        if fd is not None:
            os.close(fd)
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


def _verify_delivery(
    conn: sqlite3.Connection,
    *,
    completed: PreparationResult,
) -> PreparationResult:
    """Prove one post-reseed peer ACKed through the recorded high-water.

    This database proof cannot identify the installed APK.  The separate
    final-APK uninstall/fresh-install and pairing evidence remains mandatory.
    """

    schema = db.schema_version(conn)
    if schema != db.LATEST_SCHEMA_VERSION:
        raise SigningResetError("database schema is not current")
    end_seq = completed.reseed_end_seq
    if end_seq is None:
        raise SigningResetError("state file has no reseed delivery range")
    high_water = OutboxRepo(conn).sequence_high_water()
    if high_water != end_seq:
        raise SigningResetError(
            "desktop outbox changed after reseed apply; keep captures frozen and prepare a reviewed replacement run"
        )
    rows = conn.execute(
        "SELECT my_acked_seq, paired_at, last_seen_at FROM sync_peers"
    ).fetchall()
    if len(rows) != 1:
        raise SigningResetError("delivery verification requires exactly one paired peer")
    ack = rows[0][0]
    if isinstance(ack, bool) or not isinstance(ack, int) or not 0 <= ack <= high_water:
        raise SigningResetError("paired peer acknowledgement is outside outbox history")
    if ack < end_seq:
        raise SigningResetError("paired peer has not acknowledged the complete reseed snapshot")
    _, run_at = _parse_run_id(completed.run_id)
    try:
        _, paired_at = _parse_run_id(rows[0][1])
        _, last_seen_at = _parse_run_id(rows[0][2])
    except SigningResetError as exc:
        raise SigningResetError("post-reseed peer timestamps are missing or invalid") from exc
    if paired_at < run_at or last_seen_at < paired_at:
        raise SigningResetError(
            "delivery verification requires a peer paired and synchronized after reseed apply"
        )
    outbox_count = int(conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0])
    return PreparationResult(
        mode="verify-delivery",
        run_id=completed.run_id,
        schema_version=schema,
        paired_devices=1,
        outbox_before=outbox_count,
        outbox_after=outbox_count,
        reseed_start_seq=completed.reseed_start_seq,
        reseed_end_seq=end_seq,
        events_planned=completed.events_planned,
        payload_bytes_planned=completed.payload_bytes_planned,
        max_events=completed.max_events,
        max_payload_bytes=completed.max_payload_bytes,
        events_appended=0,
        idempotent_noop=False,
        delivery_verified=True,
        clips=completed.clips,
        memory=completed.memory,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        required=True,
        type=Path,
        help="existing Desktop clipvault.db (the path is never printed)",
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help="unique UTC timestamp, e.g. 2026-07-22T12:34:56Z",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help=(
            "atomically replace retained outbound history with the snapshot; "
            "default is read-only dry-run"
        ),
    )
    mode.add_argument(
        "--verify-delivery",
        action="store_true",
        help="prove exactly one post-reseed peer ACKed through the recorded reseed range",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=DEFAULT_MAX_EVENTS,
        help=f"fail closed above this event count (default: {DEFAULT_MAX_EVENTS})",
    )
    parser.add_argument(
        "--max-payload-bytes",
        type=int,
        default=DEFAULT_MAX_PAYLOAD_BYTES,
        help=(
            "fail closed above this aggregate stored-payload estimate "
            f"(default: {DEFAULT_MAX_PAYLOAD_BYTES})"
        ),
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help="ignored local completion record under .field-test-artifacts",
    )
    return parser


def _run_cli(args: argparse.Namespace) -> PreparationResult:
    verify_delivery = bool(getattr(args, "verify_delivery", False))
    run_id = (
        _parse_run_id(args.run_id)[0]
        if verify_delivery
        else _validate_run_id(args.run_id)
    )
    _validate_budget(args.max_events, args.max_payload_bytes)
    lock = None
    if args.apply and os.name == "nt":
        from clipvault.instance_lock import (  # noqa: PLC0415
            AlreadyRunningError,
            InstanceLock,
        )

        lock = InstanceLock()
        try:
            lock.acquire()
        except AlreadyRunningError as exc:
            raise SigningResetError(
                "stop the ClipVault Desktop service before apply"
            ) from exc

    conn = None
    try:
        database_token = _database_token(args.db)
        state = _load_state(args.state_file) if args.apply or verify_delivery else None
        if verify_delivery:
            if state is None:
                raise SigningResetError("delivery verification requires the local apply state")
            completed = _completed_result_from_state(
                state,
                run_id=run_id,
                database_token=database_token,
            )
            conn = _open_read_only(args.db)
            _assert_database_token(args.db, database_token)
            conn.execute("BEGIN")
            try:
                return _verify_delivery(conn, completed=completed)
            finally:
                conn.rollback()
        if state is not None:
            # Validate the state structure/ownership, but never accept it as
            # completion proof by itself.  A replaced DB or ACK-pruned marker
            # must fail closed instead of reporting a false idempotent success.
            completed = _completed_result_from_state(
                state,
                run_id=run_id,
                database_token=database_token,
            )
            conn = _open_read_only(args.db)
            _assert_database_token(args.db, database_token)
            verified = prepare_snapshot(
                conn,
                apply=False,
                run_id=run_id,
                max_events=completed.max_events,
                max_payload_bytes=completed.max_payload_bytes,
            )
            if not verified.idempotent_noop:
                raise SigningResetError(
                    "local state exists but retained outbox proof is missing or no longer matches"
                )
            if (
                verified.reseed_start_seq != completed.reseed_start_seq
                or verified.reseed_end_seq != completed.reseed_end_seq
                or verified.events_planned != completed.events_planned
            ):
                raise SigningResetError(
                    "local state does not match the retained outbox range"
                )
            return PreparationResult(
                mode="apply",
                run_id=run_id,
                schema_version=verified.schema_version,
                paired_devices=verified.paired_devices,
                outbox_before=verified.outbox_before,
                outbox_after=verified.outbox_after,
                reseed_start_seq=completed.reseed_start_seq,
                reseed_end_seq=completed.reseed_end_seq,
                events_planned=verified.events_planned,
                payload_bytes_planned=verified.payload_bytes_planned,
                max_events=completed.max_events,
                max_payload_bytes=completed.max_payload_bytes,
                events_appended=0,
                idempotent_noop=True,
                delivery_verified=False,
                clips=verified.clips,
                memory=verified.memory,
            )
        conn = _open_for_apply(args.db) if args.apply else _open_read_only(args.db)
        _assert_database_token(args.db, database_token)
        result = prepare_snapshot(
            conn,
            apply=bool(args.apply),
            run_id=run_id,
            max_events=args.max_events,
            max_payload_bytes=args.max_payload_bytes,
        )
        if args.apply:
            _write_state(
                args.state_file,
                run_id=run_id,
                database_token=database_token,
                result=result,
            )
        return result
    finally:
        if conn is not None:
            conn.close()
        if lock is not None:
            lock.release()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _run_cli(args)
    except SigningResetError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, sort_keys=True))
        return 2
    except Exception as exc:
        # Do not surface SQLite/path/payload exception text through this Owner
        # tool.  The class is enough to diagnose the code path without leaking
        # content or local filesystem details.
        print(json.dumps({
            "status": "failed",
            "error_class": exc.__class__.__name__,
        }, sort_keys=True))
        return 1
    print(json.dumps({"status": "ok", **result.safe_dict()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
