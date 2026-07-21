from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import sqlite3
import threading

import pytest

from clipvault.config import Config
from clipvault.core import normalize
from clipvault.core.models import Clip
from clipvault.pipeline import ingest as pipeline
from clipvault.service import ClipVaultService
from clipvault.store import db
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.obsidian_queue_repo import ObsidianQueueRepo
from clipvault.store.unit_of_work import unit_of_work


NOW = "2026-07-12T00:00:00Z"
LEGACY_SECRET = "password=legacy-current-secret-123"


def _cfg(tmp_path: Path, db_path: str = ":memory:") -> Config:
    return Config(
        device_id="desktop-test",
        device_name="desktop-test",
        db_path=db_path,
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
    )


def _ingest(conn, text: str, clip_id: str):
    return pipeline.ingest(
        conn,
        text,
        source_device="desktop-test",
        now_fn=lambda: NOW,
        new_id_fn=lambda: clip_id,
    ).clip


def _legacy_public_secret(conn, clip_id: str, *, released: bool = False) -> Clip:
    """Persist a row that predates the current SG-ASSIGN detector."""

    clip = Clip(
        id=clip_id,
        content=LEGACY_SECRET,
        content_hash=normalize.content_hash(LEGACY_SECRET),
        content_type="text",
        source_device="desktop-test",
        created_at=NOW,
        last_seen_at=NOW,
        released=released,
        released_at=NOW if released else None,
    )
    ClipsRepo(conn).insert(clip)
    assert ObsidianQueueRepo(conn).enqueue(clip.id, NOW)
    return clip


def _is_fts_indexed(conn, clip_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM clip_search_map AS m "
        "JOIN clips_fts ON clips_fts.rowid=m.search_id "
        "WHERE m.clip_id=? AND clips_fts.id=m.clip_id",
        (clip_id,),
    ).fetchone() is not None


def _guarded_thread(name, target, errors):
    def run():
        try:
            target()
        except BaseException as exc:
            errors.append((name, exc))

    return run


def _join_threads(threads, errors, *, timeout: float = 10.0) -> None:
    for thread in threads:
        thread.join(timeout)
    assert not [thread.name for thread in threads if thread.is_alive()]
    assert not errors, [(name, repr(exc)) for name, exc in errors]


def test_reconcile_missing_and_cleanup_are_row_bounded(conn):
    queue = ObsidianQueueRepo(conn)
    clips = [
        _ingest(conn, f"bounded queue {index}", f"clipbounded{index:02d}")
        for index in range(4)
    ]
    conn.execute("DELETE FROM obsidian_queue")
    conn.commit()

    assert queue.reconcile_missing(NOW, limit=2) == 2
    assert conn.execute("SELECT COUNT(*) FROM obsidian_queue").fetchone()[0] == 2
    assert queue.reconcile_missing(NOW, limit=2) == 2

    conn.executemany(
        "UPDATE clips SET obsidian_path='already-written' WHERE id=?",
        ((clip.id,) for clip in clips[:3]),
    )
    conn.commit()
    assert queue.cleanup_ineligible(limit=2) == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM obsidian_queue q JOIN clips c ON c.id=q.clip_id "
        "WHERE c.obsidian_path IS NOT NULL"
    ).fetchone()[0] == 1


def test_reconcile_failure_rolls_back_rows_cursor_and_transaction(conn):
    first = _ingest(conn, "reconcile rollback one", "clipreconcile01")
    second = _ingest(conn, "reconcile rollback two", "clipreconcile02")
    conn.execute("DELETE FROM obsidian_queue")
    conn.execute(
        "CREATE TRIGGER fail_second_reconcile BEFORE INSERT ON obsidian_queue "
        f"WHEN NEW.clip_id='{second.id}' BEGIN "
        "SELECT RAISE(ABORT, 'simulated reconcile failure'); END"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="reconcile failure"):
        ObsidianQueueRepo(conn).reconcile_missing(NOW, limit=2)

    assert conn.in_transaction is False
    assert conn.execute("SELECT COUNT(*) FROM obsidian_queue").fetchone()[0] == 0
    cursor = conn.execute(
        "SELECT last_created_at, last_clip_id FROM obsidian_reconcile_state WHERE singleton=1"
    ).fetchone()
    assert tuple(cursor) == ("", "")
    assert first.id != second.id


