"""Secret Guard must cover clip origin metadata at every local/export boundary."""

import json
import logging

import pytest

from clipvault import config as config_mod
from clipvault.api.handlers import Api
from clipvault.config import Config
from clipvault.core import normalize, origin_metadata
from clipvault.core.models import Clip
from clipvault.obsidian import writer
from clipvault.pipeline import ingest as pipeline
from clipvault.service import ClipVaultService
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.obsidian_queue_repo import ObsidianQueueRepo
from clipvault.store.outbox_repo import OutboxRepo
from clipvault.store.peers_repo import PeersRepo
from clipvault.sync import engine as sync_engine


NOW = "2026-07-21T00:00:00Z"
ORIGIN_SECRET = "AKIAIOSFODNN7EXAMPLE"


def _cfg(tmp_path) -> Config:
    return Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        device_name="desktop-test",
        db_path=":memory:",
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
    )


def _legacy_clip(*, source_device="desktop-test", source_app=ORIGIN_SECRET, **kw):
    content = kw.pop("content", "ordinary legacy content")
    return Clip(
        id=kw.pop("id", "01ORIGINMETADATALEGACY0001"),
        content=content,
        content_hash=normalize.content_hash(content),
        content_type="text",
        source_device=source_device,
        source_app=source_app,
        created_at=NOW,
        last_seen_at=NOW,
        **kw,
    )


@pytest.mark.parametrize(
    "value",
    [ORIGIN_SECRET, "bad\napp", "x" * 1025, 7, ["notepad.exe"]],
)
def test_api_rejects_unsafe_source_app_without_side_effects(conn, tmp_path, value):
    api = Api(ClipVaultService(conn, _cfg(tmp_path)))

    status, body = api.create_clip(
        {"content": "ordinary API content", "source_app": value}
    )

    assert status == 400
    assert body["error"]["code"] == "bad_request"
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM backup_queue").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM obsidian_queue").fetchone()[0] == 0
    assert ORIGIN_SECRET not in json.dumps(body, ensure_ascii=False)


def test_api_serialization_redacts_legacy_public_and_secret_origin(conn, tmp_path):
    public = _legacy_clip(id="01ORIGINMETADATAPUBLIC0001")
    secret = _legacy_clip(
        id="01ORIGINMETADATASECRET0001",
        content="ordinary quarantined legacy content",
        is_secret=True,
        secret_level="suspect",
        secret_reasons=["LEGACY"],
    )
    clips = ClipsRepo(conn)
    clips.insert(public)
    clips.insert(secret)
    api = Api(ClipVaultService(conn, _cfg(tmp_path)))

    public_status, public_body = api.list_clips({})
    secret_status, secret_body = api.list_clips({"secret": "1"})

    assert public_status == secret_status == 200
    assert public_body["clips"][0]["source_app"] is None
    assert secret_body["clips"][0]["source_app"] is None
    assert "source_app" in public_body["clips"][0]
    assert "source_app" in secret_body["clips"][0]
    responses = json.dumps(
        {"public": public_body, "secret": secret_body}, ensure_ascii=False
    )
    assert ORIGIN_SECRET not in responses


def test_local_capture_drops_unsafe_optional_origin_and_never_logs_it(
    conn, tmp_path, caplog
):
    service = ClipVaultService(conn, _cfg(tmp_path))

    with caplog.at_level(logging.DEBUG):
        outcome = service.handle_clipboard_text(
            "ordinary local clipboard content", ORIGIN_SECRET
        )

    stored = ClipsRepo(conn).get(outcome.clip.id)
    assert stored.source_app is None
    events = OutboxRepo(conn).list_since(0)
    assert len(events) == 1 and events[0]["payload"]["source_app"] is None
    markdown = next((tmp_path / "vault").rglob("*.md")).read_text(encoding="utf-8")
    assert ORIGIN_SECRET not in markdown
    assert ORIGIN_SECRET not in caplog.text


