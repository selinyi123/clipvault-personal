"""Regression gates for Personal Memory transaction and replay safety.

These tests intentionally exercise failure points between the Memory fact row,
the per-item logical clock, and the sync outbox.  A logical user action must
either commit all three pieces of state or leave none of them behind.
"""

from __future__ import annotations

import pytest

from clipvault.api import handlers
from clipvault.api.handlers import Api
from clipvault.config import Config
from clipvault.memory import importers
from clipvault.service import ClipVaultService
from clipvault.store.memory_repo import MemoryRepo
from clipvault.store.outbox_repo import OutboxRepo
from clipvault.store.unit_of_work import unit_of_work
from clipvault.sync import engine

FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture
def api(conn, tmp_path):
    cfg = Config(
        device_id="01MEMORYATOMICDESKTOP000001",
        device_name="desktop",
        db_path=":memory:",
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
    )
    return Api(ClipVaultService(conn, cfg))


def _memory_outbox(conn) -> list[dict]:
    return [
        event
        for event in OutboxRepo(conn).list_since(0)
        if event["kind"] in ("memory_upsert", "memory_delete")
    ]


def _memory_clock(conn, kind: str, text: str) -> str:
    row = conn.execute(
        "SELECT ts FROM memory_meta_ts WHERE kind=? AND text=?",
        (kind, text),
    ).fetchone()
    return row[0] if row else ""


def _fail_memory_append(monkeypatch, *, after_insert: bool = False):
    original = OutboxRepo.append

    def fail(self, kind, payload, when, *, commit=True):
        if kind.startswith("memory_"):
            if after_insert:
                original(self, kind, payload, when, commit=False)
            raise RuntimeError("injected memory outbox failure")
        return original(self, kind, payload, when, commit=commit)

    monkeypatch.setattr(OutboxRepo, "append", fail)
    return original


def test_create_memory_rolls_back_fact_clock_and_outbox_on_append_failure(
    api, conn, monkeypatch
):
    _fail_memory_append(monkeypatch, after_insert=True)

    with pytest.raises(RuntimeError, match="injected memory outbox failure"):
        api.create_memory({"kind": "term", "text": "atomic create"})

    assert MemoryRepo(conn).by_kind_text("term", "atomic create") is None
    assert _memory_clock(conn, "term", "atomic create") == ""
    assert _memory_outbox(conn) == []
    assert conn.in_transaction is False


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "term", "text": "x" * (1_048_576 + 1)},
        {"kind": "term", "text": "invalid label", "label": {}},
        {"kind": "term", "text": "invalid pin", "pinned": "false"},
        {"kind": "term", "text": "long label", "label": "x" * 4097},
    ],
)
def test_create_memory_rejects_invalid_wire_fields_without_side_effects(
    api, conn, body
):
    code, response = api.create_memory(body)

    assert code == 400
    assert response["error"]["code"] == "bad_request"
    assert MemoryRepo(conn).list() == []
    assert conn.execute("SELECT COUNT(*) FROM memory_meta_ts").fetchone()[0] == 0
    assert _memory_outbox(conn) == []
    assert conn.in_transaction is False


def test_delete_memory_rolls_back_fact_clock_and_outbox_on_append_failure(
    api, conn, monkeypatch
):
    _, created = api.create_memory({"kind": "term", "text": "atomic delete"})
    item_id = created["memory"]["id"]
    clock_before = _memory_clock(conn, "term", "atomic delete")
    events_before = _memory_outbox(conn)
    _fail_memory_append(monkeypatch, after_insert=True)

    with pytest.raises(RuntimeError, match="injected memory outbox failure"):
        api.delete_memory(item_id)

    item = MemoryRepo(conn).get(item_id)
    assert item is not None and item.deleted is False
    assert _memory_clock(conn, "term", "atomic delete") == clock_before
    assert _memory_outbox(conn) == events_before
    assert conn.in_transaction is False


