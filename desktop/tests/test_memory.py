"""S007 gates E1-E9: Personal Memory repo, importers, promote, API."""

import logging

import pytest

from clipvault.api.handlers import Api
from clipvault.config import Config
from clipvault.memory import importers
from clipvault.service import ClipVaultService
from clipvault.store.memory_repo import MemoryRepo, SecretMemoryError
from clipvault.store.outbox_repo import OutboxRepo

FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture
def cfg(tmp_path):
    return Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", device_name="d",
        db_path=":memory:", max_clip_bytes=1_048_576, poll_ms=500,
        vault_path=str(tmp_path / "vault"),
    )


@pytest.fixture
def api(conn, cfg):
    return Api(ClipVaultService(conn, cfg))


@pytest.fixture
def repo(conn):
    return MemoryRepo(conn)


def test_e1_upsert_idempotent_no_usecount_regression(repo):
    a = repo.upsert("command", "git status", use_count=5)
    b = repo.upsert("command", "git status", use_count=0)  # lower count must not regress
    assert a.id == b.id
    assert b.use_count == 5
    assert len(repo.list(kind="command")) == 1


def test_e2_invalid_kind_rejected(repo):
    with pytest.raises(ValueError):
        repo.upsert("bogus", "x")


@pytest.mark.parametrize(
    ("text", "label"),
    [
        (FAKE_AWS_KEY, None),
        ("production credential", FAKE_AWS_KEY),
    ],
)
def test_e2_secret_memory_rejected_at_repo_boundary(repo, text, label):
    with pytest.raises(SecretMemoryError):
        repo.upsert("term", text, label=label)
    assert repo.list() == []


def test_e3_list_ordering_and_filter(repo):
    repo.upsert("term", "alpha", use_count=1)
    repo.upsert("term", "beta", use_count=10)
    pinned = repo.upsert("term", "gamma", use_count=0, pinned=True)
    repo.upsert("command", "git push")
    terms = repo.list(kind="term")
    assert terms[0].id == pinned.id            # pinned first
    assert [t.text for t in terms[1:]] == ["beta", "alpha"]  # then by use_count
    assert len(repo.list(query="git")) == 1    # q filter across kinds


def test_e4_bump_use(repo):
    m = repo.upsert("phrase", "hello there")
    repo.bump_use(m.id, "2026-06-13T10:00:00Z")
    again = repo.get(m.id)
    assert again.use_count == 1 and again.last_used_at == "2026-06-13T10:00:00Z"


def test_e5_soft_delete(repo):
    m = repo.upsert("term", "temp")
    assert repo.soft_delete(m.id) is True
    assert repo.list(kind="term") == []


def test_e6_obsidian_title_import_idempotent(repo, tmp_path):
    vault = tmp_path / "vault" / "01_Prompt"
    vault.mkdir(parents=True)
    (vault / "20260613-095022_My-Great-Prompt_ABC123.md").write_text("x", encoding="utf-8")
    (vault / "Plain-Note.md").write_text("x", encoding="utf-8")
    (vault / "20260613-095022_My-Great-Prompt_ABC123.md")  # same again ignored by fs
    items = importers.from_obsidian_titles(tmp_path / "vault")
    texts = {t for _, t in items}
    assert "My Great Prompt" in texts and "Plain Note" in texts
    created1 = importers.apply(repo, items, "obsidian_import")
    created2 = importers.apply(repo, items, "obsidian_import")  # idempotent
    assert created1 == len(items) and created2 == 0


def test_e6_import_does_not_resurrect_deleted(repo):
    # A re-run of an automated importer must not bring back a memory item the user
    # soft-deleted (idempotency + "deleted stays deleted").
    items = [("path", "project notes")]
    importers.apply(repo, items, "obsidian_import")
    repo.soft_delete(repo.by_kind_text("path", "project notes").id)
    assert importers.apply(repo, items, "obsidian_import") == 0   # not re-created
    assert repo.by_kind_text("path", "project notes").deleted is True
    assert all(m.text != "project notes" for m in repo.list())     # stays out of suggestions
    # but an explicit upsert (manual re-add / promote) still un-deletes:
    repo.upsert("path", "project notes", source="manual")
    assert repo.by_kind_text("path", "project notes").deleted is False


