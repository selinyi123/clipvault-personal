"""A1 (migration), A2 (full save), A3 (dedup, no resurrection)."""

import sqlite3

import pytest

from clipvault.core import normalize
from clipvault.pipeline import ingest as pipeline
from clipvault.store import db
from clipvault.store.clips_repo import ClipsRepo

FIXED_NOW = "2026-06-12T08:30:00Z"
FIXED_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

EXPECTED_TABLES = {
    "schema_meta", "clips", "memory_items", "sync_outbox", "sync_peers",
    "backup_queue", "obsidian_queue", "obsidian_reconcile_state",
}


def test_a1_migration_from_zero(conn):
    assert db.schema_version(conn) == 8
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert EXPECTED_TABLES <= names
    assert "clips_fts" in names  # fts5 virtual table
    assert "clip_meta_ts" in names  # added by 0002 (SYNC-2)
    assert "memory_meta_ts" in names  # added by 0003 (SYNC-2 memory LWW)


def test_a1_migration_idempotent(conn):
    assert db.migrate(conn) == 8  # second run is a no-op, returns current version


def test_a1_v5_to_v6_backfills_only_eligible_obsidian_rows(tmp_path):
    """Exercise the real populated upgrade path, not only a fresh database."""
    v5_migrations = tmp_path / "v5-migrations"
    v5_migrations.mkdir()
    for script in sorted(db.MIGRATIONS_DIR.glob("[0-9]*.sql")):
        if int(script.name.split("_", 1)[0]) > 5:
            continue
        (v5_migrations / script.name).write_text(
            script.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    raw = db.connect(":memory:")
    assert db.migrate(raw, v5_migrations) == 5
    created = "2026-07-01T00:00:00Z"
    rows = (
        ("public-pending", "public", "hash-public", 0, 0, None),
        ("secret-pending", "secret", "hash-secret", 1, 0, None),
        ("deleted-public", "deleted", "hash-deleted", 0, 1, None),
        ("already-written", "written", "hash-written", 0, 0, "vault/note.md"),
    )
    raw.executemany(
        "INSERT INTO clips("
        "id, content, content_hash, is_secret, deleted, obsidian_path, "
        "source_device, created_at, last_seen_at"
        ") VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (clip_id, content, content_hash, is_secret, deleted, obsidian_path,
             "desktop-test", created, created)
            for clip_id, content, content_hash, is_secret, deleted, obsidian_path in rows
        ],
    )
    raw.commit()

    migration_0006 = next(db.MIGRATIONS_DIR.glob("0006_*.sql"))
    (v5_migrations / migration_0006.name).write_text(
        migration_0006.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert db.migrate(raw, v5_migrations) == 6
    queued = raw.execute(
        "SELECT clip_id, state, attempts, next_attempt_at "
        "FROM obsidian_queue ORDER BY clip_id"
    ).fetchall()
    assert [tuple(row) for row in queued] == [
        ("public-pending", "pending", 0, created),
    ]
    assert db.migrate(raw, v5_migrations) == 6
    assert raw.execute("SELECT COUNT(*) FROM obsidian_queue").fetchone()[0] == 1


def test_a1_populated_v7_to_v8_indexes_suggestion_candidates_without_reordering(
    tmp_path,
):
    """The v8 index is additive: it must preserve candidate semantics while
    changing the populated-database query plan from a full scan to a bounded
    public-recency index scan.
    """
    v7_migrations = tmp_path / "v7-migrations"
    v7_migrations.mkdir()
    v8_migrations = tmp_path / "v8-migrations"
    v8_migrations.mkdir()
    for script in sorted(db.MIGRATIONS_DIR.glob("[0-9]*.sql")):
        number = int(script.name.split("_", 1)[0])
        if number <= 7:
            (v7_migrations / script.name).write_text(
                script.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        if number <= 8:
            (v8_migrations / script.name).write_text(
                script.read_text(encoding="utf-8"),
                encoding="utf-8",
            )

    raw = db.connect(tmp_path / "v7-upgrade.db")
    try:
        assert db.migrate(raw, v7_migrations) == 7
        created = "2026-07-01T00:00:00Z"
        rows = [
            (
                "public-favorite",
                "favorite",
                "hash-public-favorite",
                0,
                0,
                "2026-07-04T00:00:00Z",
                1,
                1,
            ),
            (
                "public-repeated",
                "repeated",
                "hash-public-repeated",
                0,
                0,
                "2026-07-03T00:00:00Z",
                3,
                0,
            ),
            (
                "public-ineligible",
                "ineligible",
                "hash-public-ineligible",
                0,
                0,
                "2026-07-05T00:00:00Z",
                1,
                0,
            ),
            (
                "secret-repeated",
                "secret",
                "hash-secret-repeated",
                1,
                0,
                "2026-07-06T00:00:00Z",
                4,
                0,
            ),
            (
                "deleted-repeated",
                "deleted",
                "hash-deleted-repeated",
                0,
                1,
                "2026-07-07T00:00:00Z",
                4,
                0,
            ),
        ]
        raw.executemany(
            "INSERT INTO clips("
            "id, content, content_hash, is_secret, deleted, last_seen_at, "
            "times_seen, favorite, source_device, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (*row, "desktop-test", created)
                for row in rows
            ],
        )
        raw.commit()

        query = (
            "SELECT id FROM clips "
            "WHERE is_secret = 0 AND deleted = 0 AND last_seen_at >= ? "
            "AND (favorite = 1 OR times_seen >= 3) "
            "ORDER BY last_seen_at DESC, id DESC LIMIT ?"
        )
        params = ("2026-07-01T00:00:00Z", 200)
        before = [row[0] for row in raw.execute(query, params).fetchall()]
        assert before == ["public-favorite", "public-repeated"]

        assert db.migrate(raw, v8_migrations) == 8
        after = [row[0] for row in raw.execute(query, params).fetchall()]
        after_plan = " ".join(
            row[3]
            for row in raw.execute(
                "EXPLAIN QUERY PLAN " + query,
                params,
            ).fetchall()
        )
        assert after == before
        assert "idx_clips_suggest_recent" in after_plan
        assert "USE TEMP B-TREE FOR ORDER BY" not in after_plan

        index_sql = raw.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'index' AND name = 'idx_clips_suggest_recent'"
        ).fetchone()[0]
        normalized_index_sql = " ".join(index_sql.lower().split())
        assert "on clips(last_seen_at desc, id desc)" in normalized_index_sql
        assert "where is_secret = 0 and deleted = 0" in normalized_index_sql
        assert "and (favorite = 1 or times_seen >= 3)" in normalized_index_sql
    finally:
        raw.close()


