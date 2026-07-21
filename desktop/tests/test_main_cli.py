import threading

import pytest

from clipvault import main as clipvault_main
from clipvault.config import Config
from clipvault.core import ulid
from clipvault.instance_lock import InstanceLock as WindowsInstanceLock
from clipvault.pipeline import ingest as pipeline
from clipvault.store import db
from clipvault.store.clips_repo import ClipsRepo


def _service_cfg(tmp_path):
    return Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        device_name="runtime-test",
        db_path=str(tmp_path / "runtime.sqlite3"),
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
        log_dir=str(tmp_path / "logs"),
    )


class _FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _prepare_service_main(monkeypatch, tmp_path, cfg):
    monkeypatch.setattr(
        clipvault_main.launcher,
        "ensure_config",
        lambda _path: tmp_path / "config.toml",
    )
    monkeypatch.setattr(clipvault_main.config_mod, "load", lambda _path: cfg)
    monkeypatch.setattr(clipvault_main, "setup_logging", lambda _cfg: None)
    monkeypatch.setattr(clipvault_main, "InstanceLock", _FakeLock)
    monkeypatch.setattr(clipvault_main.signal, "signal", lambda *_args: None)


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


def test_service_mode_returns_nonzero_after_runtime_start_failure(tmp_path, monkeypatch):
    cfg = _service_cfg(tmp_path)

    class FailedRuntime:
        def __init__(self, _cfg):
            self.stop_event = threading.Event()

        def start(self):
            raise RuntimeError("private runtime detail")

        def request_stop(self):
            self.stop_event.set()

        def close(self):
            self.request_stop()
            return []

        def terminal_errors(self):
            return {"api": "OSError"}

    _prepare_service_main(monkeypatch, tmp_path, cfg)
    monkeypatch.setattr(clipvault_main, "ClipVaultRuntime", FailedRuntime)

    assert clipvault_main.main(["--headless"]) == 1


def test_service_mode_treats_external_stop_during_start_as_clean(tmp_path, monkeypatch):
    cfg = _service_cfg(tmp_path)

    class StoppedRuntime:
        def __init__(self, _cfg):
            self.stop_event = threading.Event()

        def start(self):
            self.stop_event.set()
            raise clipvault_main.RuntimeStopRequested("signal")

        def request_stop(self):
            self.stop_event.set()

        def close(self):
            self.request_stop()
            return []

        def terminal_errors(self):
            return {}

    _prepare_service_main(monkeypatch, tmp_path, cfg)
    monkeypatch.setattr(clipvault_main, "ClipVaultRuntime", StoppedRuntime)

    assert clipvault_main.main(["--headless"]) == 0


def test_headless_service_starts_waits_and_closes_runtime(tmp_path, monkeypatch):
    cfg = _service_cfg(tmp_path)
    calls = []

    class Runtime:
        def __init__(self, _cfg):
            self.stop_event = threading.Event()

        def start(self):
            calls.append("start")

        def wait(self):
            calls.append("wait")

        def request_stop(self):
            self.stop_event.set()

        def close(self):
            calls.append("close")
            self.request_stop()
            return []

        def terminal_errors(self):
            return {}

    _prepare_service_main(monkeypatch, tmp_path, cfg)
    monkeypatch.setattr(clipvault_main, "ClipVaultRuntime", Runtime)

    assert clipvault_main.main(["--headless"]) == 0
    assert calls == ["start", "wait", "close"]


def test_service_releases_instance_mutex_after_runtime_close_and_backup_drain(
    tmp_path,
    monkeypatch,
):
    cfg = _service_cfg(tmp_path)
    name = f"Local\\ClipVaultShutdownTest-{ulid.new()}"
    events = []

    class TrackingInstanceLock(WindowsInstanceLock):
        def __init__(self):
            super().__init__(name)

        def __exit__(self, *exc):
            result = super().__exit__(*exc)
            events.append("mutex-released")
            return result

    class Runtime:
        def __init__(self, _cfg):
            self.stop_event = threading.Event()

        def start(self):
            pass

        def wait(self):
            pass

        def request_stop(self):
            self.stop_event.set()

        def close(self):
            events.append("runtime-closed")
            self.request_stop()
            return ["backup-worker"]

        def drain_backup_before_exit(self):
            events.append("backup-drained")
            return []

        def terminal_errors(self):
            return {}

    _prepare_service_main(monkeypatch, tmp_path, cfg)
    monkeypatch.setattr(clipvault_main, "InstanceLock", TrackingInstanceLock)
    monkeypatch.setattr(clipvault_main, "ClipVaultRuntime", Runtime)

    assert clipvault_main.main(["--headless"]) == 0
    assert events == ["runtime-closed", "backup-drained", "mutex-released"]

    # The kernel mutex itself, not just the context-manager callback, is free.
    with WindowsInstanceLock(name):
        pass


def test_service_drains_persistent_backup_writer_before_return(tmp_path, monkeypatch):
    cfg = _service_cfg(tmp_path)
    calls = []

    class Runtime:
        def __init__(self, _cfg):
            self.stop_event = threading.Event()

        def start(self):
            calls.append("start")

        def wait(self):
            calls.append("wait")

        def request_stop(self):
            self.stop_event.set()

        def close(self):
            calls.append("close")
            self.request_stop()
            return ["backup-worker"]

        def drain_backup_before_exit(self):
            calls.append("drain-backup")
            return []

        def terminal_errors(self):
            return {}

    _prepare_service_main(monkeypatch, tmp_path, cfg)
    monkeypatch.setattr(clipvault_main, "ClipVaultRuntime", Runtime)

    assert clipvault_main.main(["--headless"]) == 0
    assert calls == ["start", "wait", "close", "drain-backup"]


def test_no_open_tray_fallback_waits_and_closes(tmp_path, monkeypatch):
    cfg = _service_cfg(tmp_path)
    calls = []

    class Runtime:
        def __init__(self, _cfg):
            self.stop_event = threading.Event()

        def start(self):
            calls.append("start")

        def wait(self):
            calls.append("wait")

        def request_stop(self):
            self.stop_event.set()

        def close(self):
            calls.append("close")
            self.request_stop()
            return []

        def terminal_errors(self):
            return {}

    def run_tray(_port, _base, _on_quit, stop_event):
        assert stop_event is not None
        calls.append("tray")
        return False

    _prepare_service_main(monkeypatch, tmp_path, cfg)
    monkeypatch.setattr(clipvault_main, "ClipVaultRuntime", Runtime)
    monkeypatch.setattr(clipvault_main.launcher, "run_tray", run_tray)
    monkeypatch.setattr(
        clipvault_main.launcher,
        "open_panel",
        lambda _port: pytest.fail("--no-open must not open the panel"),
    )

    assert clipvault_main.main(["--no-open"]) == 0
    assert calls == ["start", "tray", "wait", "close"]


def test_second_instance_opens_existing_panel(tmp_path, monkeypatch):
    cfg = _service_cfg(tmp_path)
    opened = []

    class AlreadyRunningLock:
        def __enter__(self):
            raise clipvault_main.AlreadyRunningError("test")

        def __exit__(self, *_args):
            return False

    _prepare_service_main(monkeypatch, tmp_path, cfg)
    monkeypatch.setattr(clipvault_main, "InstanceLock", AlreadyRunningLock)
    monkeypatch.setattr(clipvault_main.launcher, "open_panel", opened.append)

    assert clipvault_main.main([]) == 0
    assert opened == [cfg.port]
