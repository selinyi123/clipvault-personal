"""Schema 9 stable FTS rowids, lifecycle atomicity, and adaptive search."""

from __future__ import annotations

import shutil
import sqlite3

import pytest

from clipvault.api import server as api_server
from clipvault.core.models import Clip
from clipvault.store import db
from clipvault.store import clips_repo as clips_repo_module
from clipvault.store.clips_repo import ClipsRepo, _fts_match
from clipvault.store.unit_of_work import unit_of_work


NOW = "2026-07-13T04:00:00Z"


def _clip(
    clip_id: str,
    content: str,
    *,
    is_secret: bool = False,
    deleted: bool = False,
    content_type: str = "text",
    pinned: bool = False,
    last_seen_at: str = NOW,
) -> Clip:
    return Clip(
        id=clip_id,
        content=content,
        content_hash=f"hash-{clip_id}",
        content_type=content_type,
        source_device="test",
        created_at=NOW,
        last_seen_at=last_seen_at,
        is_secret=is_secret,
        secret_level="hard" if is_secret else None,
        secret_reasons=["test"] if is_secret else [],
        deleted=deleted,
        pinned=pinned,
    )


def _mapping(conn, clip_id: str):
    return conn.execute(
        "SELECT search_id FROM clip_search_map WHERE clip_id = ?", (clip_id,)
    ).fetchone()


def _assert_search_invariant(conn) -> None:
    eligible_without_pair = conn.execute(
        "SELECT COUNT(*) FROM clips AS c "
        "LEFT JOIN clip_search_map AS m ON m.clip_id = c.id "
        "LEFT JOIN clips_fts ON clips_fts.rowid = m.search_id "
        "AND clips_fts.id = m.clip_id "
        "WHERE c.is_secret = 0 AND c.deleted = 0 "
        "AND (m.clip_id IS NULL OR clips_fts.rowid IS NULL)"
    ).fetchone()[0]
    ineligible_pairs = conn.execute(
        "SELECT COUNT(*) FROM clip_search_map AS m "
        "JOIN clips AS c ON c.id = m.clip_id "
        "WHERE c.is_secret = 1 OR c.deleted = 1"
    ).fetchone()[0]
    broken_pairs = conn.execute(
        "SELECT COUNT(*) FROM clip_search_map AS m "
        "LEFT JOIN clips AS c ON c.id = m.clip_id "
        "LEFT JOIN clips_fts ON clips_fts.rowid = m.search_id "
        "WHERE c.id IS NULL OR clips_fts.rowid IS NULL "
        "OR clips_fts.id != m.clip_id"
    ).fetchone()[0]
    orphan_fts = conn.execute(
        "SELECT COUNT(*) FROM clips_fts "
        "LEFT JOIN clip_search_map AS m ON m.search_id = clips_fts.rowid "
        "WHERE m.search_id IS NULL OR clips_fts.id != m.clip_id"
    ).fetchone()[0]
    stale_content = conn.execute(
        "SELECT COUNT(*) FROM clip_search_map AS m "
        "JOIN clips AS c ON c.id=m.clip_id "
        "JOIN clips_fts ON clips_fts.rowid=m.search_id "
        "WHERE clips_fts.id!=m.clip_id OR clips_fts.content IS NOT c.content"
    ).fetchone()[0]
    assert (
        eligible_without_pair,
        ineligible_pairs,
        broken_pairs,
        orphan_fts,
        stale_content,
    ) == (
        0,
        0,
        0,
        0,
        0,
    )