def test_config_fails_fast_for_secret_shaped_device_name(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "\n".join(
            (
                "[device]",
                'device_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"',
                f'device_name = "{ORIGIN_SECRET}"',
                "[obsidian]",
                f'vault_path = "{tmp_path.as_posix()}"',
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(config_mod.ConfigError) as exc:
        config_mod.load(path)

    assert exc.value.field == "device.device_name"
    assert ORIGIN_SECRET not in str(exc.value)


def test_config_fails_fast_for_whitespace_only_device_name(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "\n".join(
            (
                "[device]",
                'device_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"',
                'device_name = "   "',
                "[obsidian]",
                f'vault_path = "{tmp_path.as_posix()}"',
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(config_mod.ConfigError) as exc:
        config_mod.load(path)

    assert exc.value.field == "device.device_name"


@pytest.mark.parametrize(
    ("source_device", "source_app"),
    ((ORIGIN_SECRET, None), ("desktop-test", ORIGIN_SECRET)),
)
def test_direct_ingest_refuses_unsafe_origin_metadata(
    conn, source_device, source_app
):
    with pytest.raises(ValueError, match="unsafe clip origin metadata"):
        pipeline.ingest(
            conn,
            "ordinary direct ingest content",
            source_device=source_device,
            source_app=source_app,
        )

    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("source_device", "source_app"),
    ((ORIGIN_SECRET, None), ("desktop-test", ORIGIN_SECRET)),
)
def test_unsafe_duplicate_origin_fails_before_any_durable_mutation(
    conn, source_device, source_app
):
    content = "ordinary duplicate origin content"
    created = pipeline.ingest(
        conn,
        content,
        source_device="desktop-test",
        source_app="notepad.exe",
        now_fn=lambda: NOW,
    )
    assert created.status == pipeline.STATUS_NEW

    def durable_snapshot():
        tables = ("clips", "backup_queue", "obsidian_queue", "sync_outbox")
        return {
            table: [
                tuple(row)
                for row in conn.execute(
                    f"SELECT * FROM {table} ORDER BY 1"  # noqa: S608 - fixed names
                ).fetchall()
            ]
            for table in tables
        }

    before = durable_snapshot()
    with pytest.raises(ValueError, match="^unsafe clip origin metadata$"):
        pipeline.ingest(
            conn,
            content,
            source_device=source_device,
            source_app=source_app,
            now_fn=lambda: "2026-07-21T01:00:00Z",
        )

    assert durable_snapshot() == before
    row = ClipsRepo(conn).get(created.clip.id)
    assert row.times_seen == 1
    assert row.last_seen_at == NOW


@pytest.mark.parametrize(
    ("source_device", "source_app"),
    ((ORIGIN_SECRET, None), ("android-peer", ORIGIN_SECRET)),
)
def test_remote_clip_with_unsafe_origin_is_acked_noop(
    conn, tmp_path, source_device, source_app, caplog
):
    service = ClipVaultService(conn, _cfg(tmp_path))
    content = "ordinary remote clip content"
    data = {
        "id": "01REMOTEORIGINMETADATA00001",
        "content": content,
        "content_hash": normalize.content_hash(content),
        "content_type": "text",
        "is_secret": False,
        "secret_level": None,
        "secret_reasons": [],
        "source_device": source_device,
        "source_app": source_app,
        "created_at": NOW,
        "last_seen_at": NOW,
        "times_seen": 1,
        "pinned": False,
        "favorite": False,
        "deleted": False,
    }
    event = {
        "origin_device": "android-peer",
        "seq": 1,
        "kind": "clip_new",
        "ts": NOW,
        "data": data,
    }

    with caplog.at_level(logging.ERROR, logger="clipvault.sync"):
        acked = sync_engine.apply_push(conn, "android-peer", [event], service)

    assert acked == 1
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM backup_queue").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM obsidian_queue").fetchone()[0] == 0
    assert ORIGIN_SECRET not in caplog.text


def test_remote_unsafe_then_legacy_whitespace_origin_advances_durable_cursor(
    conn, tmp_path, caplog
):
    peer_id = "android-peer"
    PeersRepo(conn).upsert_pair(peer_id, "Pixel", "test-token-hash", NOW)
    service = ClipVaultService(conn, _cfg(tmp_path), obsidian_notify=lambda: None)

    def clip_event(seq, content, *, source_device, source_app, clip_id):
        return {
            "origin_device": peer_id,
            "seq": seq,
            "kind": "clip_new",
            "ts": NOW,
            "data": {
                "id": clip_id,
                "content": content,
                "content_hash": normalize.content_hash(content),
                "content_type": "text",
                "is_secret": False,
                "secret_level": None,
                "secret_reasons": [],
                "source_device": source_device,
                "source_app": source_app,
                "created_at": NOW,
                "last_seen_at": NOW,
                "times_seen": 1,
                "pinned": False,
                "favorite": False,
                "deleted": False,
            },
        }

    events = [
        clip_event(
            1,
            "unsafe remote origin content",
            source_device=peer_id,
            source_app=ORIGIN_SECRET,
            clip_id="01REMOTEUNSAFEORIGIN0000001",
        ),
        clip_event(
            2,
            "safe remote legacy peer content",
            source_device="   ",
            source_app=None,
            clip_id="01REMOTELEGACYORIGIN0000002",
        ),
    ]

    with caplog.at_level(logging.ERROR, logger="clipvault.sync"):
        acked = sync_engine.apply_push(conn, peer_id, events, service)

    rows = conn.execute(
        "SELECT content, source_device, source_app FROM clips ORDER BY id"
    ).fetchall()
    assert acked == 2
    assert PeersRepo(conn).get(peer_id)["peer_cursor"] == 2
    assert [tuple(row) for row in rows] == [
        ("safe remote legacy peer content", "   ", None)
    ]
    assert conn.execute("SELECT COUNT(*) FROM backup_queue").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM obsidian_queue").fetchone()[0] == 1
    assert ORIGIN_SECRET not in caplog.text


def test_legacy_origin_metadata_is_blocked_by_sync_gate_b(conn):
    clip = _legacy_clip(released=True, released_at=NOW)
    ClipsRepo(conn).insert(clip)

    assert sync_engine.emit_clip_new(conn, clip, NOW) is None
    assert sync_engine.emit_clip_meta(
        conn,
        clip.content_hash,
        {"pinned": True},
        NOW,
        NOW,
    ) is None
    assert OutboxRepo(conn).list_since(0) == []

    seq = OutboxRepo(conn).append("clip_new", sync_engine.clip_to_data(clip), NOW)
    pull = sync_engine.build_pull(conn, 0)
    assert pull["events"] == []
    assert pull["next_seq"] == seq


def test_obsidian_blocks_legacy_origin_without_retry_churn(conn, tmp_path, caplog):
    clip = _legacy_clip()
    ClipsRepo(conn).insert(clip)
    queue = ObsidianQueueRepo(conn)
    assert queue.enqueue(clip.id, NOW)
    claim = queue.claim_one(clip.id, NOW)
    assert claim is not None
    service = ClipVaultService(conn, _cfg(tmp_path))

    with caplog.at_level(logging.ERROR):
        assert service._process_obsidian_claim(claim, NOW) is False

    row = conn.execute(
        "SELECT state, last_error FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone()
    assert tuple(row) == ("blocked_origin_metadata", "OriginMetadataSecret")
    assert queue.has_ready(NOW) is False
    assert queue.claim_ready(NOW, limit=50) == []
    assert queue.claim_one(clip.id, NOW) is None
    assert queue._recover_expired(NOW, limit=50) == 0
    assert queue.stats(NOW) == {
        "pending": 1,
        "ready": 0,
        "blocked": 1,
        "max_attempts": 0,
        "next_attempt_at": None,
    }
    assert queue.reconcile_missing(NOW, limit=50) == 0
    assert queue.cleanup_ineligible(limit=50) == 0
    assert conn.execute(
        "SELECT state FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone()[0] == "blocked_origin_metadata"
    service.clips.set_flag(clip.id, "deleted", True)
    assert queue.cleanup_ineligible(limit=50) == 1
    assert conn.execute(
        "SELECT 1 FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone() is None
    assert not (tmp_path / "vault").exists()
    assert ORIGIN_SECRET not in caplog.text


def test_obsidian_writer_refuses_released_clip_with_unsafe_origin():
    clip = _legacy_clip(released=True, released_at=NOW)

    assert origin_metadata.origin_metadata_is_safe(
        clip.source_device, clip.source_app
    ) is False
    with pytest.raises(writer.SecretWriteRefused):
        writer.render(clip)
    with pytest.raises(writer.SecretWriteRefused):
        writer.write_clip(clip, "unused-vault")


def test_obsidian_application_refuses_unsafe_origin_before_injected_writer(
    conn, tmp_path
):
    called = False

    def unsafe_adapter(*_args):
        nonlocal called
        called = True
        return tmp_path / "must-not-exist.md"

    service = ClipVaultService(conn, _cfg(tmp_path))
    service.obsidian_commands._write_clip = unsafe_adapter

    with pytest.raises(writer.SecretWriteRefused):
        service.obsidian_commands.try_write(_legacy_clip())

    assert called is False
