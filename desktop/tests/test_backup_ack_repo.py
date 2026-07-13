"""Transactional repository contracts for durable backup acknowledgements."""

import pytest

from clipvault.pipeline import ingest as pipeline
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.unit_of_work import unit_of_work


def _pending_clip(conn, content: str):
    outcome = pipeline.ingest(conn, content, source_device="desktop")
    assert outcome.status == pipeline.STATUS_NEW
    assert BackupQueueRepo(conn).state_of(outcome.clip.id) == "pending"
    return outcome.clip


def _ack_state(conn, clip_id: str) -> tuple[str | None, str, str | None]:
    clip = ClipsRepo(conn).get(clip_id)
    assert clip is not None
    queue_row = conn.execute(
        "SELECT state, done_at FROM backup_queue WHERE clip_id=?", (clip_id,)
    ).fetchone()
    assert queue_row is not None
    return clip.backed_up_at, queue_row["state"], queue_row["done_at"]


def test_commit_false_ack_rolls_back_with_outer_unit_of_work(conn):
    clip = _pending_clip(conn, "backup acknowledgement rollback")
    when = "2026-07-14T01:02:03Z"

    with pytest.raises(RuntimeError, match="simulated acknowledgement failure"):
        with unit_of_work(conn):
            ClipsRepo(conn).set_backed_up_at(clip.id, when, commit=False)
            assert BackupQueueRepo(conn).mark_done(
                clip.id, when, commit=False
            ) is True
            raise RuntimeError("simulated acknowledgement failure")

    assert conn.in_transaction is False
    assert _ack_state(conn, clip.id) == (None, "pending", None)


def test_commit_false_ack_commits_with_outer_unit_of_work(conn):
    clip = _pending_clip(conn, "backup acknowledgement commit")
    when = "2026-07-14T02:03:04Z"

    with unit_of_work(conn):
        ClipsRepo(conn).set_backed_up_at(clip.id, when, commit=False)
        assert BackupQueueRepo(conn).mark_done(
            clip.id, when, commit=False
        ) is True

    assert conn.in_transaction is False
    assert _ack_state(conn, clip.id) == (when, "done", when)


@pytest.mark.parametrize("terminal_state", ["done", "dropped_secret"])
def test_mark_done_does_not_ack_non_pending_rows(conn, terminal_state):
    clip = _pending_clip(conn, f"non-pending backup row {terminal_state}")
    original_done_at = "2026-07-14T03:04:05Z"
    conn.execute(
        "UPDATE backup_queue SET state=?, done_at=? WHERE clip_id=?",
        (terminal_state, original_done_at, clip.id),
    )
    conn.commit()

    changed = BackupQueueRepo(conn).mark_done(
        clip.id, "2026-07-14T04:05:06Z"
    )

    assert changed is False
    row = conn.execute(
        "SELECT state, done_at FROM backup_queue WHERE clip_id=?", (clip.id,)
    ).fetchone()
    assert tuple(row) == (terminal_state, original_done_at)


def test_default_ack_calls_remain_autocommitting(conn):
    clip = _pending_clip(conn, "backward-compatible backup acknowledgement")
    when = "2026-07-14T05:06:07Z"

    # The historical two-positional-argument calls remain valid and durable.
    ClipsRepo(conn).set_backed_up_at(clip.id, when)
    assert conn.in_transaction is False
    assert BackupQueueRepo(conn).mark_done(clip.id, when) is True

    assert conn.in_transaction is False
    assert _ack_state(conn, clip.id) == (when, "done", when)
