"""Application-layer dependency direction and facade compatibility."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipvault.application.obsidian_commands import ObsidianCommands
from clipvault.config import Config
from clipvault.service import ClipVaultService
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.obsidian_queue_repo import ObsidianQueueRepo


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_APPLICATION_PREFIXES = (
    "clipvault.api",
    "clipvault.obsidian",
    "clipvault.pipeline",
    "clipvault.runtime",
    "clipvault.service",
    "clipvault.sync",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _cfg(tmp_path) -> Config:
    return Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        device_name="application-test",
        db_path=":memory:",
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
    )


def test_obsidian_application_command_has_no_upward_or_concrete_adapter_imports():
    imports = _imports(ROOT / "clipvault/application/obsidian_commands.py")

    assert not any(
        module == prefix or module.startswith(prefix + ".")
        for module in imports
        for prefix in FORBIDDEN_APPLICATION_PREFIXES
    )


def test_obsidian_worker_depends_on_application_not_service():
    imports = _imports(ROOT / "clipvault/runtime/obsidian_worker.py")

    assert "clipvault.application.obsidian_commands" in imports
    assert "clipvault.service" not in imports


def test_service_facade_and_commands_share_repository_instances(conn, tmp_path):
    service = ClipVaultService(conn, _cfg(tmp_path))

    assert service.obsidian_commands.clips is service.clips
    assert service.obsidian_commands.queue is service.obsidian_queue


def test_service_retry_facade_preserves_arguments(conn, tmp_path, monkeypatch):
    service = ClipVaultService(conn, _cfg(tmp_path))
    captured = {}
    now_fn = lambda: "2026-07-13T00:00:00Z"

    def retry_sweep(**kwargs):
        captured.update(kwargs)
        return 7

    monkeypatch.setattr(service.obsidian_commands, "retry_sweep", retry_sweep)

    assert service.retry_obsidian_sweep(
        limit=17,
        max_runtime_ms=321,
        now_fn=now_fn,
    ) == 7
    assert captured["limit"] == 17
    assert captured["max_runtime_ms"] == 321
    assert captured["now_fn"] is now_fn
    assert captured["process_claim"] == service._process_obsidian_claim
    assert captured["record_failure"] == service._record_claim_failure


def test_service_facade_preserves_process_claim_override(conn, tmp_path, monkeypatch):
    service = ClipVaultService(conn, _cfg(tmp_path))
    clip = SimpleNamespace(id="legacy-hook")
    claim = SimpleNamespace(clip_id=clip.id)
    calls = []

    monkeypatch.setattr(service.obsidian_queue, "enqueue", lambda *_args: None)
    monkeypatch.setattr(service.obsidian_queue, "claim_one", lambda *_args: claim)
    monkeypatch.setattr(
        service,
        "_process_obsidian_claim",
        lambda got_claim, now: calls.append((got_claim, now)) or True,
    )

    assert service.write_obsidian_or_queue(clip) is True
    assert calls and calls[0][0] is claim


def test_service_facade_preserves_try_write_override(conn, tmp_path, monkeypatch):
    service = ClipVaultService(conn, _cfg(tmp_path))
    clip = SimpleNamespace(
        id="legacy-write-hook",
        is_secret=False,
        deleted=False,
        obsidian_path=None,
    )
    claim = SimpleNamespace(clip_id=clip.id)

    monkeypatch.setattr(service.clips, "get", lambda _clip_id: clip)
    monkeypatch.setattr(
        service,
        "_try_write_obsidian",
        lambda _clip: (None, "LegacyFailure"),
    )
    failures = []
    monkeypatch.setattr(
        service,
        "_record_claim_failure",
        lambda got_claim, error, now: failures.append((got_claim, error, now)),
    )

    assert service._process_obsidian_claim(claim, "2026-07-13T00:00:00Z") is False
    assert failures == [(claim, "LegacyFailure", "2026-07-13T00:00:00Z")]


def test_service_facade_rebinds_replaced_repositories(conn, tmp_path):
    service = ClipVaultService(conn, _cfg(tmp_path))
    replacement_clips = object()
    replacement_queue = object()
    service.clips = replacement_clips
    service.obsidian_queue = replacement_queue

    service._sync_obsidian_command_context()

    assert service.obsidian_commands.clips is replacement_clips
    assert service.obsidian_commands.queue is replacement_queue


def test_application_preserves_secret_writer_refusal(conn, tmp_path):
    class Refused(Exception):
        pass

    def refuse(*_args):
        raise Refused()

    commands = ObsidianCommands(
        conn,
        clips=ClipsRepo(conn),
        queue=ObsidianQueueRepo(conn),
        vault_path=str(tmp_path / "vault"),
        type_dirs={},
        write_clip=refuse,
        secret_write_refused=Refused,
    )

    with pytest.raises(Refused):
        commands.try_write(SimpleNamespace(id="secret-id"))


def test_service_resolves_secret_writer_refusal_dynamically(
    conn, tmp_path, monkeypatch
):
    class ReplacementRefused(Exception):
        pass

    service = ClipVaultService(conn, _cfg(tmp_path))
    monkeypatch.setattr(
        "clipvault.obsidian.writer.SecretWriteRefused",
        ReplacementRefused,
    )
    monkeypatch.setattr(
        "clipvault.obsidian.writer.write_clip",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ReplacementRefused()),
    )

    with pytest.raises(ReplacementRefused):
        service._try_write_obsidian(SimpleNamespace(id="secret-id"))


def test_application_writer_failure_logs_only_error_class(conn, tmp_path, caplog):
    marker = r"D:\Private\Vault"

    def fail(*_args):
        raise PermissionError(marker)

    commands = ObsidianCommands(
        conn,
        clips=ClipsRepo(conn),
        queue=ObsidianQueueRepo(conn),
        vault_path=str(tmp_path / "vault"),
        type_dirs={},
        write_clip=fail,
        secret_write_refused=RuntimeError,
    )

    with caplog.at_level("ERROR", logger="clipvault.application.obsidian"):
        assert commands.try_write(SimpleNamespace(id="public-id")) == (
            None,
            "PermissionError",
        )
    assert marker not in caplog.text