def test_promote_rolls_back_memory_fact_clock_and_outbox_on_append_failure(
    api, conn, monkeypatch
):
    _, created = api.create_clip({"content": "kubectl get atomic-pods"})
    clip_id = created["clip"]["id"]
    _fail_memory_append(monkeypatch, after_insert=True)

    with pytest.raises(RuntimeError, match="injected memory outbox failure"):
        api.promote_clip(clip_id)

    assert MemoryRepo(conn).by_kind_text("command", "kubectl get atomic-pods") is None
    assert _memory_clock(conn, "command", "kubectl get atomic-pods") == ""
    assert _memory_outbox(conn) == []
    assert conn.in_transaction is False


def test_promote_rescans_full_legacy_clip_before_truncating(api, conn):
    safe_prefix = "a" * 220
    _, created = api.create_clip({"content": safe_prefix})
    clip_id = created["clip"]["id"]
    conn.execute(
        "UPDATE clips SET content=?, is_secret=0, released=0 WHERE id=?",
        (safe_prefix + " " + FAKE_AWS_KEY, clip_id),
    )
    conn.commit()
    events_before = _memory_outbox(conn)

    code, _ = api.promote_clip(clip_id)

    assert code == 404
    assert MemoryRepo(conn).list() == []
    assert _memory_outbox(conn) == events_before


def test_import_failure_leaves_no_orphan_and_rerun_can_publish(
    conn, monkeypatch
):
    repo = MemoryRepo(conn)
    original = _fail_memory_append(monkeypatch, after_insert=True)

    with pytest.raises(RuntimeError, match="injected memory outbox failure"):
        importers.apply(repo, [("term", "retryable import")], "github_import")

    assert repo.by_kind_text("term", "retryable import") is None
    assert _memory_clock(conn, "term", "retryable import") == ""
    assert _memory_outbox(conn) == []
    assert conn.in_transaction is False

    monkeypatch.setattr(OutboxRepo, "append", original)
    assert importers.apply(
        repo, [("term", "retryable import")], "github_import"
    ) == 1
    assert repo.by_kind_text("term", "retryable import") is not None
    assert [event["payload"]["text"] for event in _memory_outbox(conn)] == [
        "retryable import"
    ]


def test_importer_does_not_cross_clock_only_tombstone(conn):
    text = "clock-only imported tombstone"
    remote_delete = {
        "origin_device": "peer",
        "seq": 2,
        "kind": "memory_delete",
        "ts": "2026-07-14T10:00:00Z",
        "data": {
            "kind": "term",
            "text": text,
            "ts": "2026-07-14T10:00:00Z",
        },
    }
    engine.apply_push(conn, "peer", [remote_delete], service=None)

    assert importers.apply(
        MemoryRepo(conn), [("term", text)], "github_import"
    ) == 0
    assert MemoryRepo(conn).by_kind_text("term", text) is None
    assert _memory_outbox(conn) == []


def test_standalone_memory_emit_rolls_back_clock_and_partial_outbox(
    conn, monkeypatch
):
    item = MemoryRepo(conn).upsert("term", "standalone emit")
    _fail_memory_append(monkeypatch, after_insert=True)

    with pytest.raises(RuntimeError, match="injected memory outbox failure"):
        engine.emit_memory_upsert(conn, item, "2026-07-14T10:00:00Z")

    assert _memory_clock(conn, "term", "standalone emit") == ""
    assert _memory_outbox(conn) == []
    assert conn.in_transaction is False


def test_memory_repo_commit_false_participates_in_outer_unit_of_work(conn):
    repo = MemoryRepo(conn)

    with pytest.raises(RuntimeError, match="rollback outer memory work"):
        with unit_of_work(conn):
            repo.upsert("term", "nested rollback", commit=False)
            raise RuntimeError("rollback outer memory work")

    assert repo.by_kind_text("term", "nested rollback") is None
    assert conn.in_transaction is False


def test_standalone_emitter_default_uses_savepoint_inside_outer_uow(conn):
    item = MemoryRepo(conn).upsert("term", "nested standalone emitter")

    with pytest.raises(RuntimeError, match="rollback outer emitter"):
        with unit_of_work(conn):
            engine.emit_memory_upsert(
                conn, item, "2026-07-14T10:00:00Z"
            )
            raise RuntimeError("rollback outer emitter")

    assert _memory_clock(conn, item.kind, item.text) == ""
    assert _memory_outbox(conn) == []
    assert conn.in_transaction is False


