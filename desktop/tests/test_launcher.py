"""Desktop shell lifecycle tests."""

import sys
import threading
import time
import types

import pytest

from clipvault import config as config_mod
from clipvault import launcher


def test_first_run_config_freezes_optional_ime_surfaces_off(tmp_path):
    text = launcher._config_text(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        vault_path=tmp_path / "vault",
        db_path=tmp_path / "data" / "clipvault.db",
        log_dir=tmp_path / "logs",
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(text, encoding="utf-8")

    cfg = config_mod.load(config_path)

    assert cfg.otp_windows_broker_enabled is False
    assert cfg.otp_pairing_enabled is False
    assert cfg.ime_snapshot_enabled is False
    assert cfg.ime_snapshot_host_path == ""
    assert cfg.ime_snapshot_require_signed_host is True
    assert "[otp_relay]" in text
    assert "[ime_snapshot]" in text


def test_tray_self_test_constructs_icon_without_running_it(monkeypatch):
    calls = []

    class FakeIcon:
        def __init__(self, name, image, title, menu):
            calls.append(("construct", name, image, title, menu))

        def run(self):
            raise AssertionError("self-test must not display the tray")

    fake_pystray = types.SimpleNamespace(
        Icon=FakeIcon,
        Menu=lambda *items: ("menu", items),
        MenuItem=lambda *args, **kwargs: ("item", args, kwargs),
    )
    image = object()
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
    monkeypatch.setattr(launcher, "make_icon_image", lambda: image)
    monkeypatch.setattr(
        launcher,
        "_enabled_disallowed_pillow_features",
        lambda: (),
    )

    assert launcher.self_test_tray() is None
    assert len(calls) == 1
    assert calls[0][0:4] == (
        "construct",
        "ClipVault",
        image,
        "ClipVault Personal",
    )
    assert calls[0][4][0] == "menu"


def test_tray_self_test_rejects_disallowed_optional_pillow_features(monkeypatch):
    monkeypatch.setattr(
        launcher,
        "_enabled_disallowed_pillow_features",
        lambda: ("libimagequant",),
    )
    monkeypatch.setattr(
        launcher,
        "_make_tray_icon",
        lambda *_args: pytest.fail("policy failure must precede icon construction"),
    )

    with pytest.raises(launcher.PillowFeaturePolicyError):
        launcher.self_test_tray()


def test_tray_relink_self_test_requires_marker_in_runtime_module(monkeypatch):
    fake_pystray = types.SimpleNamespace(
        CLIPVAULT_RELINK_EXERCISE_MARKER=launcher.RELINK_EXERCISE_MARKER,
        Icon=lambda *_args: object(),
        Menu=lambda *items: items,
        MenuItem=lambda *args, **kwargs: (args, kwargs),
    )
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
    monkeypatch.setattr(launcher, "make_icon_image", lambda: object())
    monkeypatch.setattr(
        launcher,
        "_enabled_disallowed_pillow_features",
        lambda: (),
    )

    assert launcher.self_test_tray(require_relink_marker=True) is None


def test_tray_relink_self_test_rejects_unmodified_runtime_module(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "pystray",
        types.SimpleNamespace(),
    )
    monkeypatch.setattr(
        launcher,
        "_enabled_disallowed_pillow_features",
        lambda: (),
    )

    with pytest.raises(launcher.TrayRelinkMarkerError):
        launcher.self_test_tray(require_relink_marker=True)


def test_third_party_notices_path_uses_pyinstaller_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr(launcher.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert launcher.third_party_notices_path() == (
        tmp_path / "THIRD_PARTY_NOTICES.md"
    )


def test_third_party_notices_path_uses_repository_root_in_source_mode(
    monkeypatch,
    tmp_path,
):
    source_file = tmp_path / "desktop" / "clipvault" / "launcher.py"
    monkeypatch.setattr(launcher.sys, "frozen", False, raising=False)
    monkeypatch.setattr(launcher, "__file__", str(source_file))

    assert launcher.third_party_notices_path() == (
        tmp_path / "THIRD_PARTY_NOTICES.md"
    )


def test_tray_menu_opens_third_party_notices(monkeypatch, tmp_path):
    opened = []

    class FakeIcon:
        def __init__(self, _name, _image, _title, menu):
            self.menu = menu

    fake_pystray = types.SimpleNamespace(
        Icon=FakeIcon,
        Menu=lambda *items: items,
        MenuItem=lambda label, callback, **kwargs: (label, callback, kwargs),
    )
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
    monkeypatch.setattr(launcher, "make_icon_image", lambda: object())
    monkeypatch.setattr(
        launcher,
        "open_third_party_notices",
        lambda: opened.append("notices"),
    )

    icon = launcher._make_tray_icon(8787, tmp_path, lambda: None)
    label, callback, _kwargs = icon.menu[2]
    callback(icon, None)

    assert label == "第三方许可证"
    assert opened == ["notices"]


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
