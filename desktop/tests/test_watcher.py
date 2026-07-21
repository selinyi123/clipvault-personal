"""B5: polling watcher logic with injected win32 functions."""

import threading
from types import SimpleNamespace

from clipvault.watcher import win_clipboard
from clipvault.watcher.win_clipboard import (
    ClipboardReadKind,
    ClipboardReadResult,
    PollingWatcher,
    _dispatch_retry_delay,
    clipboard_exclusion_reason_from_formats,
    get_clipboard_read,
    get_clipboard_text,
)


class Script:
    def __init__(self, seqs, texts):
        self.seqs = list(seqs)
        self.texts = list(texts)
        self.captured = []

    def get_seq(self):
        return self.seqs.pop(0)

    def get_text(self):
        return self.texts.pop(0)

    def on_text(self, text, app):
        self.captured.append((text, app))


def _watcher(script):
    return PollingWatcher(
        script.on_text,
        get_seq=script.get_seq,
        get_text=script.get_text,
        get_app=lambda: "test.exe",
    )


def test_b5_baseline_then_capture_once():
    s = Script(seqs=[5, 5, 6, 6], texts=["copied"])
    w = _watcher(s)
    assert w.tick() is False  # baseline: pre-existing clipboard not ingested
    assert w.tick() is False  # unchanged
    assert w.tick() is True   # seq changed -> exactly one capture
    assert w.tick() is False  # unchanged again
    assert s.captured == [("copied", "test.exe")]


def test_b5_none_and_empty_text_ignored():
    s = Script(seqs=[1, 2, 3], texts=[None, ""])
    w = _watcher(s)
    w.tick()
    assert w.tick() is False  # None text (non-text clipboard)
    assert w.tick() is False  # empty text
    assert s.captured == []


def test_b5_seq_advances_even_when_text_unreadable():
    s = Script(seqs=[1, 2, 2], texts=[None])
    w = _watcher(s)
    w.tick()
    assert w.tick() is False  # unreadable, but seq recorded
    assert w.tick() is False  # no re-read for the same seq


def test_b5_transient_busy_same_sequence_then_captures_exactly_once():
    s = Script(
        seqs=[1, 2, 2, 2],
        texts=[
            ClipboardReadResult.retry(),
            ClipboardReadResult.from_text("copied after busy"),
        ],
    )
    w = _watcher(s)

    assert w.tick() is False  # baseline
    assert w.tick() is False  # transient busy: sequence remains pending
    assert w.tick() is True   # same sequence becomes readable
    assert w.tick() is False  # consumed exactly once
    assert s.captured == [("copied after busy", "test.exe")]
    assert s.texts == []


def test_b5_typed_skip_consumes_sequence_without_re_read():
    reads = 0
    seqs = iter((1, 2, 2))

    def get_text():
        nonlocal reads
        reads += 1
        return ClipboardReadResult.skip()

    watcher = PollingWatcher(
        lambda _text, _app: None,
        get_seq=lambda: next(seqs),
        get_text=get_text,
        get_app=lambda: "test.exe",
    )

    assert watcher.tick() is False
    assert watcher.tick() is False
    assert watcher.tick() is False
    assert reads == 1


def test_b5_sequence_change_after_atomic_read_preserves_old_and_new_text():
    s = Script(
        seqs=[1, 2, 3, 3],
        texts=[
            ClipboardReadResult.from_text("old value", sequence=2),
            ClipboardReadResult.from_text("new value", sequence=3),
        ],
    )
    w = _watcher(s)

    assert w.tick() is False  # baseline
    assert w.tick() is True   # old value was read atomically before the change
    assert w.tick() is True   # latest sequence remains pending for next tick
    assert w.tick() is False
    assert s.captured == [
        ("old value", "test.exe"),
        ("new value", "test.exe"),
    ]
    assert s.texts == []


def test_b5_clipboard_read_result_repr_never_contains_text():
    content = "private clipboard payload"

    rendered = repr(ClipboardReadResult.from_text(content))

    assert content not in rendered
    assert "text=" not in rendered