def test_claim_is_atomic_across_connections_and_expired_lease_recovers(tmp_path):
    db_path = str(tmp_path / "claims.db")
    setup = db.connect(db_path)
    db.migrate(setup)
    clip = _ingest(setup, "claim exactly once", "clipclaim0001")
    setup.close()
    barrier = threading.Barrier(2)

    def claim_once():
        conn = db.connect(db_path)
        try:
            barrier.wait(timeout=5)
            return ObsidianQueueRepo(conn).claim_ready(NOW, limit=1, lease_seconds=60)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        batches = list(pool.map(lambda _: claim_once(), range(2)))

    claims = [claim for batch in batches for claim in batch]
    assert [claim.clip_id for claim in claims] == [clip.id]

    before_expiry = db.connect(db_path)
    try:
        assert ObsidianQueueRepo(before_expiry).claim_ready(
            "2026-07-12T00:00:59Z", limit=1
        ) == []
    finally:
        before_expiry.close()

    after_expiry = db.connect(db_path)
    try:
        recovered = ObsidianQueueRepo(after_expiry).claim_ready(
            "2026-07-12T00:01:00Z", limit=1
        )
        assert [claim.clip_id for claim in recovered] == [clip.id]
    finally:
        after_expiry.close()


def test_cleanup_does_not_delete_an_active_claim(conn):
    clip = _ingest(conn, "active claim cleanup", "clipactiveclaim01")
    queue = ObsidianQueueRepo(conn)
    claim = queue.claim_one(clip.id, NOW, lease_seconds=60)
    assert claim is not None
    conn.execute("UPDATE clips SET obsidian_path='finished-elsewhere' WHERE id=?", (clip.id,))
    conn.commit()

    assert queue.cleanup_ineligible(limit=10) == 0
    assert conn.execute(
        "SELECT state FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone()[0] == claim.state

    # Claim recovery does not select the now-ineligible clip, but converts the
    # expired lease back to pending so bounded cleanup may remove it safely.
    assert queue.claim_ready("2026-07-12T00:01:00Z", limit=1) == []
    assert queue.cleanup_ineligible(limit=10) == 1


def test_poison_row_does_not_stop_later_ready_item(conn, tmp_path):
    poison = _ingest(conn, "poison timestamp row", "clip000000poison")
    healthy = _ingest(conn, "healthy row after poison", "clip000001healthy")
    conn.execute("UPDATE clips SET created_at='not-a-time' WHERE id=?", (poison.id,))
    conn.commit()
    service = ClipVaultService(conn, _cfg(tmp_path))

    assert service.retry_obsidian_sweep(
        limit=2, max_runtime_ms=10_000, now_fn=lambda: NOW
    ) == 1

    poison_row = conn.execute(
        "SELECT state, attempts FROM obsidian_queue WHERE clip_id=?", (poison.id,)
    ).fetchone()
    assert poison_row["state"] == "pending" and poison_row["attempts"] == 1
    assert ClipsRepo(conn).get(healthy.id).obsidian_path is not None


def test_file_write_is_reused_after_db_finalize_failure(conn, tmp_path, monkeypatch):
    clip = _ingest(conn, "file survives db finalize", "clipfinalize0001")
    service = ClipVaultService(conn, _cfg(tmp_path))
    original = service.clips.set_obsidian_path
    calls = {"count": 0}

    def fail_once(clip_id, path, *, commit=True):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("simulated db finalize failure")
        return original(clip_id, path, commit=commit)

    monkeypatch.setattr(service.clips, "set_obsidian_path", fail_once)

    assert service.write_obsidian_or_queue(clip) is False
    files = list((tmp_path / "vault").rglob("*.md"))
    assert len(files) == 1
    next_attempt_at = conn.execute(
        "SELECT next_attempt_at FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone()[0]

    assert service.retry_obsidian_sweep(
        limit=1, max_runtime_ms=10_000, now_fn=lambda: next_attempt_at
    ) == 1
    assert len(list((tmp_path / "vault").rglob("*.md"))) == 1
    assert ClipsRepo(conn).get(clip.id).obsidian_path == str(files[0])


def test_current_secret_gate_quarantines_legacy_row_before_writer(
    conn, tmp_path, monkeypatch, caplog
):
    clip = _legacy_public_secret(conn, "cliplegacysecret01")
    service = ClipVaultService(conn, _cfg(tmp_path))
    claim = service.obsidian_queue.claim_one(clip.id, NOW)
    assert claim is not None and _is_fts_indexed(conn, clip.id)

    def must_not_write(_clip):
        raise AssertionError("current Secret Guard hit reached the writer")

    monkeypatch.setattr(service, "_try_write_obsidian", must_not_write)
    with caplog.at_level("INFO", logger="clipvault.application.obsidian"):
        assert service._process_obsidian_claim(claim, NOW) is False

    stored = ClipsRepo(conn).get(clip.id)
    assert stored is not None
    assert stored.is_secret is True
    assert stored.secret_level == "hard"
    assert stored.secret_reasons == ["SG-ASSIGN"]
    assert stored.obsidian_path is None
    assert _is_fts_indexed(conn, clip.id) is False
    assert conn.execute(
        "SELECT 1 FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone() is None
    assert conn.in_transaction is False
    assert LEGACY_SECRET not in caplog.text
    assert list((tmp_path / "vault").rglob("*")) == []


@pytest.mark.parametrize(
    ("column", "value"),
    (("obsidian_path", "legacy-existing.md"), ("deleted", 1)),
)
def test_current_secret_gate_runs_before_other_stale_claim_cleanup(
    conn, tmp_path, monkeypatch, column, value
):
    clip = _legacy_public_secret(conn, f"cliplegacy{column}01")
    service = ClipVaultService(conn, _cfg(tmp_path))
    claim = service.obsidian_queue.claim_one(clip.id, NOW)
    assert claim is not None and _is_fts_indexed(conn, clip.id)
    conn.execute(f"UPDATE clips SET {column}=? WHERE id=?", (value, clip.id))
    conn.commit()

    monkeypatch.setattr(
        service,
        "_try_write_obsidian",
        lambda _clip: (_ for _ in ()).throw(
            AssertionError("stale current-secret row reached the writer")
        ),
    )
    assert service._process_obsidian_claim(claim, NOW) is False

    stored = ClipsRepo(conn).get(clip.id)
    assert stored is not None and stored.is_secret is True
    assert stored.secret_reasons == ["SG-ASSIGN"]
    assert getattr(stored, column) == (bool(value) if column == "deleted" else value)
    assert _is_fts_indexed(conn, clip.id) is False
    assert conn.execute(
        "SELECT 1 FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone() is None


def test_current_secret_quarantine_rolls_back_if_claim_delete_fails(
    conn, tmp_path, monkeypatch, caplog
):
    clip = _legacy_public_secret(conn, "cliplegacyrollback01")
    service = ClipVaultService(conn, _cfg(tmp_path))
    claim = service.obsidian_queue.claim_one(clip.id, NOW)
    assert claim is not None and _is_fts_indexed(conn, clip.id)
    original_mark_done = service.obsidian_queue.mark_done

    def delete_then_fail(got_claim, *, commit=True):
        assert original_mark_done(got_claim, commit=commit)
        raise RuntimeError("private failure detail must not be logged")

    monkeypatch.setattr(service.obsidian_queue, "mark_done", delete_then_fail)
    monkeypatch.setattr(
        service,
        "_try_write_obsidian",
        lambda _clip: (_ for _ in ()).throw(
            AssertionError("rolled-back quarantine reached the writer")
        ),
    )
    with caplog.at_level("ERROR", logger="clipvault.application.obsidian"):
        assert service._process_obsidian_claim(claim, NOW) is False

    stored = ClipsRepo(conn).get(clip.id)
    assert stored is not None and stored.is_secret is False
    assert stored.secret_level is None and stored.secret_reasons == []
    assert _is_fts_indexed(conn, clip.id) is True
    row = conn.execute(
        "SELECT state FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone()
    assert row is not None and row["state"] == claim.state
    assert conn.in_transaction is False
    assert "RuntimeError" in caplog.text
    assert "private failure detail" not in caplog.text
    assert LEGACY_SECRET not in caplog.text


def test_expired_secret_claim_cannot_quarantine_after_lease_changes_owner(
    conn, tmp_path, monkeypatch
):
    clip = _legacy_public_secret(conn, "clipsecretleasehandoff01")
    service = ClipVaultService(conn, _cfg(tmp_path))
    old_claim = service.obsidian_queue.claim_one(clip.id, NOW, lease_seconds=60)
    assert old_claim is not None
    new_claims = service.obsidian_queue.claim_ready(
        "2026-07-12T00:01:00Z", limit=1
    )
    assert len(new_claims) == 1
    new_claim = new_claims[0]
    assert new_claim.token != old_claim.token

    monkeypatch.setattr(
        service,
        "_try_write_obsidian",
        lambda _clip: (_ for _ in ()).throw(
            AssertionError("stale claim reached the writer")
        ),
    )
    assert service._process_obsidian_claim(old_claim, NOW) is False

    stored = ClipsRepo(conn).get(clip.id)
    assert stored is not None and stored.is_secret is False
    assert stored.secret_level is None and stored.secret_reasons == []
    assert _is_fts_indexed(conn, clip.id) is True
    row = conn.execute(
        "SELECT state FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone()
    assert row is not None and row["state"] == new_claim.state
    assert conn.in_transaction is False


def test_owner_release_wins_before_current_secret_claim_preflight(
    conn, tmp_path, monkeypatch
):
    clip = _legacy_public_secret(conn, "clipreleasedsecret01")
    notifications = []
    service = ClipVaultService(
        conn,
        _cfg(tmp_path),
        obsidian_notify=lambda: notifications.append(True),
    )
    claim = service.obsidian_queue.claim_one(clip.id, NOW)
    assert claim is not None
    assert service.release_clip(clip.id) is True
    assert notifications == [True]
    expected = tmp_path / "vault" / "released.md"
    seen = []

    def allowed(got_clip):
        seen.append(got_clip)
        return str(expected), None

    monkeypatch.setattr(service, "_try_write_obsidian", allowed)
    assert service._process_obsidian_claim(claim, NOW) is True

    stored = ClipsRepo(conn).get(clip.id)
    assert seen and seen[0].released is True
    assert stored is not None and stored.released is True
    assert stored.is_secret is False
    assert stored.obsidian_path == str(expected)
    assert conn.execute(
        "SELECT 1 FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone() is None


def test_current_secret_quarantine_wins_then_owner_release_reindexes_and_requeues(
    conn, tmp_path, monkeypatch
):
    clip = _legacy_public_secret(conn, "clipquarantinethenrelease01")
    notifications = []
    service = ClipVaultService(
        conn,
        _cfg(tmp_path),
        obsidian_notify=lambda: notifications.append(True),
    )
    claim = service.obsidian_queue.claim_one(clip.id, NOW)
    assert claim is not None
    monkeypatch.setattr(
        service,
        "_try_write_obsidian",
        lambda _clip: (_ for _ in ()).throw(
            AssertionError("quarantined clip reached the writer")
        ),
    )

    assert service._process_obsidian_claim(claim, NOW) is False
    quarantined = ClipsRepo(conn).get(clip.id)
    assert quarantined is not None and quarantined.is_secret is True
    assert _is_fts_indexed(conn, clip.id) is False
    assert conn.execute(
        "SELECT 1 FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone() is None

    assert service.release_clip(clip.id) is True
    released = ClipsRepo(conn).get(clip.id)
    assert released is not None and released.released is True
    assert released.is_secret is False
    assert _is_fts_indexed(conn, clip.id) is True
    row = conn.execute(
        "SELECT state FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone()
    assert row is not None and row["state"] == "pending"
    assert notifications == [True]


def test_release_commit_precedes_blocked_worker_preflight_on_separate_connections(
    tmp_path
):
    db_path = str(tmp_path / "release-first.db")
    setup = db.connect(db_path)
    db.migrate(setup)
    clip = _legacy_public_secret(setup, "clipreleasefirstlocked01")
    claim = ObsidianQueueRepo(setup).claim_one(clip.id, NOW)
    assert claim is not None
    setup.close()

    both_connected = threading.Barrier(3)
    release_updated = threading.Event()
    worker_begin_attempted = threading.Event()
    writer_called = threading.Event()
    errors = []
    results = {}
    expected_path = str(tmp_path / "vault" / "release-first.md")

    def release_first():
        conn = db.connect(db_path)
        try:
            service = ClipVaultService(
                conn,
                _cfg(tmp_path, db_path),
                obsidian_notify=lambda: None,
            )
            both_connected.wait(timeout=5)
            # Keep the release uncommitted while the independent worker sends
            # BEGIN IMMEDIATE.  Its trace callback proves the lock request was
            # issued before this outer transaction is allowed to commit.
            with unit_of_work(conn):
                results["release"] = service.release_clip(clip.id)
                release_updated.set()
                assert worker_begin_attempted.wait(5)
        finally:
            conn.close()

    def blocked_worker():
        conn = db.connect(db_path)
        try:
            service = ClipVaultService(conn, _cfg(tmp_path, db_path))

            def trace(statement):
                if statement.strip().upper() == "BEGIN IMMEDIATE":
                    worker_begin_attempted.set()

            conn.set_trace_callback(trace)
            both_connected.wait(timeout=5)
            assert release_updated.wait(5)

            def allowed(got_clip):
                assert got_clip.released is True
                writer_called.set()
                return expected_path, None

            results["worker"] = service.obsidian_commands.process_claim(
                claim,
                NOW,
                try_write=allowed,
            )
        finally:
            conn.close()

    threads = [
        threading.Thread(
            name="release-first",
            target=_guarded_thread("release", release_first, errors),
        ),
        threading.Thread(
            name="blocked-worker",
            target=_guarded_thread("worker", blocked_worker, errors),
        ),
    ]
    for thread in threads:
        thread.start()
    try:
        both_connected.wait(timeout=5)
    except threading.BrokenBarrierError:
        # Thread-side failures are collected below after a bounded join.
        pass
    _join_threads(threads, errors)

    assert results == {"release": True, "worker": True}
    assert worker_begin_attempted.is_set() and writer_called.is_set()
    verify = db.connect(db_path)
    try:
        stored = ClipsRepo(verify).get(clip.id)
        assert stored is not None and stored.released is True
        assert stored.is_secret is False
        assert stored.obsidian_path == expected_path
        assert _is_fts_indexed(verify, clip.id) is True
        assert verify.execute(
            "SELECT 1 FROM obsidian_queue WHERE clip_id=?", (clip.id,)
        ).fetchone() is None
    finally:
        verify.close()


def test_quarantine_commit_precedes_blocked_release_on_separate_connections(
    tmp_path
):
    db_path = str(tmp_path / "quarantine-first.db")
    setup = db.connect(db_path)
    db.migrate(setup)
    clip = _legacy_public_secret(setup, "clipquarantinefirstlocked01")
    claim = ObsidianQueueRepo(setup).claim_one(clip.id, NOW)
    assert claim is not None
    setup.close()

    both_connected = threading.Barrier(3)
    quarantine_updated = threading.Event()
    release_begin_attempted = threading.Event()
    errors = []
    results = {}
    notifications = []

    def quarantine_first():
        conn = db.connect(db_path)
        try:
            service = ClipVaultService(conn, _cfg(tmp_path, db_path))
            both_connected.wait(timeout=5)
            # Hold the quarantined row, FTS deletion, and claim consumption
            # uncommitted until the Owner release has actually requested the
            # independent SQLite writer lock.
            with unit_of_work(conn):
                results["worker"] = service.obsidian_commands.process_claim(
                    claim,
                    NOW,
                    try_write=lambda _clip: (_ for _ in ()).throw(
                        AssertionError("quarantined clip reached the writer")
                    ),
                )
                stored = ClipsRepo(conn).get(clip.id)
                assert stored is not None and stored.is_secret is True
                assert _is_fts_indexed(conn, clip.id) is False
                quarantine_updated.set()
                assert release_begin_attempted.wait(5)
        finally:
            conn.close()

    def blocked_release():
        conn = db.connect(db_path)
        try:
            service = ClipVaultService(
                conn,
                _cfg(tmp_path, db_path),
                obsidian_notify=lambda: notifications.append(True),
            )

            def trace(statement):
                if statement.strip().upper() == "BEGIN IMMEDIATE":
                    release_begin_attempted.set()

            conn.set_trace_callback(trace)
            both_connected.wait(timeout=5)
            assert quarantine_updated.wait(5)
            results["release"] = service.release_clip(clip.id)
        finally:
            conn.close()

    threads = [
        threading.Thread(
            name="quarantine-first",
            target=_guarded_thread("worker", quarantine_first, errors),
        ),
        threading.Thread(
            name="blocked-release",
            target=_guarded_thread("release", blocked_release, errors),
        ),
    ]
    for thread in threads:
        thread.start()
    try:
        both_connected.wait(timeout=5)
    except threading.BrokenBarrierError:
        # Thread-side failures are collected below after a bounded join.
        pass
    _join_threads(threads, errors)

    assert results == {"worker": False, "release": True}
    assert release_begin_attempted.is_set()
    assert notifications == [True]
    verify = db.connect(db_path)
    try:
        stored = ClipsRepo(verify).get(clip.id)
        assert stored is not None and stored.released is True
        assert stored.is_secret is False
        assert stored.secret_level is None and stored.secret_reasons == []
        assert _is_fts_indexed(verify, clip.id) is True
        row = verify.execute(
            "SELECT state FROM obsidian_queue WHERE clip_id=?", (clip.id,)
        ).fetchone()
        assert row is not None and row["state"] == "pending"
    finally:
        verify.close()


def test_expired_old_owner_cannot_finalize_or_delete_new_claim(conn, tmp_path):
    clip = _ingest(conn, "lease owner handoff", "clipownerhandoff01")
    service = ClipVaultService(conn, _cfg(tmp_path))
    queue = ObsidianQueueRepo(conn)
    old_claim = queue.claim_one(clip.id, NOW, lease_seconds=60)
    assert old_claim is not None
    new_claims = queue.claim_ready("2026-07-12T00:01:00Z", limit=1)
    assert len(new_claims) == 1
    new_claim = new_claims[0]
    assert new_claim.token != old_claim.token

    # The stale worker may still finish its filesystem call, but ownership loss
    # must roll back obsidian_path and preserve the newer claim.
    assert service._process_obsidian_claim(old_claim, NOW) is False
    assert ClipsRepo(conn).get(clip.id).obsidian_path is None
    row = conn.execute(
        "SELECT state FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone()
    assert row["state"] == new_claim.state
    assert len(list((tmp_path / "vault").rglob("*.md"))) == 1

    # The current owner reuses the file by clip id, then atomically records the
    # path and consumes only its own claim.
    assert service._process_obsidian_claim(
        new_claim, "2026-07-12T00:01:00Z"
    ) is True
    assert ClipsRepo(conn).get(clip.id).obsidian_path is not None
    assert conn.execute(
        "SELECT 1 FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone() is None
    assert len(list((tmp_path / "vault").rglob("*.md"))) == 1


def test_populated_v6_upgrades_to_bounded_v7_reconciliation(tmp_path):
    v6_migrations = tmp_path / "migrations-v6"
    v6_migrations.mkdir()
    v7_migrations = tmp_path / "migrations-v7"
    v7_migrations.mkdir()
    for script in sorted(db.MIGRATIONS_DIR.glob("[0-9]*.sql")):
        number = int(script.name.split("_", 1)[0])
        if number <= 6:
            shutil.copy2(script, v6_migrations / script.name)
        if number <= 7:
            shutil.copy2(script, v7_migrations / script.name)

    conn = db.connect(tmp_path / "upgrade.db")
    try:
        assert db.migrate(conn, v6_migrations, expected_latest=6) == 6
        # Populate a real v6 shape directly.  Current production repositories
        # require the latest schema and must not be used to fabricate legacy
        # databases in migration tests.
        clip_ids = [f"clipv6upgrade{index:02d}" for index in range(3)]
        conn.executemany(
            "INSERT INTO clips(id,content,content_hash,source_device,created_at,"
            "last_seen_at) VALUES (?,?,?,?,?,?)",
            [
                (clip_id, f"v6 populated {index}", f"hash-v6-{index}",
                 "desktop-test", NOW, NOW)
                for index, clip_id in enumerate(clip_ids)
            ],
        )
        conn.executemany(
            "INSERT INTO obsidian_queue(clip_id,state,attempts,next_attempt_at,"
            "created_at,updated_at) VALUES (?,'pending',0,?,?,?)",
            [(clip_id, NOW, NOW, NOW) for clip_id in clip_ids],
        )
        conn.execute("DELETE FROM obsidian_queue WHERE clip_id=?", (clip_ids[1],))
        conn.commit()

        assert db.migrate(conn, v7_migrations, expected_latest=7) == 7
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_clips_obsidian_reconcile" in indexes
        assert "idx_obsidian_queue_cleanup" in indexes
        assert "idx_obsidian_queue_claim_expiry" in indexes
        plan = " ".join(
            row[3]
            for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT id, created_at FROM clips "
                "WHERE obsidian_path IS NULL AND is_secret=0 AND deleted=0 "
                "AND (created_at, id) > (?, ?) "
                "ORDER BY created_at, id LIMIT ?",
                ("", "", 1),
            ).fetchall()
        )
        assert "idx_clips_obsidian_reconcile" in plan

        lease_plan = " ".join(
                row[3]
                for row in conn.execute(
                    "EXPLAIN QUERY PLAN SELECT clip_id FROM obsidian_queue "
                    "INDEXED BY idx_obsidian_queue_claim_expiry "
                    "WHERE state >= 'claimed:' AND state < 'claimed;' "
                "AND next_attempt_at <= ? ORDER BY next_attempt_at, clip_id LIMIT ?",
                (NOW, 1),
            ).fetchall()
        )
        assert "SEARCH obsidian_queue USING INDEX idx_obsidian_queue_claim_expiry" in lease_plan
        assert "USE TEMP B-TREE" not in lease_plan

        queue = ObsidianQueueRepo(conn)
        for _ in range(3):
            queue.reconcile_missing(NOW, limit=1)
        assert conn.execute(
            "SELECT 1 FROM obsidian_queue WHERE clip_id=?", (clip_ids[1],)
        ).fetchone() is not None
    finally:
        conn.close()