def test_populated_v8_to_v9_repairs_and_stabilizes_search_rows(tmp_path):
    v8_migrations = tmp_path / "migrations-v8"
    v9_migrations = tmp_path / "migrations-v9"
    v8_migrations.mkdir()
    v9_migrations.mkdir()
    for script in sorted(db.MIGRATIONS_DIR.glob("[0-9]*.sql")):
        number = int(script.name.split("_", 1)[0])
        if number <= 8:
            shutil.copy2(script, v8_migrations / script.name)
        if number <= 9:
            shutil.copy2(script, v9_migrations / script.name)

    path = tmp_path / "populated-v8.db"
    conn = db.connect(path)
    assert db.migrate(conn, v8_migrations, expected_latest=8) == 8
    rows = (
        ("public-valid", "valid searchable text", 0, 0),
        ("public-missing", "missing searchable text", 0, 0),
        ("secret-leak", "secret leaked token", 1, 0),
        ("deleted-leak", "deleted leaked token", 0, 1),
    )
    conn.executemany(
        "INSERT INTO clips(id,content,content_hash,is_secret,deleted,"
        "source_device,created_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?)",
        [
            (clip_id, content, f"hash-{clip_id}", secret, deleted, "test", NOW, NOW)
            for clip_id, content, secret, deleted in rows
        ],
    )
    conn.executemany(
        "INSERT INTO clips_fts(rowid,id,content) VALUES (?,?,?)",
        (
            (10, "public-valid", "stale legacy text"),
            (11, "public-valid", "stale legacy text"),
            (20, "secret-leak", "secret leaked token"),
            (30, "deleted-leak", "deleted leaked token"),
        ),
    )
    conn.commit()
    conn.close()
    conn = db.connect(path)

    assert db.migrate(conn, v9_migrations, expected_latest=9) == 9
    assert db.migrate(conn, v9_migrations, expected_latest=9) == 9
    mapped = {
        row["clip_id"]: row["search_id"]
        for row in conn.execute(
            "SELECT clip_id,search_id FROM clip_search_map ORDER BY clip_id"
        )
    }
    assert set(mapped) == {"public-valid", "public-missing"}
    assert mapped["public-valid"] == 10
    assert mapped["public-missing"] > 10
    assert conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name='clip_search_map'"
    ).fetchone()[0] >= max(mapped.values())
    indexes = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    assert {
        "idx_clips_public_search_recent",
        "idx_clips_public_list_recent",
    } <= indexes
    list_plan = " ".join(
        row[3]
        for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM clips "
            "WHERE is_secret=0 AND deleted=0 "
            "ORDER BY pinned DESC,last_seen_at DESC,id DESC LIMIT 256"
        )
    )
    search_plan = " ".join(
        row[3]
        for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM clips "
            "WHERE is_secret=0 AND deleted=0 "
            "ORDER BY last_seen_at DESC,id DESC LIMIT 256"
        )
    )
    assert "idx_clips_public_list_recent" in list_plan
    assert "idx_clips_public_search_recent" in search_plan
    assert "USE TEMP B-TREE" not in list_plan
    assert "USE TEMP B-TREE" not in search_plan
    trigger = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' "
        "AND name='clip_search_map_delete_fts'"
    ).fetchone()
    assert trigger is not None
    trigger_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' "
        "AND name='clip_search_map_delete_fts'"
    ).fetchone()[0]
    assert "rowid = OLD.search_id" in trigger_sql
    assert "id = OLD.clip_id" not in trigger_sql
    assert ClipsRepo(conn).fts_contains("public-missing")
    assert not ClipsRepo(conn).fts_contains("secret-leak")
    assert not ClipsRepo(conn).fts_contains("deleted-leak")
    assert [clip.id for clip in ClipsRepo(conn).search_fts("valid searchable")] == [
        "public-valid"
    ]
    assert ClipsRepo(conn).search_fts("stale legacy") == []
    _assert_search_invariant(conn)

    stable = dict(mapped)
    conn.close()
    conn = db.connect(path)
    conn.execute("VACUUM")
    after_vacuum = {
        row["clip_id"]: row["search_id"]
        for row in conn.execute("SELECT clip_id,search_id FROM clip_search_map")
    }
    assert after_vacuum == stable
    _assert_search_invariant(conn)
    ClipsRepo(conn).insert(_clip("after-vacuum", "after vacuum searchable"))
    assert _mapping(conn, "after-vacuum")[0] > max(stable.values())
    conn.close()


