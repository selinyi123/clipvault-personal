"""A7 (golden files, idempotency, collision), A8 (fence lengthening),
secret refusal (gate B)."""

from datetime import timezone

import pytest

from clipvault.core import normalize
from clipvault.core.models import Clip
from clipvault.obsidian import writer


def _clip(content, content_type, clip_id, source_app=None):
    return Clip(
        id=clip_id,
        content=content,
        content_hash=normalize.content_hash(content),
        content_type=content_type,
        source_device="desktop-main",
        source_app=source_app,
        created_at="2026-06-12T08:30:00Z",
        last_seen_at="2026-06-12T08:30:00Z",
    )


GOLDEN_CASES = [
    (
        "text.golden.md",
        _clip("Buy milk and eggs", "text", "01ARZ3NDEKTSV4RRFFQ69G5FAV", "notepad.exe"),
        "00_Inbox/Clipboard/20260612-083000_Buy-milk-and-eggs_9G5FAV.md",
    ),
    (
        "code.golden.md",
        _clip("def main():\n    print('hi')\n    return 0", "code", "01BX5ZZKBKACTAV9WEVGEMMVRZ"),
        "02_Code/20260612-083000_def-main()_EMMVRZ.md",
    ),
    (
        "url.golden.md",
        _clip("https://github.com/anthropics/claude-code", "url", "01HQRS7Y8Z2J4K6M8N0P2Q4R6T", "chrome.exe"),
        "04_Web_Link/20260612-083000_httpsgithub.comanthropic_2Q4R6T.md",
    ),
]


@pytest.mark.parametrize("golden_name,clip,expected_rel", GOLDEN_CASES)
def test_a7_golden_render(golden_dir, golden_name, clip, expected_rel):
    rel, content = writer.render(clip, tz=timezone.utc)
    assert rel == expected_rel
    expected = (golden_dir / golden_name).read_text(encoding="utf-8")
    assert content == expected


def test_a7_write_clip_idempotent(tmp_path):
    clip = _clip("idempotent note", "text", "01ARZ3NDEKTSV4RRFFQ69G5FAV")
    first = writer.write_clip(clip, tmp_path, tz=timezone.utc)
    assert first.exists()
    clip.obsidian_path = str(first)
    second = writer.write_clip(clip, tmp_path, tz=timezone.utc)
    assert second == first
    assert len(list(tmp_path.rglob("*.md"))) == 1


def test_a7_write_clip_recovers_file_when_db_path_was_not_recorded(tmp_path, monkeypatch):
    clip = _clip("crash-safe note", "text", "01ARZ3NDEKTSV4RRFFQ69G5FAV")

    first = writer.write_clip(clip, tmp_path, tz=timezone.utc)
    assert clip.obsidian_path is None  # simulate crash before SQLite path update

    def fail_iterdir(_path):
        raise AssertionError("recovery must not enumerate the Vault directory")

    monkeypatch.setattr(type(tmp_path), "iterdir", fail_iterdir)
    second = writer.write_clip(clip, tmp_path, tz=timezone.utc)

    assert second == first
    monkeypatch.undo()
    assert len(list(tmp_path.rglob("*.md"))) == 1


def test_a7_write_clip_recovers_bounded_collision_suffix(tmp_path):
    clip = _clip("collision recovery", "text", "01ARZ3NDEKTSV4RRFFQ69G5FAV")
    rel_path, _ = writer.render(clip, tz=timezone.utc)
    writer.write(tmp_path, rel_path, "unrelated file\n")

    first = writer.write_clip(clip, tmp_path, tz=timezone.utc)
    assert first.stem.endswith("-1")
    second = writer.write_clip(clip, tmp_path, tz=timezone.utc)

    assert second == first
    assert len(list(tmp_path.rglob("*.md"))) == 2


def test_a7_collision_gets_suffix(tmp_path):
    first = writer.write(tmp_path, "00_Inbox/Clipboard/note.md", "one\n")
    second = writer.write(tmp_path, "00_Inbox/Clipboard/note.md", "two\n")
    assert first.name == "note.md"
    assert second.name == "note-1.md"
    assert first.read_text(encoding="utf-8") == "one\n"  # never overwritten
    assert len(list(tmp_path.rglob("*.tmp"))) == 0       # atomic: no temp left


def test_a8_fence_lengthened():
    content = "def x():\n    pass\n```\ninner fence\n```"
    clip = _clip(content, "code", "01ARZ3NDEKTSV4RRFFQ69G5FAV")
    _, rendered = writer.render(clip, tz=timezone.utc)
    body = rendered.split("---\n\n", 1)[1]
    assert body.startswith("````python\n")
    assert body.endswith("\n````\n")


def test_secret_refused(tmp_path):
    clip = _clip("AKIAIOSFODNN7EXAMPLE", "text", "01ARZ3NDEKTSV4RRFFQ69G5FAV")
    clip.is_secret = True
    with pytest.raises(writer.SecretWriteRefused):
        writer.render(clip)
    with pytest.raises(writer.SecretWriteRefused):
        writer.write_clip(clip, tmp_path)
    assert list(tmp_path.rglob("*")) == []
