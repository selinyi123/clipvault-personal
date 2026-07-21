"""Explicit Owner release remains a first-class public backup transition."""

import json
import subprocess

from clipvault.backup import git_repo, jsonl_store
from clipvault.backup.github_backup import BackupWorker
from clipvault.config import Config
from clipvault.core import normalize, secret_guard
from clipvault.pipeline import ingest as pipeline
from clipvault.service import ClipVaultService
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo


NOW = "2026-06-13T10:00:00Z"
FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


def _repo(tmp_path, *, remote=False):
    repo = tmp_path / "backup"
    bare = tmp_path / "remote.git"
    git_repo.init(repo)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    if remote:
        subprocess.run(
            ["git", "init", "--bare", "-b", "main", str(bare)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "origin", str(bare)],
            check=True,
        )
    return repo, bare


def _service(conn, tmp_path):
    cfg = Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        device_name="test-desktop",
        db_path=":memory:",
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
    )
    return ClipVaultService(conn, cfg)


def test_owner_release_reactivates_dropped_intent_and_can_push(conn, tmp_path):
    service = _service(conn, tmp_path)
    outcome = service.handle_clipboard_text(FAKE_AWS_KEY)
    clip_id = outcome.clip.id
    assert outcome.clip.is_secret

    # Model a legacy Gate-C row that was dropped before the Owner released it.
    conn.execute(
        "INSERT INTO backup_queue(clip_id, state, created_at) "
        "VALUES (?, 'dropped_secret', ?)",
        (clip_id, NOW),
    )
    conn.commit()

    assert service.release_clip(clip_id) is True
    released = ClipsRepo(conn).get(clip_id)
    assert released.released is True and released.is_secret is False
    assert BackupQueueRepo(conn).state_of(clip_id) == "pending"

    repo, remote = _repo(tmp_path, remote=True)
    worker = BackupWorker(
        conn,
        str(repo),
        push_enabled=True,
        now_fn=lambda: NOW,
    )
    result = worker.run_once(monotonic=100.0)

    assert result["written"] == 1 and result["pushed"] is True
    assert BackupQueueRepo(conn).state_of(clip_id) == "done"
    relpath = jsonl_store.daily_relpath(released.created_at)
    raw = subprocess.run(
        [
            "git",
            "-C",
            str(remote),
            "show",
            f"refs/heads/main:{relpath}",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    record = json.loads(raw)
    assert record["id"] == clip_id
    assert record["content"] == FAKE_AWS_KEY
    assert record["is_secret"] is False
    assert record["released"] is True
    assert record["released_at"] == released.released_at
    roundtrip = jsonl_store.deserialize_clip(raw)
    assert roundtrip.released is True
    assert roundtrip.released_at == released.released_at


def test_owner_release_never_exempts_secret_origin_metadata(conn, tmp_path):
    service = _service(conn, tmp_path)
    outcome = service.handle_clipboard_text(FAKE_AWS_KEY)
    clip_id = outcome.clip.id
    origin_marker = "password=OriginMetadataSecret123"
    conn.execute("UPDATE clips SET source_app=? WHERE id=?", (origin_marker, clip_id))
    conn.commit()

    assert service.release_clip(clip_id) is True
    released = ClipsRepo(conn).get(clip_id)
    assert released.released is True and released.is_secret is False
    assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0
    assert conn.execute(
        "SELECT state FROM obsidian_queue WHERE clip_id=?", (clip_id,)
    ).fetchone()[0] == "blocked_origin_metadata"
    assert not (tmp_path / "vault").exists()

    repo, _ = _repo(tmp_path)
    result = BackupWorker(conn, str(repo), push_enabled=False, now_fn=lambda: NOW).run_once()

    assert result["written"] == 0 and result["dropped"] == 1
    assert BackupQueueRepo(conn).state_of(clip_id) == "dropped_secret"
    assert not (repo / "clips").exists()


def test_release_between_gate_scan_and_drop_is_serialized_as_public(
    conn,
    tmp_path,
    monkeypatch,
):
    outcome = pipeline.ingest(
        conn,
        "temporary public queue row",
        source_device="d",
        now_fn=lambda: NOW,
    )
    clip_id = outcome.clip.id
    conn.execute(
        "UPDATE clips SET content=?, content_hash=?, is_secret=0, released=0 "
        "WHERE id=?",
        (FAKE_AWS_KEY, "legacy-hash", clip_id),
    )
    conn.commit()

    real_scan = secret_guard.scan
    released = False

    def release_during_first_scan(content):
        nonlocal released
        verdict = real_scan(content)
        if content == FAKE_AWS_KEY and not released:
            released = True
            conn.execute(
                "UPDATE clips SET is_secret=0, released=1, released_at=? "
                "WHERE id=?",
                (NOW, clip_id),
            )
            BackupQueueRepo(conn).reenqueue(clip_id, NOW, commit=False)
            conn.commit()
        return verdict

    monkeypatch.setattr(secret_guard, "scan", release_during_first_scan)
    repo, _ = _repo(tmp_path)
    result = BackupWorker(
        conn,
        str(repo),
        push_enabled=False,
        now_fn=lambda: NOW,
    ).run_once()

    assert released
    assert result["written"] == 1 and result["dropped"] == 0
    assert BackupQueueRepo(conn).state_of(clip_id) == "done"


def test_mark_dropped_cannot_overwrite_non_pending_state(conn):
    outcome = pipeline.ingest(conn, "conditional drop row", source_device="d")
    queue = BackupQueueRepo(conn)
    assert queue.mark_done(outcome.clip.id, NOW)

    assert queue.mark_dropped(outcome.clip.id, "stale_gate_c") is False
    assert queue.state_of(outcome.clip.id) == "done"


def test_unpublished_legacy_public_then_owner_release_can_push(
    conn,
    tmp_path,
    monkeypatch,
):
    outcome = pipeline.ingest(
        conn,
        "legacy detector treated this as public",
        source_device="d",
        now_fn=lambda: NOW,
    )
    clip_id = outcome.clip.id
    conn.execute(
        "UPDATE clips SET content=?, content_hash=?, is_secret=0, released=0, "
        "released_at=NULL WHERE id=?",
        (FAKE_AWS_KEY, normalize.content_hash(FAKE_AWS_KEY), clip_id),
    )
    conn.commit()
    repo, remote = _repo(tmp_path, remote=True)
    worker = BackupWorker(
        conn,
        str(repo),
        push_enabled=False,
        now_fn=lambda: NOW,
    )
    real_scan = secret_guard.scan

    def legacy_scan(content):
        if content == FAKE_AWS_KEY:
            return real_scan("ordinary public text")
        return real_scan(content)

    with monkeypatch.context() as patch:
        patch.setattr(secret_guard, "scan", legacy_scan)
        assert worker.run_once()["committed"] is not None

    assert _service(conn, tmp_path).release_clip(clip_id) is True
    worker.push_enabled = True
    assert worker.run_once(monotonic=100.0)["pushed"] is True

    released = ClipsRepo(conn).get(clip_id)
    relpath = jsonl_store.daily_relpath(released.created_at)
    raw_lines = subprocess.run(
        ["git", "-C", str(remote), "show", f"refs/heads/main:{relpath}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    records = [json.loads(line) for line in raw_lines]
    assert len(records) == 2
    assert "released" not in records[0] and "released_at" not in records[0]
    assert records[1]["released"] is True
    assert records[1]["released_at"] == released.released_at