def test_search_map_tracks_public_secret_delete_restore_and_physical_delete(conn):
    repo = ClipsRepo(conn)
    repo.insert(_clip("public", "public searchable"))
    first_id = _mapping(conn, "public")[0]
    assert repo.fts_contains("public")

    repo.set_flag("public", "pinned", True)
    repo.touch_seen("public", "2026-07-13T04:00:01Z")
    assert _mapping(conn, "public")[0] == first_id

    repo.set_flag("public", "deleted", True)
    assert _mapping(conn, "public") is None
    assert not repo.fts_contains("public")
    repo.set_flag("public", "deleted", False)
    assert _mapping(conn, "public")[0] > first_id
    assert repo.fts_contains("public")
    conn.execute(
        "INSERT INTO clips_fts(id,content) VALUES (?,?)",
        ("public", "public searchable"),
    )
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) FROM clips_fts WHERE id='public'"
    ).fetchone()[0] == 2
    repo.set_flag("public", "deleted", True)
    assert conn.execute(
        "SELECT COUNT(*) FROM clips_fts WHERE id='public'"
    ).fetchone()[0] == 1
    assert not repo.fts_contains("public")
    assert repo.search_fts("public searchable") == []
    assert repo.repair_search_index()
    assert conn.execute(
        "SELECT COUNT(*) FROM clips_fts WHERE id='public'"
    ).fetchone()[0] == 0
    repo.set_flag("public", "deleted", False)

    repo.insert(_clip("secret", "secret searchable", is_secret=True))
    assert _mapping(conn, "secret") is None
    released = repo.release_secret("secret", "2026-07-13T04:00:02Z")
    assert released is not None and not released.is_secret
    assert repo.fts_contains("secret")
    released_id = _mapping(conn, "secret")[0]
    assert repo.release_secret("secret", "2026-07-13T04:00:03Z") is None
    assert _mapping(conn, "secret")[0] == released_id

    conn.execute("DELETE FROM clips WHERE id = ?", ("public",))
    conn.commit()
    assert _mapping(conn, "public") is None
    assert not repo.fts_contains("public")
    _assert_search_invariant(conn)


def test_api_startup_gate_repairs_legacy_writer_drift(conn):
    # Simulate a schema-8 binary writing into a schema-9 database: it knows the
    # old FTS table but not clip_search_map.  Also seed a duplicate orphan row.
    conn.execute(
        "INSERT INTO clips(id,content,content_hash,source_device,created_at,"
        "last_seen_at) VALUES (?,?,?,?,?,?)",
        ("old-writer", "legacy searchable text", "hash-old-writer", "old", NOW, NOW),
    )
    conn.execute(
        "INSERT INTO clips_fts(id,content) VALUES (?,?)",
        ("old-writer", "legacy searchable text"),
    )
    current = ClipsRepo(conn)
    current.insert(_clip("duplicate", "duplicate searchable text"), commit=False)
    conn.execute(
        "INSERT INTO clips_fts(id,content) VALUES (?,?)",
        ("duplicate", "duplicate searchable text"),
    )
    current.insert(_clip("stale", "current searchable text"), commit=False)
    stale_search_id = _mapping(conn, "stale")[0]
    conn.execute(
        "UPDATE clips_fts SET content=? WHERE rowid=?",
        ("stale legacy text", stale_search_id),
    )
    current.insert(_clip("drift-secret", "secret searchable text", is_secret=True), commit=False)
    conn.execute("INSERT INTO clip_search_map(clip_id) VALUES ('drift-secret')")
    secret_search_id = _mapping(conn, "drift-secret")[0]
    conn.execute(
        "INSERT INTO clips_fts(rowid,id,content) VALUES (?,?,?)",
        (secret_search_id, "drift-secret", "secret searchable text"),
    )
    current.insert(_clip("drift-deleted", "deleted searchable text", deleted=True), commit=False)
    conn.execute("INSERT INTO clip_search_map(clip_id) VALUES ('drift-deleted')")
    deleted_search_id = _mapping(conn, "drift-deleted")[0]
    conn.execute(
        "INSERT INTO clips_fts(rowid,id,content) VALUES (?,?,?)",
        (deleted_search_id, "drift-deleted", "deleted searchable text"),
    )
    conn.commit()
    assert _mapping(conn, "old-writer") is None

    # serve() performs this repair before it signals API readiness and before
    # the runtime starts its clipboard watcher.
    api_server._prepare_database(conn)
    repaired = ClipsRepo(conn)

    assert repaired.fts_contains("old-writer")
    assert [
        clip.id for clip in repaired.list_clips(query="legacy searchable")
    ] == ["old-writer"]
    assert conn.execute(
        "SELECT COUNT(*) FROM clips_fts WHERE id='duplicate'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT content FROM clips_fts WHERE id='stale'"
    ).fetchone()[0] == "current searchable text"
    assert _mapping(conn, "drift-secret") is None
    assert _mapping(conn, "drift-deleted") is None
    _assert_search_invariant(conn)


