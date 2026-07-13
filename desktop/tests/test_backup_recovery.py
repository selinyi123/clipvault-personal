"""Recovery boundaries for unpublished backup history contamination."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from clipvault.backup import git_repo, github_backup
from clipvault.backup.github_backup import BackupWorker
from clipvault.core.models import SecretVerdict
from clipvault.pipeline import ingest as pipeline
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo


NOW = "2026-06-13T10:00:00Z"
DAILY_RELPATH = "clips/2026/06/2026-06-13.jsonl"
PUBLIC_A = "public alpha recovery candidate"
PUBLIC_B = "public beta survives recovery"
SAFE_CONTENT = "safe state cycle recovery record"
RECLASSIFIED_CONTENT = "later reclassified recovery record"


def _git(
    repo: Path,
    *args: str,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=text,
    )


def _repo_with_empty_remote(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "backup"
    remote = tmp_path / "remote.git"
    git_repo.init(repo)
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    _git(repo, "remote", "add", "origin", str(remote))
    return repo, remote


def _worker(
    conn,
    repo: Path,
    *,
    push_enabled: bool = False,
) -> BackupWorker:
    return BackupWorker(
        conn,
        str(repo),
        push_enabled=push_enabled,
        now_fn=lambda: NOW,
    )


def _ingest(conn, content: str):
    outcome = pipeline.ingest(
        conn,
        content,
        source_device="desktop",
        now_fn=lambda: NOW,
    )
    assert outcome.status == pipeline.STATUS_NEW
    return outcome.clip


def _set_deleted(conn, clip_id: str, deleted: bool) -> None:
    conn.execute(
        "UPDATE clips SET deleted=? WHERE id=?",
        (int(deleted), clip_id),
    )
    conn.commit()
    BackupQueueRepo(conn).reenqueue(clip_id, NOW)


def _managed_files(repo: Path) -> dict[str, bytes]:
    clips = repo / "clips"
    if not clips.exists():
        return {}
    return {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in sorted(clips.rglob("*.jsonl"))
    }


def _index(repo: Path) -> bytes:
    return _git(repo, "ls-files", "--stage", "-z", text=False).stdout


def _remote_head(remote: Path) -> str | None:
    result = _git(
        remote,
        "rev-parse",
        "--verify",
        "refs/heads/main^{commit}",
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _remote_blob_bytes(remote: Path) -> bytes:
    objects = _git(remote, "rev-list", "--objects", "--all").stdout.splitlines()
    blobs: list[bytes] = []
    for record in objects:
        object_id = record.split(" ", 1)[0]
        kind = _git(remote, "cat-file", "-t", object_id).stdout.strip()
        if kind == "blob":
            blobs.append(_git(remote, "cat-file", "blob", object_id, text=False).stdout)
    return b"\n".join(blobs)


def _remote_records(remote: Path) -> list[dict]:
    result = _git(
        remote,
        "show",
        f"refs/heads/main:{DAILY_RELPATH}",
    )
    return [json.loads(line) for line in result.stdout.splitlines()]


def _repo_snapshot(repo: Path, remote: Path) -> tuple:
    return (
        git_repo.head_commit(repo),
        _managed_files(repo),
        _index(repo),
        _remote_head(remote),
    )


def test_empty_remote_scrubs_reclassified_only_clip_then_accepts_new_public_clip(
    conn,
    tmp_path,
):
    repo, remote = _repo_with_empty_remote(tmp_path)
    clip_a = _ingest(conn, PUBLIC_A)
    worker = _worker(conn, repo)
    first = worker.run_once()
    assert first["committed"] is not None
    assert _remote_head(remote) is None

    conn.execute("UPDATE clips SET is_secret=1 WHERE id=?", (clip_a.id,))
    conn.commit()
    worker.push_enabled = True

    # With no published base, removing the only contaminated ID recovers the
    # managed branch to its unborn state rather than publishing an empty commit.
    assert worker._try_push(monotonic=100.0) is False
    assert git_repo.head_commit(repo) is None
    assert _managed_files(repo) == {}
    assert _index(repo) == b""
    assert _git(repo, "status", "--porcelain", "--untracked-files=all").stdout == ""
    assert _remote_head(remote) is None
    assert BackupQueueRepo(conn).state_of(clip_a.id) == "dropped_secret"
    assert ClipsRepo(conn).get(clip_a.id).backed_up_at is None

    clip_b = _ingest(conn, PUBLIC_B)
    result = worker.run_once(monotonic=200.0)

    assert result["pushed"] is True
    assert _remote_head(remote) == git_repo.head_commit(repo)
    records = _remote_records(remote)
    assert [record["id"] for record in records] == [clip_b.id]
    assert PUBLIC_A.encode() not in _remote_blob_bytes(remote)


def test_mixed_recovery_drops_all_secret_id_history_and_preserves_safe_a_b_a(
    conn,
    tmp_path,
):
    repo, remote = _repo_with_empty_remote(tmp_path)
    safe = _ingest(conn, SAFE_CONTENT)
    reclassified = _ingest(conn, RECLASSIFIED_CONTENT)
    worker = _worker(conn, repo)
    worker.run_once()

    _set_deleted(conn, safe.id, True)
    _set_deleted(conn, reclassified.id, True)
    worker.run_once()
    _set_deleted(conn, safe.id, False)
    _set_deleted(conn, reclassified.id, False)
    worker.run_once()

    conn.execute("UPDATE clips SET is_secret=1 WHERE id=?", (reclassified.id,))
    conn.commit()
    worker.push_enabled = True

    assert worker._try_push(monotonic=100.0) is True

    records = _remote_records(remote)
    safe_records = [record for record in records if record["id"] == safe.id]
    assert [record["deleted"] for record in safe_records] == [False, True, False]
    assert all(record["id"] != reclassified.id for record in records)
    assert RECLASSIFIED_CONTENT.encode() not in _remote_blob_bytes(remote)
    assert BackupQueueRepo(conn).state_of(reclassified.id) == "dropped_secret"
    assert ClipsRepo(conn).get(reclassified.id).backed_up_at is None
    local_records = [
        json.loads(line)
        for line in (repo / Path(DAILY_RELPATH)).read_text(encoding="utf-8").splitlines()
    ]
    assert local_records == safe_records


@pytest.mark.parametrize("drift", ["missing", "immutable_mismatch"])
def test_ordinary_unpublished_drift_fails_closed_without_mutation(
    conn,
    tmp_path,
    drift,
):
    repo, remote = _repo_with_empty_remote(tmp_path)
    clip = _ingest(conn, "ordinary recovery integrity record")
    worker = _worker(conn, repo)
    worker.run_once()
    before = _repo_snapshot(repo, remote)

    if drift == "missing":
        conn.execute("DELETE FROM clips WHERE id=?", (clip.id,))
    else:
        conn.execute(
            "UPDATE clips SET source_device='unexpected-device' WHERE id=?",
            (clip.id,),
        )
    conn.commit()

    with pytest.raises(git_repo.GitPushError):
        worker._authorize_with_safe_recovery()

    assert _repo_snapshot(repo, remote) == before


def test_published_reclassified_id_requires_owner_remediation_without_mutation(
    conn,
    tmp_path,
):
    repo, remote = _repo_with_empty_remote(tmp_path)
    clip = _ingest(conn, "published record requiring owner remediation")
    worker = _worker(conn, repo, push_enabled=True)
    published = worker.run_once(monotonic=100.0)
    assert published["pushed"] is True
    published_head = _remote_head(remote)
    assert published_head is not None

    # Create an unpublished suffix for the same ID, then quarantine it. The
    # already-published base cannot be sanitized by rewriting only local refs.
    worker.push_enabled = False
    _set_deleted(conn, clip.id, True)
    suffix = worker.run_once()
    assert suffix["committed"] is not None
    assert git_repo.head_commit(repo) != published_head
    conn.execute("UPDATE clips SET is_secret=1 WHERE id=?", (clip.id,))
    conn.commit()
    before = _repo_snapshot(repo, remote)

    with pytest.raises(git_repo.OwnerRemediationRequired):
        worker._authorize_with_safe_recovery()

    assert _repo_snapshot(repo, remote) == before
    assert _remote_head(remote) == published_head


def test_published_ancestry_rewrite_cannot_hide_reclassified_id(
    conn,
    tmp_path,
):
    repo, remote = _repo_with_empty_remote(tmp_path)
    exposed = _ingest(conn, "published ancestry exposure marker")
    retained = _ingest(conn, "published ancestry retained row")
    worker = _worker(conn, repo, push_enabled=True)
    assert worker.run_once(monotonic=100.0)["pushed"] is True

    path = repo / Path(DAILY_RELPATH)
    original_lines = path.read_text(encoding="utf-8").splitlines()
    exposed_line = next(
        line for line in original_lines if json.loads(line)["id"] == exposed.id
    )
    retained_line = next(
        line for line in original_lines if json.loads(line)["id"] == retained.id
    )

    # Model a manual already-published rewrite whose final tree no longer
    # contains the exposed ID. Its older blob is still part of remote history.
    path.write_text(retained_line + "\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "--", DAILY_RELPATH)
    _git(repo, "commit", "-m", "manual published rewrite")
    _git(repo, "push", "origin", "main")
    rewritten_base = _remote_head(remote)
    assert rewritten_base == git_repo.head_commit(repo)

    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(exposed_line + "\n")
    git_repo.add_commit(repo, "local suffix repeats exposed id", paths=[DAILY_RELPATH])
    conn.execute("UPDATE clips SET is_secret=1 WHERE id=?", (exposed.id,))
    conn.commit()
    before = _repo_snapshot(repo, remote)

    with pytest.raises(git_repo.OwnerRemediationRequired):
        worker._authorize_with_safe_recovery()

    assert _repo_snapshot(repo, remote) == before
    assert _remote_head(remote) == rewritten_base
    assert b"published ancestry exposure marker" in _remote_blob_bytes(remote)


def test_post_ref_index_sync_failure_is_repaired_before_secret_recovery(
    conn,
    tmp_path,
    monkeypatch,
):
    repo, remote = _repo_with_empty_remote(tmp_path)
    retained = _ingest(conn, "retained after index repair")
    quarantined = _ingest(conn, "quarantined after index repair")
    worker = _worker(conn, repo)
    real_sync = git_repo._sync_real_index
    failed = False

    def fail_first_index_sync(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise git_repo.GitError("simulated post-ref index sync failure")
        return real_sync(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(git_repo, "_sync_real_index", fail_first_index_sync)
        persisted = worker.run_once()

    assert failed and persisted["committed"] is not None
    assert _index(repo) == b""
    conn.execute("UPDATE clips SET is_secret=1 WHERE id=?", (quarantined.id,))
    conn.commit()
    worker.push_enabled = True

    recovered = worker.run_once(monotonic=100.0)

    assert recovered["pushed"] is True
    assert [record["id"] for record in _remote_records(remote)] == [retained.id]
    assert quarantined.content.encode() not in _remote_blob_bytes(remote)
    assert _git(repo, "status", "--porcelain").stdout == ""
    assert _index(repo) != b""


def test_secret_ack_is_invalidated_before_git_recovery_can_fail(
    conn,
    tmp_path,
    monkeypatch,
):
    repo, remote = _repo_with_empty_remote(tmp_path)
    clip = _ingest(conn, "ack invalidated before local recovery")
    worker = _worker(conn, repo)
    worker.run_once()
    contaminated_head = git_repo.head_commit(repo)
    assert contaminated_head is not None
    assert BackupQueueRepo(conn).state_of(clip.id) == "done"
    assert ClipsRepo(conn).get(clip.id).backed_up_at is not None
    conn.execute("UPDATE clips SET is_secret=1 WHERE id=?", (clip.id,))
    conn.commit()
    worker.push_enabled = True

    def fail_rebuild(*_args, **_kwargs):
        raise git_repo.GitPushError("simulated recovery interruption")

    with monkeypatch.context() as patch:
        patch.setattr(git_repo, "_rebuild_unpublished_candidate", fail_rebuild)
        assert worker._try_push(monotonic=100.0) is False

    assert git_repo.head_commit(repo) == contaminated_head
    assert _remote_head(remote) is None
    assert BackupQueueRepo(conn).state_of(clip.id) == "dropped_secret"
    assert ClipsRepo(conn).get(clip.id).backed_up_at is None

    # A later attempt can finish the Git scrub without resurrecting the stale
    # acknowledgement. The only local candidate becomes an unborn branch.
    assert worker._try_push(monotonic=300.0) is False
    assert git_repo.head_commit(repo) is None
    assert BackupQueueRepo(conn).state_of(clip.id) == "dropped_secret"
    assert ClipsRepo(conn).get(clip.id).backed_up_at is None


def test_owner_release_after_recovery_plan_abandons_stale_git_scrub(
    conn,
    tmp_path,
    monkeypatch,
):
    repo, remote = _repo_with_empty_remote(tmp_path)
    clip = _ingest(conn, "release wins the recovery planning race")
    worker = _worker(conn, repo)
    worker.run_once()
    original_head = git_repo.head_commit(repo)
    assert original_head is not None
    conn.execute("UPDATE clips SET is_secret=1 WHERE id=?", (clip.id,))
    conn.commit()
    worker.push_enabled = True

    real_invalidate = worker._invalidate_recovered_secret_acks
    released_at = "2026-06-13T10:01:00Z"

    def release_then_recheck(clip_ids):
        released = ClipsRepo(conn).release_secret(
            clip.id,
            released_at,
            commit=False,
        )
        assert released is not None
        BackupQueueRepo(conn).reenqueue(clip.id, released_at, commit=False)
        conn.commit()
        return real_invalidate(clip_ids)

    with monkeypatch.context() as patch:
        patch.setattr(
            worker,
            "_invalidate_recovered_secret_acks",
            release_then_recheck,
        )
        assert worker._try_push(monotonic=100.0) is False

    # The stale recovery plan must not remove a newly released clip. Its queue
    # intent remains pending and the next normal pass persists the release audit.
    assert git_repo.head_commit(repo) == original_head
    assert _remote_head(remote) is None
    assert BackupQueueRepo(conn).state_of(clip.id) == "pending"
    assert ClipsRepo(conn).get(clip.id).released is True

    completed = worker.run_once(monotonic=200.0)
    assert completed["pushed"] is True
    records = _remote_records(remote)
    assert records[-1]["id"] == clip.id
    assert records[-1]["released"] is True
    assert records[-1]["released_at"] == released_at


def test_missing_secret_record_removes_orphan_ack_and_allows_reconstruction(
    conn,
    tmp_path,
    monkeypatch,
):
    repo, remote = _repo_with_empty_remote(tmp_path)
    clip = _ingest(conn, "orphan acknowledgement must not survive Git scrub")
    worker = _worker(conn, repo)
    worker.run_once()
    assert git_repo.head_commit(repo) is not None
    assert BackupQueueRepo(conn).state_of(clip.id) == "done"

    # The schema intentionally has no queue FK/cascade. Model a missing fact row
    # plus a newer Secret Guard rule that taints its unpublished backup record.
    conn.execute("DELETE FROM clips WHERE id=?", (clip.id,))
    conn.commit()
    orphan = conn.execute(
        "SELECT state, done_at FROM backup_queue WHERE clip_id=?",
        (clip.id,),
    ).fetchone()
    assert tuple(orphan) == ("done", NOW)
    worker.push_enabled = True

    with monkeypatch.context() as patch:
        patch.setattr(
            github_backup.secret_guard,
            "scan",
            lambda _content: SecretVerdict(
                True,
                "hard",
                ["test-rule-upgrade"],
            ),
        )
        assert worker._try_push(monotonic=100.0) is False

    assert git_repo.head_commit(repo) is None
    assert _remote_head(remote) is None
    assert BackupQueueRepo(conn).state_of(clip.id) is None

    # An orphan queue tombstone must not permanently suppress an otherwise
    # valid public reconstruction of the same immutable clip ID.
    ClipsRepo(conn).insert(clip)
    assert BackupQueueRepo(conn).enqueue(clip.id, NOW) is True
    assert BackupQueueRepo(conn).state_of(clip.id) == "pending"