def test_suggestion_candidates_use_deterministic_id_tiebreak(conn):
    seen = "2026-07-13T00:00:00Z"
    ids = [f"same-second-{index:03d}" for index in range(205)]
    conn.executemany(
        "INSERT INTO clips("
        "id, content, content_hash, is_secret, deleted, last_seen_at, "
        "times_seen, favorite, source_device, created_at"
        ") VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                clip_id,
                f"candidate {index}",
                f"hash-same-second-{index:03d}",
                0,
                0,
                seen,
                3,
                0,
                "desktop-test",
                seen,
            )
            for index, clip_id in enumerate(ids)
        ],
    )
    conn.commit()

    candidates = ClipsRepo(conn).suggest_candidates(
        "2026-07-12T00:00:00Z",
        limit=200,
    )

    assert [clip.id for clip in candidates] == sorted(ids, reverse=True)[:200]


def test_suggestion_index_tracks_eligibility_and_privacy_transitions(conn):
    seen = "2026-07-13T00:00:00Z"
    conn.executemany(
        "INSERT INTO clips("
        "id, content, content_hash, is_secret, deleted, last_seen_at, "
        "times_seen, favorite, source_device, created_at"
        ") VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("by-use", "use", "hash-by-use", 0, 0, seen, 2, 0, "test", seen),
            (
                "by-favorite",
                "favorite",
                "hash-by-favorite",
                0,
                0,
                seen,
                1,
                0,
                "test",
                seen,
            ),
            (
                "released",
                "released",
                "hash-released",
                1,
                0,
                seen,
                3,
                0,
                "test",
                seen,
            ),
        ],
    )
    conn.commit()
    repo = ClipsRepo(conn)

    assert repo.suggest_candidates("2026-07-12T00:00:00Z") == []

    repo.touch_seen("by-use", "2026-07-13T00:00:01Z")
    repo.set_flag("by-favorite", "favorite", True)
    released = repo.release_secret("released", "2026-07-13T00:00:02Z")
    assert released is not None
    assert {clip.id for clip in repo.suggest_candidates("2026-07-12T00:00:00Z")} == {
        "by-use",
        "by-favorite",
        "released",
    }

    repo.set_flag("by-use", "deleted", True)
    assert {clip.id for clip in repo.suggest_candidates("2026-07-12T00:00:00Z")} == {
        "by-favorite",
        "released",
    }