def test_search_index_repair_failure_rolls_back_complete_previous_state(conn):
    repo = ClipsRepo(conn)
    repo.insert(_clip("repair-rollback", "repair rollback searchable"), commit=False)
    conn.execute(
        "INSERT INTO clips(id,content,content_hash,source_device,created_at,"
        "last_seen_at) VALUES (?,?,?,?,?,?)",
        (
            "repair-missing-map",
            "legacy missing map searchable",
            "hash-repair-missing-map",
            "legacy",
            NOW,
            NOW,
        ),
    )
    conn.execute(
        "INSERT INTO clips_fts(id,content) VALUES (?,?)",
        ("repair-missing-map", "legacy missing map searchable"),
    )
    conn.commit()
    before_map = conn.execute(
        "SELECT search_id,clip_id FROM clip_search_map ORDER BY search_id"
    ).fetchall()
    before_fts = conn.execute(
        "SELECT rowid,id,content FROM clips_fts ORDER BY rowid"
    ).fetchall()
    conn.executescript(
        "CREATE TRIGGER fail_search_repair BEFORE INSERT ON clip_search_map "
        "BEGIN SELECT RAISE(ABORT, 'injected repair failure'); END;"
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected repair failure"):
        repo.repair_search_index()

    assert not conn.in_transaction
    assert conn.execute(
        "SELECT search_id,clip_id FROM clip_search_map ORDER BY search_id"
    ).fetchall() == before_map
    assert conn.execute(
        "SELECT rowid,id,content FROM clips_fts ORDER BY rowid"
    ).fetchall() == before_fts


def test_public_insert_rolls_back_clip_map_and_fts_together(conn):
    class FailingRepo(ClipsRepo):
        def _index_public_clip(self, clip_id: str, content: str) -> None:
            super()._index_public_clip(clip_id, content)
            raise sqlite3.IntegrityError("injected after FTS insert")

    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        FailingRepo(conn).insert(_clip("rollback-insert", "rollback searchable"))

    assert conn.execute(
        "SELECT COUNT(*) FROM clips WHERE id='rollback-insert'"
    ).fetchone()[0] == 0
    assert _mapping(conn, "rollback-insert") is None
    assert conn.execute("SELECT COUNT(*) FROM clips_fts").fetchone()[0] == 0
    assert not conn.in_transaction


def test_outer_unit_of_work_rolls_back_commit_false_search_failure(conn):
    class FailingRepo(ClipsRepo):
        def _index_public_clip(self, clip_id: str, content: str) -> None:
            super()._index_public_clip(clip_id, content)
            raise sqlite3.IntegrityError("injected external transaction failure")

    with pytest.raises(sqlite3.IntegrityError, match="external transaction"):
        with unit_of_work(conn):
            FailingRepo(conn).insert(
                _clip("outer-rollback", "outer rollback searchable"),
                commit=False,
            )

    assert conn.execute(
        "SELECT COUNT(*) FROM clips WHERE id='outer-rollback'"
    ).fetchone()[0] == 0
    assert _mapping(conn, "outer-rollback") is None
    assert conn.execute("SELECT COUNT(*) FROM clips_fts").fetchone()[0] == 0
    assert not conn.in_transaction


def test_release_delete_and_restore_failures_roll_back_all_search_state(conn):
    repo = ClipsRepo(conn)
    repo.insert(_clip("release-fail", "release failure", is_secret=True))
    conn.execute(
        "CREATE TRIGGER fail_map_insert BEFORE INSERT ON clip_search_map "
        "BEGIN SELECT RAISE(ABORT, 'injected map insert'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected map insert"):
        repo.release_secret("release-fail", NOW)
    assert repo.get("release-fail").is_secret
    assert _mapping(conn, "release-fail") is None
    assert not conn.in_transaction
    conn.execute("DROP TRIGGER fail_map_insert")

    repo.insert(_clip("delete-fail", "delete failure"))
    stable_id = _mapping(conn, "delete-fail")[0]
    conn.execute(
        "CREATE TRIGGER fail_map_delete BEFORE DELETE ON clip_search_map "
        "BEGIN SELECT RAISE(ABORT, 'injected map delete'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected map delete"):
        repo.set_flag("delete-fail", "deleted", True)
    assert not repo.get("delete-fail").deleted
    assert _mapping(conn, "delete-fail")[0] == stable_id
    assert repo.fts_contains("delete-fail")
    assert not conn.in_transaction
    conn.execute("DROP TRIGGER fail_map_delete")

    repo.set_flag("delete-fail", "deleted", True)
    conn.execute(
        "CREATE TRIGGER fail_restore BEFORE INSERT ON clip_search_map "
        "BEGIN SELECT RAISE(ABORT, 'injected restore'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected restore"):
        repo.set_flag("delete-fail", "deleted", False)
    assert repo.get("delete-fail").deleted
    assert _mapping(conn, "delete-fail") is None
    assert not repo.fts_contains("delete-fail")
    assert not conn.in_transaction


def _oracle_ids(conn, query: str, *, content_type=None, before_id=None, limit=50):
    where = ["c.is_secret=0", "c.deleted=0", "clips_fts MATCH ?"]
    params = [_fts_match(query)]
    if content_type:
        where.append("c.content_type=?")
        params.append(content_type)
    if before_id:
        where.append("c.id < ?")
        params.append(before_id)
    params.append(limit)
    return [
        row[0]
        for row in conn.execute(
            "SELECT c.id FROM clips_fts "
            "JOIN clip_search_map AS m ON m.search_id=clips_fts.rowid "
            "JOIN clips AS c ON c.id=m.clip_id "
            f"WHERE {' AND '.join(where)} "
            "AND clips_fts.id=m.clip_id "
            "ORDER BY c.pinned DESC,c.last_seen_at DESC,c.id DESC LIMIT ?",
            params,
        )
    ]


def test_common_probe_and_exact_fallback_match_api_order_and_filters(conn):
    repo = ClipsRepo(conn)
    for index in range(320):
        repo.insert(
            _clip(
                f"clip-{index:03d}",
                f"common-token row {index:03d}",
                content_type="command" if index % 2 else "text",
                pinned=index % 47 == 0,
                last_seen_at=f"2026-07-13T03:{index % 60:02d}:{index % 60:02d}Z",
            ),
            commit=False,
        )
    conn.commit()

    cases = (
        (None, None, 1),
        (None, None, 50),
        (None, None, 200),
        ("command", None, 50),
        ("command", None, 200),
        (None, "clip-250", 50),
        ("command", "clip-250", 17),
    )
    for content_type, before_id, limit in cases:
        actual = [
            clip.id
            for clip in repo.list_clips(
                query="common-token",
                content_type=content_type,
                before_id=before_id,
                limit=limit,
            )
        ]
        assert actual == _oracle_ids(
            conn,
            "common-token",
            content_type=content_type,
            before_id=before_id,
            limit=limit,
        )

    assert [clip.id for clip in repo.list_clips(query="row 319")] == ["clip-319"]
    assert repo.list_clips(query="definitely-not-present") == []
    expected_repo = [
        row[0]
        for row in conn.execute(
            "SELECT c.id FROM clips_fts "
            "JOIN clip_search_map AS m ON m.search_id=clips_fts.rowid "
            "JOIN clips AS c ON c.id=m.clip_id "
            "WHERE c.is_secret=0 AND c.deleted=0 AND clips_fts MATCH ? "
            "AND clips_fts.id=m.clip_id "
            "ORDER BY c.last_seen_at DESC,c.id DESC LIMIT 200",
            (_fts_match("common-token"),),
        )
    ]
    assert [clip.id for clip in repo.search_fts("common-token", limit=200)] == (
        expected_repo
    )

    class ProbeMustNotRun(ClipsRepo):
        def _recent_probe_likely_full(self, *args, **kwargs):
            raise AssertionError("filtered or oversized search entered recent hint")

        def _fts_recent_probe_rows(self, *args, **kwargs):
            raise AssertionError("filtered or oversized search entered recent probe")

    fallback_only = ProbeMustNotRun(conn)
    assert fallback_only.list_clips(
        query="common-token", content_type="command", limit=50
    )
    assert fallback_only.list_clips(
        query="common-token", before_id="clip-250", limit=50
    )
    assert len(fallback_only.search_fts("common-token", limit=300)) == 300
    assert fallback_only.list_clips(query="common-token", limit=0) == []
    assert fallback_only.list_clips(query="common-token", limit=-1) == []
    assert fallback_only.list_clips(limit=-1) == []
    assert fallback_only.search_fts("common-token", limit=-1) == []


def test_old_skew_hint_skips_probe_but_keeps_exact_page(conn, monkeypatch):
    monkeypatch.setattr(clips_repo_module, "_FTS_COMMON_MATCH_THRESHOLD", 3)
    monkeypatch.setattr(clips_repo_module, "_FTS_RECENT_PROBE_SIZE", 4)
    repo = ClipsRepo(conn)
    for index in range(5):
        repo.insert(
            _clip(
                f"old-{index}",
                f"old-skew-token match {index}",
                last_seen_at=f"2026-07-12T00:00:0{index}Z",
            ),
            commit=False,
        )
    for index in range(4):
        repo.insert(
            _clip(
                f"recent-{index}",
                f"recent unrelated content {index}",
                last_seen_at=f"2026-07-13T04:00:0{index}Z",
            ),
            commit=False,
        )
    repo.insert(
        _clip(
            "secret-recent",
            "old-skew-token secret",
            is_secret=True,
            last_seen_at="2026-07-13T05:00:00Z",
        ),
        commit=False,
    )
    repo.insert(
        _clip(
            "deleted-recent",
            "old-skew-token deleted",
            deleted=True,
            last_seen_at="2026-07-13T05:00:01Z",
        ),
        commit=False,
    )
    conn.commit()

    class ProbeMustNotRun(ClipsRepo):
        def _fts_recent_probe_rows(self, *args, **kwargs):
            raise AssertionError("historical skew should skip the FTS recent probe")

    actual = [
        clip.id
        for clip in ProbeMustNotRun(conn).list_clips(
            query="old-skew-token", limit=2
        )
    ]
    assert actual == _oracle_ids(conn, "old-skew-token", limit=2)
    assert set(actual).isdisjoint({"secret-recent", "deleted-recent"})


@pytest.mark.parametrize(
    ("query", "decoy", "literal"),
    [
        ("pct%token", "pct-ANY-token", "contains pct%token literally"),
        ("under_token", "underXtoken", "contains under_token literally"),
        (r"slash\token", "slashtoken", r"contains slash\token literally"),
    ],
)
def test_recent_hint_escapes_like_metacharacters(conn, query, decoy, literal):
    repo = ClipsRepo(conn)
    repo.insert(
        _clip("decoy", decoy, last_seen_at="2026-07-13T05:00:00Z")
    )
    assert not repo._recent_probe_likely_full(
        query, limit=1, probe_size=1, api_order=True
    )

    repo.insert(
        _clip("literal", literal, last_seen_at="2026-07-13T05:00:01Z")
    )
    assert repo._recent_probe_likely_full(
        query, limit=1, probe_size=1, api_order=True
    )


def test_recent_hint_supports_repo_order_at_probe_size_boundary(conn):
    repo = ClipsRepo(conn)
    for index in range(256):
        repo.insert(
            _clip(
                f"boundary-{index:03d}",
                f"boundary-token {index:03d}",
                last_seen_at=f"2026-07-13T03:{index % 60:02d}:00Z",
            ),
            commit=False,
        )
    conn.commit()

    assert repo._recent_probe_likely_full(
        "boundary-token", limit=256, probe_size=256, api_order=False
    )
    assert not repo._recent_probe_likely_full(
        "boundary-token", limit=257, probe_size=256, api_order=False
    )


def test_false_path_hint_keeps_exact_unicode_fts_semantics(conn, monkeypatch):
    monkeypatch.setattr(clips_repo_module, "_FTS_COMMON_MATCH_THRESHOLD", 0)
    monkeypatch.setattr(clips_repo_module, "_FTS_RECENT_PROBE_SIZE", 1)
    ClipsRepo(conn).insert(_clip("unicode", "contains äpf token"))

    class FalseHintRepo(ClipsRepo):
        def _recent_probe_likely_full(self, *args, **kwargs):
            return False

    repo = FalseHintRepo(conn)
    assert [clip.id for clip in repo.list_clips(query="ÄPF", limit=1)] == [
        "unicode"
    ]


def test_hint_prefix_miss_keeps_exact_fts_results(conn, monkeypatch):
    monkeypatch.setattr(clips_repo_module, "_FTS_COMMON_MATCH_THRESHOLD", 0)
    monkeypatch.setattr(clips_repo_module, "_FTS_RECENT_PROBE_SIZE", 1)
    prefix = "x" * clips_repo_module._FTS_HINT_CONTENT_CHARS
    repo = ClipsRepo(conn)
    repo.insert(_clip("tail", f"{prefix} tail-only-token"))

    assert not repo._recent_probe_likely_full(
        "tail-only-token", limit=1, probe_size=1, api_order=True
    )
    assert [clip.id for clip in repo.list_clips(query="tail-only-token", limit=1)] == [
        "tail"
    ]


def test_public_search_defends_against_polluted_secret_and_deleted_fts(conn):
    repo = ClipsRepo(conn)
    repo.insert(_clip("safe", "leak-marker safe"))
    repo.insert(_clip("secret", "leak-marker secret", is_secret=True))
    repo.insert(_clip("deleted", "leak-marker deleted", deleted=True))
    for clip_id in ("secret", "deleted"):
        cur = conn.execute(
            "INSERT INTO clip_search_map(clip_id) VALUES (?)", (clip_id,)
        )
        content = repo.get(clip_id).content
        conn.execute(
            "INSERT INTO clips_fts(rowid,id,content) VALUES (?,?,?)",
            (cur.lastrowid, clip_id, content),
        )
    conn.commit()

    assert [clip.id for clip in repo.search_fts("leak-marker")] == ["safe"]
    assert [clip.id for clip in repo.list_clips(query="leak-marker")] == ["safe"]


def test_same_second_id_desc_tie_matches_before_id_direction(conn):
    repo = ClipsRepo(conn)
    for index in range(100):
        repo.insert(
            _clip(f"tie-{index:03d}", f"tie-token {index:03d}"),
            commit=False,
        )
    conn.commit()

    first = repo.list_clips(query="tie-token", limit=50)
    second = repo.list_clips(
        query="tie-token", before_id=first[-1].id, limit=50
    )
    assert [clip.id for clip in first] == [
        f"tie-{index:03d}" for index in range(99, 49, -1)
    ]
    assert [clip.id for clip in second] == [
        f"tie-{index:03d}" for index in range(49, -1, -1)
    ]


def test_common_search_holds_one_snapshot_across_probe(tmp_path):
    path = tmp_path / "snapshot.db"
    first = db.connect(path)
    db.migrate(first)
    other = db.connect(path)
    base = ClipsRepo(first)
    for index in range(4_097):
        base.insert(
            _clip(
                f"base-{index:04d}",
                f"snapshot-common {index:04d}",
                pinned=index % 127 == 0,
                last_seen_at=f"2026-07-13T03:{index % 60:02d}:{index % 60:02d}Z",
            ),
            commit=False,
        )
    first.commit()
    expected_before_injection = _oracle_ids(
        first, "snapshot-common", limit=50
    )
    injected = _clip(
        "injected",
        "snapshot-common newest",
        pinned=True,
        last_seen_at="2026-07-13T05:00:00Z",
    )

    class InjectingRepo(ClipsRepo):
        done = False

        def _fts_recent_probe_rows(self, *args, **kwargs):
            if not self.done:
                ClipsRepo(other).insert(injected)
                self.done = True
            return super()._fts_recent_probe_rows(*args, **kwargs)

    repo = InjectingRepo(first)
    first_page = repo.list_clips(query="snapshot-common", limit=50)
    assert repo.done
    assert [clip.id for clip in first_page] == expected_before_injection
    assert "injected" not in {clip.id for clip in first_page}
    second_page = repo.list_clips(query="snapshot-common", limit=50)
    assert second_page[0].id == "injected"
    first.close()
    other.close()
