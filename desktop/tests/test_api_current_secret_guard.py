"""Current Secret Guard rules must govern every Desktop API clip boundary."""

from datetime import datetime, timezone
import json

import pytest

from clipvault.api import handlers
from clipvault.api.handlers import Api
from clipvault.config import Config
from clipvault.core import normalize
from clipvault.core.models import Clip
from clipvault.service import ClipVaultService
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.outbox_repo import OutboxRepo


CURRENT_SECRET = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture
def api(conn, tmp_path):
    cfg = Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        device_name="desktop-test",
        db_path=":memory:",
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
    )
    return Api(ClipVaultService(conn, cfg))


def _legacy_public(
    conn,
    *,
    clip_id: str,
    content: str = CURRENT_SECRET,
    created_at: str = "2026-07-21T00:00:00Z",
    last_seen_at: str | None = None,
    pinned: bool = False,
    favorite: bool = False,
    times_seen: int = 1,
    released: bool = False,
    source_app: str | None = None,
    obsidian_path: str | None = None,
    deleted: bool = False,
) -> Clip:
    clip = Clip(
        id=clip_id,
        content=content,
        content_hash=normalize.content_hash(content),
        content_type="text",
        source_device="legacy-desktop",
        source_app=source_app,
        created_at=created_at,
        last_seen_at=last_seen_at or created_at,
        pinned=pinned,
        favorite=favorite,
        times_seen=times_seen,
        released=released,
        released_at=created_at if released else None,
        obsidian_path=obsidian_path,
        deleted=deleted,
    )
    ClipsRepo(conn).insert(clip)
    return clip


@pytest.mark.parametrize("params", [{}, {"q": "AK"}, {"q": "AKIA"}])
def test_public_list_quarantines_current_secret_for_plain_like_and_fts(
    api, conn, params
):
    clip = _legacy_public(
        conn,
        clip_id="01APICURRENTSECRETLIST001",
    )
    assert ClipsRepo(conn).fts_contains(clip.id)

    status, body = api.list_clips(params)

    assert status == 200
    assert body["clips"] == []
    assert CURRENT_SECRET not in json.dumps(body, ensure_ascii=False)
    stored = ClipsRepo(conn).get(clip.id)
    assert stored is not None and stored.is_secret is True
    assert stored.secret_reasons
    assert not ClipsRepo(conn).fts_contains(clip.id)


def test_public_list_refills_at_most_once_after_limit_one_quarantine(
    api, conn, monkeypatch
):
    safe = _legacy_public(
        conn,
        clip_id="01APISAFEREFILL0000000001",
        content="ordinary refill result",
        created_at="2026-07-21T00:00:00Z",
    )
    secret = _legacy_public(
        conn,
        clip_id="02APISECRETREFILL00000001",
        pinned=True,
        created_at="2026-07-21T00:00:01Z",
    )
    calls = []
    real_list = api.clips.list_clips

    def counted_list(**kwargs):
        calls.append(kwargs.copy())
        return real_list(**kwargs)

    monkeypatch.setattr(api.clips, "list_clips", counted_list)

    status, body = api.list_clips({"limit": "1"})

    assert status == 200
    assert [clip["id"] for clip in body["clips"]] == [safe.id]
    assert len(calls) == 2
    assert calls[1]["before_id"] is None
    assert calls[1]["limit"] == 1
    assert ClipsRepo(conn).get(secret.id).is_secret is True


def test_suggest_scans_full_content_before_two_hundred_character_preview(api, conn):
    content = ("ordinary-prefix-" * 16) + CURRENT_SECRET
    assert content.index(CURRENT_SECRET) > 200
    clip = _legacy_public(
        conn,
        clip_id="01APILATESECRETSUGGEST001",
        content=content,
        last_seen_at="2099-01-01T00:00:00Z",
        favorite=True,
    )

    status, body = api.suggest({"prefix": "", "limit": "10"})

    assert status == 200
    assert body["suggestions"] == []
    assert content[:200] not in json.dumps(body, ensure_ascii=False)
    assert ClipsRepo(conn).get(clip.id).is_secret is True
    assert not ClipsRepo(conn).fts_contains(clip.id)