def test_b5_dispatch_failure_retries_same_sequence_without_content_log(caplog):
    content = "private clipboard payload"
    seqs = iter((1, 2, 2))
    texts = iter((content, content))
    attempts = 0
    captured = []
    errors = []
    stop = threading.Event()

    def on_text(text, app):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError(content)
        captured.append((text, app))
        stop.set()

    watcher = PollingWatcher(
        on_text,
        get_seq=lambda: next(seqs),
        get_text=lambda: next(texts),
        get_app=lambda: "test.exe",
        interval_ms=1,
        on_error=lambda error_class, failures: errors.append((error_class, failures)),
    )

    with caplog.at_level("ERROR", logger="clipvault.watcher"):
        watcher.run(stop)

    assert attempts == 2
    assert captured == [(content, "test.exe")]
    assert errors == [("RuntimeError", 1), (None, 0)]
    assert content not in caplog.text


def test_b5_dispatch_retry_delay_is_exponential_and_capped():
    assert _dispatch_retry_delay(500, 1) == 0.5
    assert _dispatch_retry_delay(500, 2) == 1.0
    assert _dispatch_retry_delay(500, 20) == 30.0


def test_b5_windows_exclusion_formats_block_capture():
    assert clipboard_exclusion_reason_from_formats(
        lambda name: name == "ExcludeClipboardContentFromMonitorProcessing",
        lambda name: 1,
    ) == "ExcludeClipboardContentFromMonitorProcessing"
    assert clipboard_exclusion_reason_from_formats(
        lambda name: name == "Clipboard Viewer Ignore",
        lambda name: 1,
    ) == "Clipboard Viewer Ignore"
    assert clipboard_exclusion_reason_from_formats(
        lambda name: name == "CanIncludeInClipboardHistory",
        lambda name: 0,
    ) == "CanIncludeInClipboardHistory=0"
    assert clipboard_exclusion_reason_from_formats(
        lambda name: name == "CanUploadToCloudClipboard",
        lambda name: 0,
    ) == "CanUploadToCloudClipboard=0"


def test_b5_windows_exclusion_formats_allow_explicit_opt_in():
    assert clipboard_exclusion_reason_from_formats(
        lambda name: name in {"CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"},
        lambda name: 1,
    ) is None


def test_b5_windows_exclusion_formats_fail_closed_when_flag_unreadable():
    assert clipboard_exclusion_reason_from_formats(
        lambda name: name == "CanIncludeInClipboardHistory",
        lambda name: None,
    ) == "CanIncludeInClipboardHistory=unreadable"


def test_b5_open_clipboard_final_busy_is_retry(monkeypatch):
    open_calls = 0
    sleeps = []

    def open_clipboard(_owner):
        nonlocal open_calls
        open_calls += 1
        return False

    monkeypatch.setattr(
        win_clipboard,
        "_user32",
        SimpleNamespace(OpenClipboard=open_clipboard),
    )
    monkeypatch.setattr(win_clipboard.time, "sleep", sleeps.append)

    result = get_clipboard_read(retries=3, retry_delay=0.01)

    assert result == ClipboardReadResult.retry()
    assert open_calls == 3
    assert sleeps == [0.01, 0.01]


def test_b5_declared_unicode_with_unavailable_handle_is_retry(monkeypatch):
    closed = []
    monkeypatch.setattr(
        win_clipboard,
        "_user32",
        SimpleNamespace(
            OpenClipboard=lambda _owner: True,
            CloseClipboard=lambda: closed.append(True),
            IsClipboardFormatAvailable=lambda _fmt: True,
            GetClipboardData=lambda _fmt: 0,
        ),
    )
    monkeypatch.setattr(
        win_clipboard,
        "_clipboard_exclusion_reason_open",
        lambda: None,
    )

    assert get_clipboard_read() == ClipboardReadResult.retry()
    assert closed == [True]