def test_a1_failed_migration_rolls_back_script_and_schema_version(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_broken.sql").write_text(
        """
        CREATE TABLE schema_meta (version INTEGER NOT NULL);
        CREATE TABLE leaked_table (id INTEGER PRIMARY KEY);
        INSERT INTO leaked_table(id) VALUES (1);
        SELECT missing_column FROM missing_table;
        """,
        encoding="utf-8",
    )
    raw = sqlite3.connect(":memory:")

    with pytest.raises(sqlite3.OperationalError):
        db.migrate(raw, migrations)

    names = {
        r[0]
        for r in raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "leaked_table" not in names
    assert db.schema_version(raw) == 0


def test_a1_clip_meta_ts_upgrade_seeds_every_field():
    # 0004 must preserve an existing coarse timestamp across all fields, so no
    # previously-rejected update becomes accepted after the upgrade.
    import sqlite3
    raw = sqlite3.connect(":memory:")
    for prefix in ("0001", "0002", "0003"):
        script = next(db.MIGRATIONS_DIR.glob(f"{prefix}_*.sql"))
        raw.executescript(script.read_text(encoding="utf-8"))
    raw.execute("INSERT INTO clip_meta_ts(content_hash, ts) VALUES (?,?)", ("h1", "2026-01-01T00:00:00Z"))
    raw.executescript(next(db.MIGRATIONS_DIR.glob("0004_*.sql")).read_text(encoding="utf-8"))
    got = dict(raw.execute("SELECT field, ts FROM clip_meta_ts WHERE content_hash='h1'").fetchall())
    assert got == {
        "pinned": "2026-01-01T00:00:00Z",
        "favorite": "2026-01-01T00:00:00Z",
        "deleted": "2026-01-01T00:00:00Z",
    }


def test_a2_save_clip_full_fields(conn):
    outcome = pipeline.ingest(
        conn,
        "Buy milk and eggs",
        source_device="desktop-main",
        source_app="notepad.exe",
        now_fn=lambda: FIXED_NOW,
        new_id_fn=lambda: FIXED_ID,
    )
    assert outcome.status == pipeline.STATUS_NEW
    clip = ClipsRepo(conn).get(FIXED_ID)
    assert clip is not None
    assert clip.content == "Buy milk and eggs"
    assert clip.content_hash == normalize.content_hash("Buy milk and eggs")
    assert clip.content_type == "text"
    assert clip.created_at == FIXED_NOW and clip.last_seen_at == FIXED_NOW
    assert clip.times_seen == 1
    assert clip.source_device == "desktop-main" and clip.source_app == "notepad.exe"
    assert not clip.is_secret and clip.secret_level is None and clip.secret_reasons == []


def test_a3_dedup_bumps_times_seen(conn):
    first = pipeline.ingest(conn, "same content", source_device="d")
    second = pipeline.ingest(
        conn, "same content", source_device="d", now_fn=lambda: "2026-06-13T00:00:00Z"
    )
    assert second.status == pipeline.STATUS_DUPLICATE
    assert second.clip.id == first.clip.id
    assert second.clip.times_seen == 2
    assert second.clip.last_seen_at == "2026-06-13T00:00:00Z"
    count = conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
    assert count == 1


def test_a3_crlf_variant_is_duplicate(conn):
    pipeline.ingest(conn, "line1\nline2", source_device="d")
    outcome = pipeline.ingest(conn, "line1\r\nline2", source_device="d")
    assert outcome.status == pipeline.STATUS_DUPLICATE


def test_a3_deleted_clip_not_resurrected(conn):
    first = pipeline.ingest(conn, "ephemeral note", source_device="d")
    conn.execute("UPDATE clips SET deleted = 1 WHERE id = ?", (first.clip.id,))
    conn.commit()
    outcome = pipeline.ingest(conn, "ephemeral note", source_device="d")
    assert outcome.status == pipeline.STATUS_DUPLICATE
    assert outcome.clip.deleted is True
    assert outcome.clip.times_seen == 2
