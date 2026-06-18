"""A1 (migration), A2 (full save), A3 (dedup, no resurrection)."""

from clipvault.core import normalize
from clipvault.pipeline import ingest as pipeline
from clipvault.store import db
from clipvault.store.clips_repo import ClipsRepo

FIXED_NOW = "2026-06-12T08:30:00Z"
FIXED_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

EXPECTED_TABLES = {
    "schema_meta", "clips", "memory_items", "sync_outbox", "sync_peers", "backup_queue",
}


def test_a1_migration_from_zero(conn):
    assert db.schema_version(conn) == 3  # 0001_init + 0002_clip_meta_ts + 0003_memory_meta_ts
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
    assert db.migrate(conn) == 3  # second run is a no-op, returns current version


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
