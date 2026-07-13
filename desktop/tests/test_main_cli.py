import pytest

from clipvault import main as clipvault_main
from clipvault.config import Config
from clipvault.pipeline import ingest as pipeline
from clipvault.store import db
from clipvault.store.clips_repo import ClipsRepo


def test_help_renders_literal_localappdata_placeholder(capsys):
    with pytest.raises(SystemExit) as exc:
        clipvault_main.main(["--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "%LOCALAPPDATA%/ClipVault/config.toml" in output


def test_once_writes_current_clip_even_with_older_pending_work(tmp_path, monkeypatch):
    cfg = Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        device_name="once-test",
        db_path=str(tmp_path / "once.sqlite3"),
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
        log_dir=str(tmp_path / "logs"),
    )
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    older = pipeline.ingest(
        conn,
        "older pending item",
        source_device=cfg.device_name,
        now_fn=lambda: "2026-07-13T00:00:00Z",
    ).clip
    conn.close()

    monkeypatch.setattr(clipvault_main.config_mod, "load", lambda _path: cfg)
    monkeypatch.setattr(clipvault_main, "setup_logging", lambda _cfg: None)
    monkeypatch.setattr(clipvault_main, "get_clipboard_text", lambda: "current once item")
    monkeypatch.setattr(clipvault_main, "get_foreground_app", lambda: "pytest")

    assert clipvault_main.main(["--once", "--config", "ignored.toml"]) == 0

    verify = db.connect(cfg.db_path)
    current = ClipsRepo(verify).get_by_hash(
        pipeline.normalize.content_hash("current once item")
    )
    assert current.obsidian_path is not None
    assert ClipsRepo(verify).get(older.id).obsidian_path is None
    assert verify.execute(
        "SELECT 1 FROM obsidian_queue WHERE clip_id=?", (older.id,)
    ).fetchone() is not None
    verify.close()
