from __future__ import annotations

import codecs
import tomllib

import pytest

from clipvault import v2_configure

HOST = "C:/Program Files/ClipVault/ime/host-x64/ClipVaultImeHost.exe"


def _config_text() -> str:
    return '''# owner comment
[device]
device_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

[custom]
keep = "unchanged # value"
description = """
[ime_snapshot]
this line belongs to the custom multiline value
"""

[ime_snapshot]
enabled = false # keep snapshot comment
host_path = "D:/old/ClipVaultImeHost.exe"
require_signed_host = false
future_key = "preserve-me"

[otp_relay]
windows_broker_enabled = true
pairing_enabled = true # keep OTP comment
'''


def test_atomic_upsert_preserves_comments_unknown_content_bom_and_defaults_otp_off(
    tmp_path,
):
    path = tmp_path / "config.toml"
    original = _config_text().replace("\n", "\r\n")
    path.write_bytes(codecs.BOM_UTF8 + original.encode("utf-8"))

    result = v2_configure.configure_v2_ime_host(HOST, config_path=path)

    raw = path.read_bytes()
    updated = raw.decode("utf-8-sig")
    parsed = tomllib.loads(updated)
    assert result == path
    assert raw.startswith(codecs.BOM_UTF8)
    assert "\r\n" in updated
    assert "# owner comment" in updated
    assert 'keep = "unchanged # value"' in updated
    assert "this line belongs to the custom multiline value" in updated
    assert 'future_key = "preserve-me"' in updated
    assert "# keep snapshot comment" in updated
    assert "# keep OTP comment" in updated
    assert updated.count("[ime_snapshot]") == 2  # one is multiline text
    assert parsed["ime_snapshot"]["enabled"] is True
    assert parsed["ime_snapshot"]["host_path"] == HOST
    assert parsed["ime_snapshot"]["require_signed_host"] is True
    assert parsed["otp_relay"]["windows_broker_enabled"] is False
    assert parsed["otp_relay"]["pairing_enabled"] is False


def test_explicit_otp_opt_in_sets_broker_and_pairing_together(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(_config_text(), encoding="utf-8")

    v2_configure.configure_v2_ime_host(
        HOST,
        enable_otp_relay=True,
        config_path=path,
    )

    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["otp_relay"] == {
        "windows_broker_enabled": True,
        "pairing_enabled": True,
    }


@pytest.mark.parametrize(
    "host_path",
    [
        "ClipVaultImeHost.exe",
        "//server/share/ClipVaultImeHost.exe",
        "//?/C:/ClipVault/ClipVaultImeHost.exe",
        "//./C:/ClipVault/ClipVaultImeHost.exe",
    ],
)
def test_rejected_host_path_never_changes_existing_config(tmp_path, host_path):
    path = tmp_path / "config.toml"
    original = _config_text().encode("utf-8")
    path.write_bytes(original)

    with pytest.raises(Exception):
        v2_configure.configure_v2_ime_host(host_path, config_path=path)

    assert path.read_bytes() == original


def test_replace_failure_leaves_original_and_removes_temporary_file(tmp_path):
    path = tmp_path / "config.toml"
    original = _config_text().encode("utf-8")
    path.write_bytes(original)

    def fail_replace(_source, _target):
        raise OSError(r"C:\Users\owner\private\config.toml")

    with pytest.raises(v2_configure.V2ConfigurationError) as exc:
        v2_configure.configure_v2_ime_host(
            HOST,
            config_path=path,
            replace=fail_replace,
        )

    assert str(exc.value) == "configuration update failed"
    assert path.read_bytes() == original
    assert list(tmp_path.glob(".config.toml.*.tmp")) == []
