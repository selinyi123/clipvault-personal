"""S007 gates E1-E9: Personal Memory repo, importers, promote, API."""

import logging

import pytest

from clipvault.api.handlers import Api
from clipvault.config import Config
from clipvault.memory import importers
from clipvault.service import ClipVaultService
from clipvault.store.memory_repo import MemoryRepo

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


def test_e6_from_names_dedup(repo):
    items = importers.from_names(["repo-a", "repo-b", "repo-a", " "])
    assert sorted(t for _, t in items) == ["repo-a", "repo-b"]


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


def test_e9_no_content_in_logs(api, caplog):
    with caplog.at_level(logging.INFO, logger="clipvault"):
        _, obj = api.create_clip({"content": "promote-me-secretphrase"})
        api.promote_clip(obj["clip"]["id"])
    assert "secretphrase" not in caplog.text