def test_b5_declared_unicode_with_temporarily_unlocked_handle_is_retry(monkeypatch):
    closed = []
    monkeypatch.setattr(
        win_clipboard,
        "_user32",
        SimpleNamespace(
            OpenClipboard=lambda _owner: True,
            CloseClipboard=lambda: closed.append(True),
            IsClipboardFormatAvailable=lambda _fmt: True,
            GetClipboardData=lambda _fmt: 7,
        ),
    )
    monkeypatch.setattr(
        win_clipboard,
        "_kernel32",
        SimpleNamespace(GlobalLock=lambda _handle: 0),
    )
    monkeypatch.setattr(
        win_clipboard,
        "_clipboard_exclusion_reason_open",
        lambda: None,
    )

    assert get_clipboard_read() == ClipboardReadResult.retry()
    assert closed == [True]


def test_b5_text_sequence_is_captured_before_clipboard_closes(monkeypatch):
    events = []
    monkeypatch.setattr(
        win_clipboard,
        "_user32",
        SimpleNamespace(
            OpenClipboard=lambda _owner: events.append("open") or True,
            CloseClipboard=lambda: events.append("close"),
            IsClipboardFormatAvailable=lambda _fmt: True,
            GetClipboardData=lambda _fmt: 7,
            GetClipboardSequenceNumber=lambda: events.append("sequence") or 42,
        ),
    )
    monkeypatch.setattr(
        win_clipboard,
        "_kernel32",
        SimpleNamespace(
            GlobalLock=lambda _handle: 11,
            GlobalUnlock=lambda _handle: events.append("unlock"),
        ),
    )
    monkeypatch.setattr(
        win_clipboard,
        "_clipboard_exclusion_reason_open",
        lambda: None,
    )
    monkeypatch.setattr(win_clipboard.ctypes, "wstring_at", lambda _ptr: "text")

    result = get_clipboard_read()

    assert result == ClipboardReadResult.from_text("text", sequence=42)
    assert events == ["open", "unlock", "sequence", "close"]


def test_b5_non_text_excluded_and_empty_values_are_skip(monkeypatch):
    closed = []
    user32 = SimpleNamespace(
        OpenClipboard=lambda _owner: True,
        CloseClipboard=lambda: closed.append(True),
        IsClipboardFormatAvailable=lambda _fmt: False,
        GetClipboardSequenceNumber=lambda: 42,
    )
    monkeypatch.setattr(win_clipboard, "_user32", user32)
    monkeypatch.setattr(
        win_clipboard,
        "_clipboard_exclusion_reason_open",
        lambda: None,
    )
    assert get_clipboard_read() == ClipboardReadResult.skip(sequence=42)

    monkeypatch.setattr(
        win_clipboard,
        "_clipboard_exclusion_reason_open",
        lambda: "producer-excluded",
    )
    assert get_clipboard_read() == ClipboardReadResult.skip(sequence=42)

    user32.IsClipboardFormatAvailable = lambda _fmt: True
    user32.GetClipboardData = lambda _fmt: 7
    monkeypatch.setattr(
        win_clipboard,
        "_clipboard_exclusion_reason_open",
        lambda: None,
    )
    monkeypatch.setattr(
        win_clipboard,
        "_kernel32",
        SimpleNamespace(
            GlobalLock=lambda _handle: 11,
            GlobalUnlock=lambda _handle: None,
        ),
    )
    monkeypatch.setattr(win_clipboard.ctypes, "wstring_at", lambda _ptr: "")
    assert get_clipboard_read() == ClipboardReadResult.skip(sequence=42)
    assert len(closed) == 3


def test_b5_get_clipboard_text_keeps_legacy_return_type(monkeypatch):
    monkeypatch.setattr(
        win_clipboard,
        "get_clipboard_read",
        lambda **_kwargs: ClipboardReadResult.from_text("legacy text"),
    )
    assert get_clipboard_text() == "legacy text"

    monkeypatch.setattr(
        win_clipboard,
        "get_clipboard_read",
        lambda **_kwargs: ClipboardReadResult(ClipboardReadKind.RETRY),
    )
    assert get_clipboard_text() is None