def test_duplicate_post_increments_seen_then_quarantines_without_downstream(api, conn):
    clip = _legacy_public(
        conn,
        clip_id="01APIDUPLICATESECRET000001",
        obsidian_path=rf"C:\Vault\{CURRENT_SECRET}.md",
    )
    assert OutboxRepo(conn).max_seq() == 0

    status, body = api.create_clip({"content": CURRENT_SECRET})

    assert status == 201
    assert body["status"] == "duplicate"
    assert body["clip"]["id"] == clip.id
    assert body["clip"]["is_secret"] is True
    assert body["clip"]["length"] is None
    assert body["clip"]["obsidian_path"] is None
    assert CURRENT_SECRET not in json.dumps(body, ensure_ascii=False)
    stored = ClipsRepo(conn).get(clip.id)
    assert stored is not None and stored.is_secret is True
    assert stored.times_seen == 2
    assert OutboxRepo(conn).max_seq() == 0
    assert conn.execute("SELECT COUNT(*) FROM backup_queue").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM obsidian_queue").fetchone()[0] == 0
    assert not ClipsRepo(conn).fts_contains(clip.id)


def test_duplicate_seen_and_quarantine_roll_back_together(api, conn, monkeypatch):
    clip = _legacy_public(
        conn,
        clip_id="01APIDUPLICATEROLLBACK0001",
    )

    def fail_unindex(self, _clip_id, **_kwargs):
        raise RuntimeError("synthetic duplicate FTS failure")

    monkeypatch.setattr(ClipsRepo, "_unindex_clip", fail_unindex)

    with pytest.raises(RuntimeError, match="synthetic duplicate FTS failure"):
        api.create_clip({"content": CURRENT_SECRET})

    stored = ClipsRepo(conn).get(clip.id)
    assert stored is not None and stored.is_secret is False
    assert stored.times_seen == 1
    assert ClipsRepo(conn).fts_contains(clip.id)
    assert OutboxRepo(conn).max_seq() == 0
    assert conn.in_transaction is False


def test_patch_persists_current_secret_and_keeps_flags_local(api, conn):
    clip = _legacy_public(
        conn,
        clip_id="01APIPATCHSECRET000000001",
    )

    status, body = api.patch_clip(clip.id, {"pinned": True})

    assert status == 200 and body["applied"] == {"pinned": True}
    stored = ClipsRepo(conn).get(clip.id)
    assert stored is not None and stored.is_secret is True and stored.pinned is True
    assert not ClipsRepo(conn).fts_contains(clip.id)
    assert OutboxRepo(conn).max_seq() == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM clip_meta_ts WHERE content_hash=?",
        (clip.content_hash,),
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("field", "value", "initial_deleted"),
    [
        ("pinned", True, False),
        ("favorite", True, False),
        ("deleted", True, False),
        ("deleted", False, True),
    ],
)
def test_patch_new_quarantine_does_not_repeat_unindexed_fts_delete(
    api, conn, field, value, initial_deleted
):
    clip = _legacy_public(
        conn,
        clip_id="01APIPATCHSQLSHAPE0000001",
        deleted=initial_deleted,
    )
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        status, _ = api.patch_clip(clip.id, {field: value})
    finally:
        conn.set_trace_callback(None)

    assert status == 200
    assert not any(
        "DELETE FROM CLIPS_FTS WHERE ID =" in statement.upper()
        for statement in statements
    )


def test_patch_with_unsafe_origin_stays_local_and_skips_backup(api, conn):
    clip = _legacy_public(
        conn,
        clip_id="01APIUNSAFEORIGINPATCH00001",
        content="ordinary content with unsafe legacy origin",
        source_app=CURRENT_SECRET,
    )

    status, body = api.patch_clip(clip.id, {"deleted": True})

    assert status == 200 and body["applied"] == {"deleted": True}
    stored = ClipsRepo(conn).get(clip.id)
    assert stored is not None and stored.deleted is True
    assert stored.is_secret is False
    assert OutboxRepo(conn).max_seq() == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM clip_meta_ts WHERE content_hash=?",
        (clip.content_hash,),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM backup_queue WHERE clip_id=?",
        (clip.id,),
    ).fetchone()[0] == 0


def test_actions_reclassify_legacy_secret_and_offer_only_release(api, conn):
    clip = _legacy_public(
        conn,
        clip_id="01APIACTIONSSECRET0000001",
    )

    status, body = api.clip_actions(clip.id)

    assert status == 200
    assert [action["action"] for action in body["actions"]] == ["release"]
    assert ClipsRepo(conn).get(clip.id).is_secret is True
    assert not ClipsRepo(conn).fts_contains(clip.id)


