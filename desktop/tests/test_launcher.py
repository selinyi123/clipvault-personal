"""Desktop shell lifecycle tests."""

import sys
import threading
import time
import types

from clipvault import launcher


def test_tray_stops_when_runtime_requests_external_shutdown(tmp_path, monkeypatch):
    stopped = threading.Event()

    class FakeIcon:
        def __init__(self, *_args):
            pass

        def run(self):
            assert stopped.wait(2)

        def stop(self):
            stopped.set()

    fake_pystray = types.SimpleNamespace(
        Icon=FakeIcon,
        Menu=lambda *items: items,
        MenuItem=lambda *args, **kwargs: (args, kwargs),
    )
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
    monkeypatch.setattr(launcher, "make_icon_image", lambda: object())
    external_stop = threading.Event()
    setter = threading.Thread(
        target=lambda: (time.sleep(0.02), external_stop.set()),
        daemon=True,
    )
    setter.start()

    assert launcher.run_tray(8787, tmp_path, lambda: None, external_stop) is True
    assert stopped.is_set()


def test_tray_does_not_start_when_runtime_already_stopped(tmp_path, monkeypatch):
    external_stop = threading.Event()
    external_stop.set()
    monkeypatch.setitem(
        sys.modules,
        "pystray",
        types.SimpleNamespace(
            Icon=lambda *_args: (_ for _ in ()).throw(
                AssertionError("icon must not be created after runtime stop")
            ),
            Menu=lambda *items: items,
            MenuItem=lambda *args, **kwargs: (args, kwargs),
        ),
    )

    assert launcher.run_tray(8787, tmp_path, lambda: None, external_stop) is True
