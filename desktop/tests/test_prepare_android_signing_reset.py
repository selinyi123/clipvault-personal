"""Owner signing-reset snapshot tool: privacy, atomicity, and replay gates."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

from clipvault.core import normalize
from clipvault.core.models import Clip
from clipvault.store import db
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.memory_repo import MemoryRepo
from clipvault.store.outbox_repo import OutboxRepo
from clipvault.store.peers_repo import PeersRepo
from clipvault.sync import engine as sync_engine


_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "prepare_android_signing_reset.py"
_spec = importlib.util.spec_from_file_location("prepare_android_signing_reset", _SCRIPT)
reset_tool = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = reset_tool
_spec.loader.exec_module(reset_tool)


NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
RUN_ID = "2026-07-22T12:00:00Z"
FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(reset_tool, "_utc_now", lambda: NOW)


def _clip(
    clip_id: str,
    content: str,
    *,
    is_secret: bool = False,
    deleted: bool = False,
    source_device: str = "desktop-main",
    source_app: str | None = "editor.exe",
    pinned: bool = False,
    favorite: bool = False,
) -> Clip:
    return Clip(
        id=clip_id,
        content=content,
        content_hash=normalize.content_hash(content),
        content_type="text",
        source_device=source_device,
        source_app=source_app,
        created_at="2026-07-20T10:00:00Z",
        last_seen_at="2026-07-21T10:00:00Z",
        is_secret=is_secret,
        secret_level="hard" if is_secret else None,
        secret_reasons=["SG-AWS-ID"] if is_secret else [],
        deleted=deleted,
        pinned=pinned,
        favorite=favorite,
    )


def _seed_safe_snapshot(conn):
    ClipsRepo(conn).insert(
        _clip(
            "01PUBLIC000000000000000001",
            "current public clip",
            pinned=True,
            favorite=True,
        )
    )
    MemoryRepo(conn).upsert(
        "phrase",
        "current public phrase",
        label="safe label",
        pinned=True,
        use_count=7,
        now="2026-07-20T10:00:00Z",
    )


def _args(
    db_path: Path,
    state_path: Path,
    *,
    apply: bool = True,
    verify_delivery: bool = False,
):
    return argparse.Namespace(
        db=db_path,
        run_id=RUN_ID,
        apply=apply,
        verify_delivery=verify_delivery,
        max_events=reset_tool.DEFAULT_MAX_EVENTS,
        max_payload_bytes=reset_tool.DEFAULT_MAX_PAYLOAD_BYTES,
        state_file=state_path,
    )


def test_apply_reemits_only_live_current_safe_state_and_full_clip_meta(conn):
    _seed_safe_snapshot(conn)
    ClipsRepo(conn).insert(
        _clip("01PERSISTEDSECRET000000001", "stored secret", is_secret=True)
    )
    # Simulate a legacy row that predates the current SG rule.
    ClipsRepo(conn).insert(
        _clip("01CURRENTSECRET0000000001", FAKE_AWS_KEY)
    )
    ClipsRepo(conn).insert(
        _clip("01DELETED0000000000000001", "deleted public", deleted=True)
    )
    ClipsRepo(conn).insert(
        _clip(
            "01BAD_ORIGIN00000000000001",
            "unsafe origin public",
            source_device="desktop\nleak",
        )
    )
    deleted_memory = MemoryRepo(conn).upsert("term", "deleted memory")
    MemoryRepo(conn).soft_delete(deleted_memory.id)
    conn.execute(
        "INSERT INTO memory_items"
        "(id,kind,text,label,pinned,use_count,last_used_at,source,created_at,deleted) "
        "VALUES (?,?,?,?,?,?,?,?,?,0)",
        (
            "01SECRET_MEMORY00000000001",
            "term",
            FAKE_AWS_KEY,
            None,
            0,
            0,
            None,
            "manual",
            "2026-07-20T10:00:00Z",
        ),
    )
    conn.commit()

    result = reset_tool.prepare_snapshot(conn, apply=True, run_id=RUN_ID)

    assert result.paired_devices == 0
    assert result.events_appended == 4
    assert result.reseed_start_seq == 1
    assert result.reseed_end_seq == 4
    assert result.clips.safe_dict() == {
        "total": 5,
        "eligible": 1,
        "skipped_deleted": 1,
        "skipped_persisted_secret": 1,
        "skipped_current_secret": 1,
        "skipped_unsafe_origin": 1,
        "skipped_invalid": 0,
    }
    assert result.memory.eligible == 1
    assert result.memory.skipped_deleted == 1
    assert result.memory.skipped_current_secret == 1

    events = OutboxRepo(conn).list_since(0)
    assert [event["kind"] for event in events] == [
        "clip_new",
        "clip_meta",
        "memory_upsert",
        "privacy_noop",
    ]
    assert events[1]["payload"]["patch"] == {
        "pinned": True,
        "favorite": True,
        "deleted": False,
    }
    serialized = json.dumps(events, ensure_ascii=False)
    assert FAKE_AWS_KEY not in serialized
    assert "deleted public" not in serialized
    assert "unsafe origin public" not in serialized
    assert "deleted memory" not in serialized

    # The full clip_meta snapshot is intentional: it repairs all flags and
    # deleted=false after the exact current clip_new. Its LWW clock update is
    # part of the same transaction and uses the bounded run timestamp.
    clocks = conn.execute(
        "SELECT field, ts FROM clip_meta_ts ORDER BY field"
    ).fetchall()
    assert [(row["field"], row["ts"]) for row in clocks] == [
        ("deleted", RUN_ID),
        ("favorite", RUN_ID),
        ("pinned", RUN_ID),
    ]


def test_nonzero_peer_rejects_new_apply_without_any_write(conn):
    _seed_safe_snapshot(conn)
    PeersRepo(conn).upsert_pair(
        "old-phone", "Old phone", "token-hash", "2026-07-22T11:00:00Z"
    )

    with pytest.raises(reset_tool.SigningResetError, match="zero paired peers"):
        reset_tool.prepare_snapshot(conn, apply=True, run_id=RUN_ID)

    assert OutboxRepo(conn).list_since(0) == []
    assert conn.execute("SELECT COUNT(*) FROM clip_meta_ts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM memory_meta_ts").fetchone()[0] == 0


def test_same_retained_run_is_a_proven_noop(conn):
    _seed_safe_snapshot(conn)
    first = reset_tool.prepare_snapshot(conn, apply=True, run_id=RUN_ID)
    second = reset_tool.prepare_snapshot(conn, apply=True, run_id=RUN_ID)

    assert first.events_appended == 4
    assert second.events_appended == 0
    assert second.idempotent_noop is True
    assert len(OutboxRepo(conn).list_since(0)) == 4
    assert conn.execute("SELECT COUNT(*) FROM clip_meta_ts").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM memory_meta_ts").fetchone()[0] == 1


def test_apply_rolls_back_events_and_lww_clocks_on_mid_snapshot_failure(
    conn, monkeypatch
):
    _seed_safe_snapshot(conn)

    def fail_memory(*_args, **_kwargs):
        raise RuntimeError("simulated")

    monkeypatch.setattr(sync_engine, "emit_memory_upsert", fail_memory)
    with pytest.raises(RuntimeError, match="simulated"):
        reset_tool.prepare_snapshot(conn, apply=True, run_id=RUN_ID)

    assert OutboxRepo(conn).list_since(0) == []
    assert conn.execute("SELECT COUNT(*) FROM clip_meta_ts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM memory_meta_ts").fetchone()[0] == 0
    assert not conn.in_transaction


@pytest.mark.parametrize(
    ("max_events", "max_payload_bytes", "message"),
    [
        (2, reset_tool.DEFAULT_MAX_PAYLOAD_BYTES, "event budget"),
        (reset_tool.DEFAULT_MAX_EVENTS, 1, "payload-byte budget"),
    ],
)
def test_apply_budget_excess_fails_closed(
    conn, max_events, max_payload_bytes, message
):
    _seed_safe_snapshot(conn)
    with pytest.raises(reset_tool.SigningResetError, match=message):
        reset_tool.prepare_snapshot(
            conn,
            apply=True,
            run_id=RUN_ID,
            max_events=max_events,
            max_payload_bytes=max_payload_bytes,
        )
    assert OutboxRepo(conn).list_since(0) == []
    assert conn.execute("SELECT COUNT(*) FROM clip_meta_ts").fetchone()[0] == 0


def test_state_hit_requires_retained_matching_outbox_proof(tmp_path, monkeypatch):
    monkeypatch.setattr(reset_tool, "STATE_ROOT", tmp_path)
    database = tmp_path / "clipvault.db"
    state_file = tmp_path / "reset-state.json"
    setup = db.connect(database)
    db.migrate(setup)
    _seed_safe_snapshot(setup)
    setup.close()

    first = reset_tool._run_cli(_args(database, state_file))
    assert first.events_appended == 4
    assert state_file.is_file()

    prune = db.connect(database)
    prune.execute("DELETE FROM sync_outbox")
    prune.commit()
    prune.close()

    with pytest.raises(reset_tool.SigningResetError, match="retained outbox proof"):
        reset_tool._run_cli(_args(database, state_file))


def test_state_and_retained_marker_return_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(reset_tool, "STATE_ROOT", tmp_path)
    database = tmp_path / "clipvault.db"
    state_file = tmp_path / "reset-state.json"
    setup = db.connect(database)
    db.migrate(setup)
    _seed_safe_snapshot(setup)
    setup.close()

    reset_tool._run_cli(_args(database, state_file))
    second = reset_tool._run_cli(_args(database, state_file))

    assert second.idempotent_noop is True
    assert second.events_appended == 0
    verify = db.connect(database)
    try:
        assert verify.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 4
    finally:
        verify.close()


def _prepared_delivery_database(tmp_path, monkeypatch):
    monkeypatch.setattr(reset_tool, "STATE_ROOT", tmp_path)
    database = tmp_path / "clipvault.db"
    state_file = tmp_path / "reset-state.json"
    setup = db.connect(database)
    db.migrate(setup)
    _seed_safe_snapshot(setup)
    setup.close()
    applied = reset_tool._run_cli(_args(database, state_file))
    verify_args = _args(
        database,
        state_file,
        apply=False,
        verify_delivery=True,
    )
    return database, applied, verify_args


def test_delivery_verification_rejects_no_peer(tmp_path, monkeypatch):
    _database, _applied, verify_args = _prepared_delivery_database(
        tmp_path, monkeypatch
    )

    with pytest.raises(reset_tool.SigningResetError, match="exactly one"):
        reset_tool._run_cli(verify_args)


def test_delivery_verification_rejects_ack_below_reseed_end(
    tmp_path, monkeypatch
):
    database, applied, verify_args = _prepared_delivery_database(
        tmp_path, monkeypatch
    )
    conn = db.connect(database)
    PeersRepo(conn).upsert_pair(
        "fresh-phone", "Fresh phone", "fresh-token", RUN_ID
    )
    conn.close()

    with pytest.raises(reset_tool.SigningResetError, match="not acknowledged"):
        reset_tool._run_cli(verify_args)
    assert applied.reseed_end_seq is not None


def test_delivery_verification_rejects_multiple_peers(tmp_path, monkeypatch):
    database, _applied, verify_args = _prepared_delivery_database(
        tmp_path, monkeypatch
    )
    conn = db.connect(database)
    peers = PeersRepo(conn)
    peers.upsert_pair("fresh-a", "Fresh A", "token-a", RUN_ID)
    peers.upsert_pair("fresh-b", "Fresh B", "token-b", RUN_ID)
    conn.close()

    with pytest.raises(reset_tool.SigningResetError, match="exactly one"):
        reset_tool._run_cli(verify_args)


def test_delivery_verification_accepts_single_peer_ack_after_outbox_prune(
    tmp_path, monkeypatch
):
    database, applied, verify_args = _prepared_delivery_database(
        tmp_path, monkeypatch
    )
    assert applied.reseed_end_seq is not None
    conn = db.connect(database)
    peers = PeersRepo(conn)
    peers.upsert_pair("fresh-phone", "Fresh phone", "fresh-token", RUN_ID)
    high_water = OutboxRepo(conn).sequence_high_water()
    peers.set_my_acked(
        "fresh-phone",
        applied.reseed_end_seq,
        high_water=high_water,
    )
    peers.touch_last_seen("fresh-phone", RUN_ID)
    conn.execute(
        "DELETE FROM sync_outbox WHERE seq <= ?",
        (applied.reseed_end_seq,),
    )
    conn.commit()
    conn.close()

    verified = reset_tool._run_cli(verify_args)

    assert verified.delivery_verified is True
    assert verified.paired_devices == 1
    assert verified.reseed_start_seq == applied.reseed_start_seq
    assert verified.reseed_end_seq == applied.reseed_end_seq
    assert verified.outbox_after == 0


def test_safe_output_contains_no_row_content_or_identifiers(conn):
    _seed_safe_snapshot(conn)
    result = reset_tool.prepare_snapshot(conn, apply=False, run_id=RUN_ID)
    rendered = json.dumps(result.safe_dict(), sort_keys=True)

    assert "current public clip" not in rendered
    assert "current public phrase" not in rendered
    assert "safe label" not in rendered
    assert "01PUBLIC000000000000000001" not in rendered
    assert normalize.content_hash("current public clip") not in rendered
    assert "clipvault.db" not in rendered


def test_run_id_rejects_poisoning_future_and_stale_clock_skew(monkeypatch):
    monkeypatch.setattr(reset_tool, "_utc_now", lambda: NOW)

    assert reset_tool._validate_run_id("2026-07-22T12:05:00Z")
    assert reset_tool._validate_run_id("2026-07-21T12:00:00Z")
    with pytest.raises(reset_tool.SigningResetError, match="future"):
        reset_tool._validate_run_id("2026-07-22T12:05:01Z")
    with pytest.raises(reset_tool.SigningResetError, match="older"):
        reset_tool._validate_run_id("2026-07-21T11:59:59Z")


@pytest.mark.skipif(os.name != "nt", reason="Windows named-mutex contract")
def test_cli_apply_rejects_when_desktop_instance_lock_is_held(
    tmp_path, monkeypatch
):
    from clipvault.instance_lock import InstanceLock

    monkeypatch.setattr(reset_tool, "STATE_ROOT", tmp_path)
    database = tmp_path / "clipvault.db"
    state_file = tmp_path / "reset-state.json"
    setup = db.connect(database)
    db.migrate(setup)
    setup.close()

    with InstanceLock():
        with pytest.raises(reset_tool.SigningResetError, match="stop the ClipVault"):
            reset_tool._run_cli(_args(database, state_file))


def test_paginated_pull_exposes_complete_snapshot_after_prepare(conn):
    for index in range(9):
        ClipsRepo(conn).insert(
            _clip(
                f"01PUBLIC{index:018d}",
                f"public migration row {index}",
                pinned=index % 2 == 0,
            )
        )
    for index in range(3):
        MemoryRepo(conn).upsert("term", f"memory migration row {index}")

    result = reset_tool.prepare_snapshot(conn, apply=True, run_id=RUN_ID)
    cursor = 0
    events = []
    while True:
        page = sync_engine.build_pull(conn, cursor)
        events.extend(page["events"])
        cursor = page["next_seq"]
        if not page["has_more"]:
            break

    assert len(events) == result.events_appended == 22
    assert cursor == OutboxRepo(conn).max_seq()
    assert sum(event["kind"] == "clip_new" for event in events) == 9
    assert sum(event["kind"] == "clip_meta" for event in events) == 9
    assert sum(event["kind"] == "memory_upsert" for event in events) == 3
    assert sum(event["kind"] == "privacy_noop" for event in events) == 1


def test_apply_replaces_all_retained_history_before_fresh_pull(conn):
    _seed_safe_snapshot(conn)
    outbox = OutboxRepo(conn)
    outbox.append(
        "clip_new",
        sync_engine.clip_to_data(
            _clip("01OLDDELETED0000000000001", "old deleted content", deleted=True)
        ),
        "2026-07-20T10:00:00Z",
    )
    outbox.append(
        "memory_upsert",
        {
            "kind": "term",
            "text": FAKE_AWS_KEY,
            "label": None,
            "pinned": False,
            "use_count": 0,
            "source": "manual",
        },
        "2026-07-20T10:00:01Z",
    )
    old_high_water = outbox.sequence_high_water()

    result = reset_tool.prepare_snapshot(conn, apply=True, run_id=RUN_ID)
    events = OutboxRepo(conn).list_since(0)

    assert result.outbox_before == 2
    assert result.outbox_after == result.events_appended == 4
    assert result.reseed_start_seq == old_high_water + 1
    assert [event["kind"] for event in events] == [
        "clip_new",
        "clip_meta",
        "memory_upsert",
        "privacy_noop",
    ]
    rendered = json.dumps(events, ensure_ascii=False)
    assert "old deleted content" not in rendered
    assert FAKE_AWS_KEY not in rendered


def test_retained_noop_rejects_marker_with_blocked_clip_meta(conn):
    _seed_safe_snapshot(conn)
    reset_tool.prepare_snapshot(conn, apply=True, run_id=RUN_ID)
    row = conn.execute(
        "SELECT seq, payload FROM sync_outbox WHERE kind = 'clip_meta'"
    ).fetchone()
    payload = json.loads(row["payload"])
    payload["ts"] = "not-a-timestamp"
    conn.execute(
        "UPDATE sync_outbox SET payload = ? WHERE seq = ?",
        (json.dumps(payload), row["seq"]),
    )
    conn.commit()

    with pytest.raises(reset_tool.SigningResetError, match="blocked"):
        reset_tool.prepare_snapshot(conn, apply=True, run_id=RUN_ID)


def test_empty_snapshot_still_has_ackable_reseed_marker(conn):
    first = reset_tool.prepare_snapshot(conn, apply=True, run_id=RUN_ID)
    second = reset_tool.prepare_snapshot(conn, apply=True, run_id=RUN_ID)

    assert first.events_appended == first.events_planned == 1
    assert first.reseed_start_seq == first.reseed_end_seq == 1
    assert second.idempotent_noop is True
    assert OutboxRepo(conn).list_since(0)[0]["kind"] == "privacy_noop"
    pull = sync_engine.build_pull(conn, 0)
    assert [event["kind"] for event in pull["events"]] == ["privacy_noop"]
    assert pull["events"][0]["payload"] == {}
    assert pull["events"][0]["created_at"] == "1970-01-01T00:00:00Z"
    assert pull["next_seq"] == 1


def test_delivery_verification_rejects_tampered_lower_state_range_after_prune(
    tmp_path, monkeypatch
):
    database, applied, verify_args = _prepared_delivery_database(
        tmp_path, monkeypatch
    )
    conn = db.connect(database)
    peers = PeersRepo(conn)
    peers.upsert_pair("fresh-phone", "Fresh phone", "fresh-token", RUN_ID)
    peers.set_my_acked(
        "fresh-phone",
        applied.reseed_end_seq - 1,
        high_water=applied.reseed_end_seq,
    )
    peers.touch_last_seen("fresh-phone", RUN_ID)
    conn.execute(
        "DELETE FROM sync_outbox WHERE seq <= ?",
        (applied.reseed_end_seq,),
    )
    conn.commit()
    conn.close()

    state = json.loads(verify_args.state_file.read_text(encoding="utf-8"))
    state["result"]["reseed_end_seq"] -= 1
    state["result"]["events_planned"] -= 1
    verify_args.state_file.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(reset_tool.SigningResetError, match="outbox changed"):
        reset_tool._run_cli(verify_args)


def test_delivery_verification_rejects_new_desktop_outbox_write(
    tmp_path, monkeypatch
):
    database, applied, verify_args = _prepared_delivery_database(
        tmp_path, monkeypatch
    )
    conn = db.connect(database)
    peers = PeersRepo(conn)
    peers.upsert_pair("fresh-phone", "Fresh phone", "fresh-token", RUN_ID)
    peers.set_my_acked(
        "fresh-phone",
        applied.reseed_end_seq,
        high_water=applied.reseed_end_seq,
    )
    peers.touch_last_seen("fresh-phone", RUN_ID)
    OutboxRepo(conn).append(
        "privacy_noop", {}, "2026-07-22T12:01:00Z"
    )
    conn.close()

    with pytest.raises(reset_tool.SigningResetError, match="outbox changed"):
        reset_tool._run_cli(verify_args)