def test_memory_emitters_reject_invalid_local_timestamps(conn):
    item = MemoryRepo(conn).upsert("term", "invalid local clock")

    with pytest.raises(ValueError, match="timestamp"):
        engine.emit_memory_upsert(conn, item, "not-a-timestamp")
    with pytest.raises(ValueError, match="timestamp"):
        engine.emit_memory_delete(
            conn,
            item.kind,
            item.text,
            "not-a-timestamp",
            "2026-07-14T10:00:00Z",
        )

    assert _memory_clock(conn, item.kind, item.text) == ""
    assert _memory_outbox(conn) == []


def test_memory_clock_advances_for_same_second_local_actions(conn):
    repo = MemoryRepo(conn)
    item = repo.upsert("term", "same-second memory")
    now = "2026-07-14T10:00:00Z"

    engine.emit_memory_upsert(conn, item, now)
    engine.emit_memory_delete(conn, item.kind, item.text, now, now)
    engine.emit_memory_upsert(conn, item, now)

    assert _memory_clock(conn, item.kind, item.text) == "2026-07-14T10:00:02Z"
    delete_events = [
        event for event in _memory_outbox(conn) if event["kind"] == "memory_delete"
    ]
    assert delete_events[-1]["payload"]["ts"] == "2026-07-14T10:00:01Z"


def test_memory_clock_advances_when_wall_clock_moves_backwards(conn):
    repo = MemoryRepo(conn)
    item = repo.upsert("term", "clock rollback memory")

    engine.emit_memory_upsert(conn, item, "2026-07-14T10:20:00Z")
    engine.emit_memory_delete(
        conn,
        item.kind,
        item.text,
        "2026-07-14T10:10:00Z",
        "2026-07-14T10:10:00Z",
    )

    assert _memory_clock(conn, item.kind, item.text) == "2026-07-14T10:20:01Z"
    assert _memory_outbox(conn)[-1]["payload"]["ts"] == "2026-07-14T10:20:01Z"


def test_memory_clock_ceiling_uses_local_fence_without_invalid_wire_time(conn):
    repo = MemoryRepo(conn)
    item = repo.upsert("term", "memory clock ceiling")
    ceiling = "9999-12-31T23:59:59Z"
    conn.execute(
        "INSERT INTO memory_meta_ts(kind, text, ts) VALUES (?,?,?)",
        (item.kind, item.text, ceiling),
    )
    conn.commit()

    engine.emit_memory_delete(
        conn, item.kind, item.text, ceiling, ceiling
    )

    stored = _memory_clock(conn, item.kind, item.text)
    assert stored.startswith(ceiling)
    assert stored > ceiling
    assert _memory_outbox(conn)[-1]["payload"]["ts"] == ceiling


def test_repeated_local_actions_at_clock_ceiling_resist_gap_replay(
    api, conn, monkeypatch
):
    ceiling = "9999-12-31T23:59:59Z"
    monkeypatch.setattr(handlers, "_now_iso", lambda: ceiling)
    _, created = api.create_memory({"kind": "term", "text": "ceiling owner fence"})
    item_id = created["memory"]["id"]
    conn.execute(
        "UPDATE memory_meta_ts SET ts=? WHERE kind=? AND text=?",
        (ceiling, "term", "ceiling owner fence"),
    )
    conn.commit()
    remote_delete = {
        "origin_device": "peer",
        "seq": 2,
        "kind": "memory_delete",
        "ts": ceiling,
        "data": {
            "kind": "term",
            "text": "ceiling owner fence",
            "ts": ceiling,
        },
    }

    assert engine.apply_push(conn, "peer", [remote_delete], service=None) == 0
    assert MemoryRepo(conn).get(item_id).deleted is True
    assert api.create_memory(
        {"kind": "term", "text": "ceiling owner fence"}
    )[0] == 201
    assert api.delete_memory(item_id)[0] == 200
    assert api.create_memory(
        {"kind": "term", "text": "ceiling owner fence"}
    )[0] == 201
    assert _memory_clock(conn, "term", "ceiling owner fence") > ceiling
    delete_events = [
        event
        for event in _memory_outbox(conn)
        if event["kind"] == "memory_delete"
    ]
    assert delete_events[-1]["payload"]["ts"] == ceiling

    assert engine.apply_push(conn, "peer", [remote_delete], service=None) == 0
    assert MemoryRepo(conn).get(item_id).deleted is False


