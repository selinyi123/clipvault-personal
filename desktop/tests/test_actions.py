"""S011 gates G11-1..G11-6: Context Action rules + endpoint + kind-promote."""

import pytest

from clipvault.api.handlers import Api
from clipvault.config import Config
from clipvault.core import actions
from clipvault.service import ClipVaultService
from clipvault.store.memory_repo import MemoryRepo

FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture
def cfg(tmp_path):
    return Config(device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", device_name="d",
                  db_path=":memory:", max_clip_bytes=1_048_576, poll_ms=500,
                  vault_path=str(tmp_path / "vault"))


@pytest.fixture
def api(conn, cfg):
    return Api(ClipVaultService(conn, cfg))


@pytest.mark.parametrize("ctype,expect_kind", [
    ("command", "command"),
    ("prompt", "prompt"),
    ("url", "path"),
    ("path", "path"),
    ("code", "phrase"),
    ("error_log", "phrase"),
    ("text", "phrase"),
])
def test_g11_1_rules_per_type_end_with_copy(ctype, expect_kind):
    chips = actions.recommend(ctype, is_secret=False)
    assert chips[0].action == "promote" and chips[0].kind == expect_kind
    assert chips[-1].action == "copy"


def test_g11_2_secret_only_release():
    chips = actions.recommend("text", is_secret=True)
    assert [c.action for c in chips] == ["release"]


def test_g11_3_promote_kind_override(api, conn):
    _, obj = api.create_clip({"content": "just some plain words"})  # -> text
    cid = obj["clip"]["id"]
    code, body = api.promote_clip(cid, {"kind": "command"})
    assert code == 201 and body["memory"]["kind"] == "command"


def test_g11_3_promote_bad_kind_rejected(api):
    _, obj = api.create_clip({"content": "plain words two"})
    code, _ = api.promote_clip(obj["clip"]["id"], {"kind": "bogus"})
    assert code == 400


def test_g11_3_promote_default_when_no_kind(api):
    _, obj = api.create_clip({"content": "docker compose up"})  # -> command
    code, body = api.promote_clip(obj["clip"]["id"], {})
    assert code == 201 and body["memory"]["kind"] == "command"


def test_g11_4_actions_endpoint_public(api):
    _, obj = api.create_clip({"content": "git status"})  # command
    code, body = api.clip_actions(obj["clip"]["id"])
    assert code == 200
    acts = body["actions"]
    assert acts[0]["action"] == "promote" and acts[0]["kind"] == "command"
    assert any(a["action"] == "copy" for a in acts)


def test_g11_4_actions_endpoint_secret(api):
    _, obj = api.create_clip({"content": FAKE_AWS_KEY})
    code, body = api.clip_actions(obj["clip"]["id"])
    assert code == 200
    assert [a["action"] for a in body["actions"]] == ["release"]


def test_g11_4_actions_missing_404(api):
    code, _ = api.clip_actions("01NOPE0000000000000000000")
    assert code == 404
