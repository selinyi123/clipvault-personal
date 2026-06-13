"""B5: polling watcher logic with injected win32 functions."""

from clipvault.watcher.win_clipboard import PollingWatcher


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