def test_gapped_delete_replay_cannot_undo_same_second_local_readd(
    api, conn, monkeypatch
):
    now = ["2026-07-14T09:59:59Z"]
    monkeypatch.setattr(handlers, "_now_iso", lambda: now[0])
    _, created = api.create_memory({"kind": "term", "text": "readd wins"})
    item_id = created["memory"]["id"]

    now[0] = "2026-07-14T10:00:00Z"
    remote_delete = {
        "origin_device": "peer",
        "seq": 2,
        "kind": "memory_delete",
        "ts": now[0],
        "data": {"kind": "term", "text": "readd wins", "ts": now[0]},
    }
    assert engine.apply_push(conn, "peer", [remote_delete], service=None) == 0
    assert MemoryRepo(conn).get(item_id).deleted is True

    api.create_memory({"kind": "term", "text": "readd wins"})
    assert MemoryRepo(conn).get(item_id).deleted is False

    assert engine.apply_push(conn, "peer", [remote_delete], service=None) == 0
    assert MemoryRepo(conn).get(item_id).deleted is False


def test_gapped_remote_upsert_replay_does_not_resurrect_local_delete(
    api, conn, monkeypatch
):
    now = "2026-07-14T10:00:00Z"
    monkeypatch.setattr(handlers, "_now_iso", lambda: now)
    remote_upsert = {
        "origin_device": "peer",
        "seq": 2,
        "kind": "memory_upsert",
        "ts": now,
        "data": {
            "kind": "term",
            "text": "remote tombstone",
            "label": None,
            "pinned": False,
            "use_count": 7,
            "source": "manual",
        },
    }

    assert engine.apply_push(conn, "peer", [remote_upsert], service=None) == 0
    item = MemoryRepo(conn).by_kind_text("term", "remote tombstone")
    assert item is not None and item.use_count == 7
    assert api.delete_memory(item.id)[0] == 200
    assert MemoryRepo(conn).get(item.id).deleted is True

    replay = dict(remote_upsert)
    replay["data"] = dict(remote_upsert["data"])
    replay["data"].update({"label": "stale", "pinned": True, "use_count": 99})
    assert engine.apply_push(conn, "peer", [replay], service=None) == 0
    replayed = MemoryRepo(conn).get(item.id)
    assert replayed is not None and replayed.deleted is True
    assert replayed.use_count == 7
    assert replayed.label is None and replayed.pinned is False


def test_remote_upsert_does_not_create_over_clock_only_tombstone(conn):
    text = "delete arrived before upsert"
    deleted_at = "2026-07-14T10:00:00Z"
    remote_delete = {
        "origin_device": "peer",
        "seq": 2,
        "kind": "memory_delete",
        "ts": deleted_at,
        "data": {"kind": "term", "text": text, "ts": deleted_at},
    }
    remote_upsert = {
        "origin_device": "peer",
        "seq": 3,
        "kind": "memory_upsert",
        "ts": deleted_at,
        "data": {
            "kind": "term",
            "text": text,
            "label": None,
            "pinned": False,
            "use_count": 1,
            "source": "manual",
        },
    }

    assert engine.apply_push(conn, "peer", [remote_delete], service=None) == 0
    assert _memory_clock(conn, "term", text) == deleted_at
    assert engine.apply_push(conn, "peer", [remote_upsert], service=None) == 0
    assert MemoryRepo(conn).by_kind_text("term", text) is None


