"""B5: polling watcher logic with injected win32 functions."""

import threading

from clipvault.watcher.win_clipboard import (
    PollingWatcher,
    _dispatch_retry_delay,
    clipboard_exclusion_reason_from_formats,
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