def test_owner_release_is_the_only_current_content_scan_exception(api, conn):
    clip = _legacy_public(
        conn,
        clip_id="01APIRELEASEDSECRET0000001",
        favorite=True,
        times_seen=4,
        released=True,
        last_seen_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    status, body = api.list_clips({"q": "AKIA"})
    actions_status, actions = api.clip_actions(clip.id)
    promote_status, _ = api.promote_clip(clip.id)

    assert status == actions_status == 200
    assert body["clips"][0]["content"] == CURRENT_SECRET
    assert [action["action"] for action in actions["actions"]] == ["copy"]
    assert promote_status == 404
    stored = ClipsRepo(conn).get(clip.id)
    assert stored is not None and stored.released is True
    assert stored.is_secret is False
    assert ClipsRepo(conn).fts_contains(clip.id)


def test_suggest_does_not_expose_unsafe_origin_through_score_oracle(api, conn):
    clip = _legacy_public(
        conn,
        clip_id="01APIORIGINSCORE000000001",
        content="ordinary origin score candidate",
        favorite=True,
        times_seen=4,
        source_app=CURRENT_SECRET,
        last_seen_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    _, matched = api.suggest({"prefix": "", "app": CURRENT_SECRET, "limit": "10"})
    _, other = api.suggest({"prefix": "", "app": "different-app", "limit": "10"})

    matched_row = next(item for item in matched["suggestions"] if item["id"] == clip.id)
    other_row = next(item for item in other["suggestions"] if item["id"] == clip.id)
    assert matched_row["score"] == other_row["score"]


def test_serializer_auto_redacts_current_or_persisted_secret():
    current = Clip(
        id="01APISERIALIZERCURRENT00001",
        content=CURRENT_SECRET,
        content_hash=normalize.content_hash(CURRENT_SECRET),
        content_type="text",
        source_device="legacy-desktop",
        created_at="2026-07-21T00:00:00Z",
        last_seen_at="2026-07-21T00:00:00Z",
        obsidian_path=rf"C:\Vault\{CURRENT_SECRET}.md",
    )
    persisted = Clip(
        **{
            **current.__dict__,
            "id": "01APISERIALIZERSECRET00001",
            "is_secret": True,
            "secret_level": "hard",
            "secret_reasons": ["LEGACY"],
            "released": True,
            "released_at": "2026-07-21T00:00:01Z",
        }
    )
    released = Clip(
        **{
            **current.__dict__,
            "id": "01APISERIALIZERRELEASE001",
            "released": True,
            "released_at": "2026-07-21T00:00:01Z",
        }
    )

    current_redacted = handlers._clip_dict(current, redact=False)
    persisted_redacted = handlers._clip_dict(persisted, redact=False)
    for redacted in (current_redacted, persisted_redacted):
        assert redacted["is_secret"] is True
        assert redacted["length"] is None
        assert redacted["secret_level"] is not None
        assert redacted["secret_reasons"]
        assert redacted["obsidian_path"] is None
        assert CURRENT_SECRET not in redacted["content"]
    assert handlers._clip_dict(released, redact=False)["content"] == CURRENT_SECRET


def test_secret_list_hides_legacy_obsidian_path_after_current_quarantine(api, conn):
    clip = _legacy_public(
        conn,
        clip_id="01APIPATHSECRET0000000001",
        obsidian_path=rf"C:\Vault\{CURRENT_SECRET}.md",
    )

    assert api.list_clips({}) == (200, {"clips": []})
    status, body = api.list_clips({"secret": "1"})

    assert status == 200
    row = next(item for item in body["clips"] if item["id"] == clip.id)
    assert row["obsidian_path"] is None
    assert CURRENT_SECRET not in json.dumps(body, ensure_ascii=False)


def test_quarantine_and_fts_removal_roll_back_before_public_response(
    api, conn, monkeypatch
):
    clip = _legacy_public(
        conn,
        clip_id="01APIROLLBACKSECRET0000001",
    )

    def fail_unindex(_clip_id, **_kwargs):
        raise RuntimeError("synthetic FTS failure")

    monkeypatch.setattr(api.clips, "_unindex_clip", fail_unindex)

    with pytest.raises(RuntimeError, match="synthetic FTS failure"):
        api.list_clips({})

    stored = ClipsRepo(conn).get(clip.id)
    assert stored is not None and stored.is_secret is False
    assert ClipsRepo(conn).fts_contains(clip.id)
    assert conn.in_transaction is False