def test_remote_upsert_cannot_sanitize_legacy_secret_label(conn):
    repo = MemoryRepo(conn)
    item = repo.upsert("term", "legacy labelled row", label="safe")
    conn.execute(
        "UPDATE memory_items SET label=?, pinned=0, use_count=1 WHERE id=?",
        (FAKE_AWS_KEY, item.id),
    )
    conn.commit()
    remote_upsert = {
        "origin_device": "peer",
        "seq": 2,
        "kind": "memory_upsert",
        "ts": "2026-07-14T10:00:00Z",
        "data": {
            "kind": "term",
            "text": "legacy labelled row",
            "label": "sanitized by peer",
            "pinned": True,
            "use_count": 99,
            "source": "manual",
        },
    }

    assert engine.apply_push(conn, "peer", [remote_upsert], service=None) == 0
    unchanged = repo.get(item.id)
    assert unchanged.label == FAKE_AWS_KEY
    assert unchanged.pinned is False and unchanged.use_count == 1
    assert repo.list() == []


def test_remote_secret_delete_is_acked_without_persisting_key(conn, caplog):
    event = {
        "origin_device": "peer",
        "seq": 1,
        "kind": "memory_delete",
        "ts": "2026-07-14T10:00:00Z",
        "data": {
            "kind": "term",
            "text": FAKE_AWS_KEY,
            "ts": "2026-07-14T10:00:00Z",
        },
    }

    assert engine.apply_push(conn, "peer", [event], service=None) == 1
    assert MemoryRepo(conn).by_kind_text("term", FAKE_AWS_KEY) is None
    assert _memory_clock(conn, "term", FAKE_AWS_KEY) == ""
    assert FAKE_AWS_KEY not in caplog.text


def test_delete_does_not_emit_legacy_secret_label(api, conn):
    _, created = api.create_memory(
        {"kind": "term", "text": "legacy delete", "label": "safe"}
    )
    item_id = created["memory"]["id"]
    conn.execute(
        "UPDATE memory_items SET label=? WHERE id=?",
        (FAKE_AWS_KEY, item_id),
    )
    conn.commit()
    events_before = _memory_outbox(conn)

    assert api.delete_memory(item_id)[0] == 200

    assert MemoryRepo(conn).get(item_id).deleted is True
    assert _memory_outbox(conn) == events_before


def test_pull_blocks_legacy_memory_events_when_current_label_is_secret(
    api, conn
):
    _, created = api.create_memory(
        {"kind": "term", "text": "legacy pull", "label": "safe"}
    )
    item_id = created["memory"]["id"]
    first_seq = OutboxRepo(conn).max_seq()
    conn.execute(
        "UPDATE memory_items SET label=? WHERE id=?",
        (FAKE_AWS_KEY, item_id),
    )
    conn.commit()
    delete_seq = OutboxRepo(conn).append(
        "memory_delete",
        {
            "kind": "term",
            "text": "legacy pull",
            "ts": "2026-07-14T10:00:00Z",
        },
        "2026-07-14T10:00:00Z",
    )

    upsert_pull = engine.build_pull(conn, since_seq=0)
    delete_pull = engine.build_pull(conn, since_seq=first_seq)

    assert upsert_pull["events"] == []
    assert upsert_pull["next_seq"] >= first_seq
    assert delete_pull["events"] == []
    assert delete_pull["next_seq"] == delete_seq


def test_remote_delete_rolls_back_soft_delete_when_clock_write_fails(
    api, conn, monkeypatch
):
    monkeypatch.setattr(handlers, "_now_iso", lambda: "2026-07-14T09:00:00Z")
    _, created = api.create_memory({"kind": "term", "text": "remote rollback"})
    item_id = created["memory"]["id"]
    clock_before = _memory_clock(conn, "term", "remote rollback")

    def fail_clock(*args, **kwargs):
        raise RuntimeError("injected memory clock failure")

    monkeypatch.setattr(engine, "_set_mem_ts", fail_clock)
    remote_delete = {
        "origin_device": "peer",
        "seq": 1,
        "kind": "memory_delete",
        "ts": "2026-07-14T10:00:00Z",
        "data": {
            "kind": "term",
            "text": "remote rollback",
            "ts": "2026-07-14T10:00:00Z",
        },
    }

    with pytest.raises(RuntimeError, match="injected memory clock failure"):
        engine.apply_push(conn, "peer", [remote_delete], service=None)

    item = MemoryRepo(conn).get(item_id)
    assert item is not None and item.deleted is False
    assert _memory_clock(conn, "term", "remote rollback") == clock_before
    assert conn.in_transaction is False