def test_e6_from_names_dedup(repo):
    items = importers.from_names(["repo-a", "repo-b", "repo-a", " "])
    assert sorted(t for _, t in items) == ["repo-a", "repo-b"]


def test_e6_import_skips_secret_memory_and_does_not_emit(repo):
    created = importers.apply(
        repo,
        [("term", "safe project name"), ("term", FAKE_AWS_KEY)],
        "github_import",
    )

    assert created == 1
    assert [item.text for item in repo.list()] == ["safe project name"]
    events = OutboxRepo(repo.conn).list_since(0)
    assert [event["payload"]["text"] for event in events] == ["safe project name"]


def test_e7_promote_command_clip(api, conn):
    _, obj = api.create_clip({"content": "docker compose up -d"})
    code, body = api.promote_clip(obj["clip"]["id"])
    assert code == 201
    assert body["memory"]["kind"] == "command"
    assert body["memory"]["text"] == "docker compose up -d"


def test_e7_promote_secret_refused(api):
    _, obj = api.create_clip({"content": FAKE_AWS_KEY})
    code, _ = api.promote_clip(obj["clip"]["id"])
    assert code == 404


def test_e7_promote_legacy_public_clip_rejected_by_current_secret_guard(api, conn):
    _, obj = api.create_clip({"content": "legacy public value"})
    clip_id = obj["clip"]["id"]
    conn.execute("UPDATE clips SET content=? WHERE id=?", (FAKE_AWS_KEY, clip_id))
    conn.commit()

    code, _ = api.promote_clip(clip_id)

    assert code == 404
    assert MemoryRepo(conn).list() == []
    assert all(event["kind"] != "memory_upsert" for event in OutboxRepo(conn).list_since(0))


def test_e8_memory_api_crud(api):
    code, created = api.create_memory({"kind": "prompt", "text": "You are a helpful bot"})
    assert code == 201
    mid = created["memory"]["id"]
    _, listed = api.list_memory({"kind": "prompt"})
    assert any(m["id"] == mid for m in listed["memory"])
    code, _ = api.delete_memory(mid)
    assert code == 200
    _, after = api.list_memory({})
    assert all(m["id"] != mid for m in after["memory"])


def test_e8_memory_api_bad_kind(api):
    code, _ = api.create_memory({"kind": "nope", "text": "x"})
    assert code == 400


@pytest.mark.parametrize(
    ("text", "label"),
    [(FAKE_AWS_KEY, None), ("production credential", FAKE_AWS_KEY)],
)
def test_e8_memory_api_rejects_secret_without_persisting_or_syncing(
    api, conn, text, label
):
    code, body = api.create_memory({"kind": "term", "text": text, "label": label})

    assert code == 422
    assert body["error"]["code"] == "secret_rejected"
    assert MemoryRepo(conn).list() == []
    assert OutboxRepo(conn).list_since(0) == []


def test_e8_memory_list_hides_legacy_secret_rows(repo):
    item = repo.upsert("term", "temporary safe value")
    repo.conn.execute(
        "UPDATE memory_items SET text=? WHERE id=?", (FAKE_AWS_KEY, item.id)
    )
    repo.conn.commit()

    assert repo.list() == []


def test_e8_legacy_secret_does_not_starve_later_safe_memory(repo):
    secret = repo.upsert("term", "temporary pinned value", pinned=True)
    repo.upsert("term", "safe fallback")
    repo.conn.execute(
        "UPDATE memory_items SET text=? WHERE id=?", (FAKE_AWS_KEY, secret.id)
    )
    repo.conn.commit()

    assert [item.text for item in repo.list(limit=1)] == ["safe fallback"]


def test_e8_upsert_cannot_revive_legacy_secret_label(repo):
    item = repo.upsert("term", "safe text", label="temporary label")
    repo.conn.execute(
        "UPDATE memory_items SET label=?, deleted=1 WHERE id=?",
        (FAKE_AWS_KEY, item.id),
    )
    repo.conn.commit()

    with pytest.raises(SecretMemoryError):
        repo.upsert("term", "safe text")
    assert repo.by_kind_text("term", "safe text").deleted is True


def test_e9_no_content_in_logs(api, caplog):
    with caplog.at_level(logging.INFO, logger="clipvault"):
        _, obj = api.create_clip({"content": "promote-me-secretphrase"})
        api.promote_clip(obj["clip"]["id"])
    assert "secretphrase" not in caplog.text
