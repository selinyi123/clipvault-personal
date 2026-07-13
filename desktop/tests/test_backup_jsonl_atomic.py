import json
import os
from pathlib import Path

import pytest

from clipvault.backup import jsonl_store


RELPATH = "clips/2026/06/2026-06-13.jsonl"


def _line(clip_id: str, state: str) -> str:
    return json.dumps(
        {"id": clip_id, "state": state},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _target(repo: Path) -> Path:
    return repo / Path(RELPATH)


def test_latest_state_retry_is_noop_and_a_b_a_is_preserved(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    a = _line("clip-1", "A")
    b = _line("clip-1", "B")

    target = jsonl_store.append_latest_clip_states(repo, RELPATH, [a])

    def unexpected_replace(*_args, **_kwargs):
        raise AssertionError("an identical retry must not replace the file")

    monkeypatch.setattr(jsonl_store.os, "replace", unexpected_replace)
    assert jsonl_store.append_latest_clip_states(repo, RELPATH, [a]) == target
    monkeypatch.undo()

    jsonl_store.append_latest_clip_states(repo, RELPATH, [b])
    jsonl_store.append_latest_clip_states(repo, RELPATH, [a])
    assert target.read_text(encoding="utf-8").splitlines() == [a, b, a]


def test_latest_state_tracks_each_clip_independently_and_in_input_order(tmp_path):
    repo = tmp_path / "repo"
    one_a = _line("clip-1", "A")
    two_a = _line("clip-2", "A")
    one_b = _line("clip-1", "B")

    target = jsonl_store.append_latest_clip_states(
        repo, RELPATH, [one_a, two_a, one_a, one_b, two_a]
    )

    assert target.read_text(encoding="utf-8").splitlines() == [
        one_a,
        two_a,
        one_b,
    ]


@pytest.mark.parametrize(
    "damaged",
    [
        b'{"id":"clip-1"}',
        b'{"id":"clip-1"}\nnot-json\n',
        b'{"id":"clip-1"}\n\xff\n',
        b'\n',
    ],
    ids=["missing-final-newline", "invalid-json", "invalid-utf8", "blank-line"],
)
def test_existing_unknown_damage_fails_closed_without_repair(tmp_path, damaged):
    repo = tmp_path / "repo"
    target = _target(repo)
    target.parent.mkdir(parents=True)
    target.write_bytes(damaged)

    with pytest.raises(jsonl_store.JsonlIntegrityError):
        jsonl_store.append_latest_clip_states(repo, RELPATH, [_line("clip-2", "A")])

    assert target.read_bytes() == damaged
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_valid_json_without_clip_id_fails_closed_for_state_comparison(tmp_path):
    repo = tmp_path / "repo"
    target = _target(repo)
    target.parent.mkdir(parents=True)
    original = b'{"legacy":true}\n'
    target.write_bytes(original)

    with pytest.raises(jsonl_store.JsonlIntegrityError):
        jsonl_store.append_latest_clip_states(repo, RELPATH, [_line("clip-1", "A")])

    assert target.read_bytes() == original


def test_replace_failure_keeps_original_and_removes_temporary_file(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    a = _line("clip-1", "A")
    b = _line("clip-1", "B")
    target = jsonl_store.append_latest_clip_states(repo, RELPATH, [a])
    original = target.read_bytes()

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(jsonl_store.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        jsonl_store.append_latest_clip_states(repo, RELPATH, [b])

    assert target.read_bytes() == original
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_atomic_replacement_uses_same_directory_and_fsyncs_file(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    target = _target(repo)
    real_replace = jsonl_store.os.replace
    replacements = []
    fsynced = []

    def observe_replace(source, destination):
        source = Path(source)
        destination = Path(destination)
        replacements.append((source, destination))
        assert source.parent == destination.parent
        real_replace(source, destination)

    def observe_fsync(fd):
        fsynced.append(fd)

    monkeypatch.setattr(jsonl_store.os, "replace", observe_replace)
    monkeypatch.setattr(jsonl_store.os, "fsync", observe_fsync)

    jsonl_store.append_latest_clip_states(repo, RELPATH, [_line("clip-1", "A")])

    assert replacements and replacements[0][1] == target
    assert fsynced


def test_append_lines_remains_compatible_but_is_atomic_and_validating(tmp_path):
    repo = tmp_path / "repo"
    first = _line("clip-1", "A")
    second = _line("clip-2", "A")

    target = jsonl_store.append_lines(repo, RELPATH, [first])
    assert jsonl_store.append_lines(repo, RELPATH, [second]) == target
    assert list(jsonl_store.iter_jsonl(repo)) == [first, second]

    original = target.read_bytes()
    with pytest.raises(jsonl_store.JsonlIntegrityError):
        jsonl_store.append_lines(repo, RELPATH, ["not-json"])
    assert target.read_bytes() == original


def test_legacy_crlf_records_are_validated_and_preserved(tmp_path):
    repo = tmp_path / "repo"
    target = _target(repo)
    target.parent.mkdir(parents=True)
    a = _line("clip-1", "A")
    b = _line("clip-1", "B")
    target.write_bytes((a + "\r\n").encode("utf-8"))

    jsonl_store.append_latest_clip_states(repo, RELPATH, [b])

    assert target.read_bytes() == (a + "\r\n" + b + "\n").encode("utf-8")


def test_state_append_streams_existing_file_without_path_read_text(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    a = _line("clip-1", "A")
    b = _line("clip-1", "B")
    jsonl_store.append_latest_clip_states(repo, RELPATH, [a])

    def forbidden_read_text(*_args, **_kwargs):
        raise AssertionError("JSONL state append must stream the file")

    monkeypatch.setattr(Path, "read_text", forbidden_read_text)
    jsonl_store.append_latest_clip_states(repo, RELPATH, [b])

    assert _target(repo).read_bytes() == (a + "\n" + b + "\n").encode("utf-8")


@pytest.mark.parametrize(
    "line",
    [
        "",
        "not-json",
        '{"id":""}',
        '{"id":1}',
        '{"id":"clip-1"}\n{"id":"clip-2"}',
        '{"id":"clip-1","value":NaN}',
    ],
)
def test_latest_state_rejects_invalid_input_before_creating_file(tmp_path, line):
    repo = tmp_path / "repo"

    with pytest.raises(jsonl_store.JsonlIntegrityError):
        jsonl_store.append_latest_clip_states(repo, RELPATH, [line])

    assert not _target(repo).exists()


def test_existing_hardlink_is_rejected_before_any_content_is_read_or_replaced(
    tmp_path,
):
    repo = tmp_path / "repo"
    target = _target(repo)
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside-private.jsonl"
    original = (_line("outside-secret", "AKIAIOSFODNN7EXAMPLE") + "\n").encode()
    outside.write_bytes(original)
    try:
        os.link(outside, target)
    except OSError as exc:  # pragma: no cover - filesystem capability guard
        pytest.skip(f"hard links unavailable: {exc.__class__.__name__}")
    assert outside.stat().st_nlink == 2

    with pytest.raises(jsonl_store.JsonlIntegrityError, match="private regular"):
        jsonl_store.append_latest_clip_states(
            repo,
            RELPATH,
            [_line("safe", "public")],
        )

    assert outside.read_bytes() == original
    assert target.read_bytes() == original
    assert outside.samefile(target)
    assert outside.stat().st_nlink == 2
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []
