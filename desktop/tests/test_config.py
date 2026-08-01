"""B1: config fail-fast behaviour and device_id backfill."""

import pytest

from clipvault import config as config_mod

VALID = """[device]
device_id   = ""
device_name = "test-desktop"

[obsidian]
vault_path = "{vault}"
"""


def test_missing_file_writes_template(tmp_path):
    path = tmp_path / "config.toml"
    with pytest.raises(config_mod.ConfigMissing):
        config_mod.load(path)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "vault_path" in text
    assert 'host = "127.0.0.1"' in text
    assert "[ime_snapshot]" in text
    assert "require_signed_host = true" in text


def test_template_itself_fails_on_empty_vault(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(config_mod.TEMPLATE, encoding="utf-8")
    with pytest.raises(config_mod.ConfigError) as exc:
        config_mod.load(path)
    assert exc.value.field == "obsidian.vault_path"


def test_bad_port_names_field(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        VALID.format(vault=tmp_path.as_posix()) + "\n[server]\nport = 99999\n",
        encoding="utf-8",
    )
    with pytest.raises(config_mod.ConfigError) as exc:
        config_mod.load(path)
    assert exc.value.field == "server.port"


def test_bad_poll_interval(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        VALID.format(vault=tmp_path.as_posix()) + "\n[watcher]\npoll_fallback_ms = 5\n",
        encoding="utf-8",
    )
    with pytest.raises(config_mod.ConfigError) as exc:
        config_mod.load(path)
    assert exc.value.field == "watcher.poll_fallback_ms"


def test_device_id_generated_and_persisted(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(VALID.format(vault=tmp_path.as_posix()), encoding="utf-8")
    cfg = config_mod.load(path)
    assert len(cfg.device_id) == 26
    assert f'device_id   = "{cfg.device_id}"' in path.read_text(encoding="utf-8")
    # second load reuses the persisted id
    assert config_mod.load(path).device_id == cfg.device_id


def test_defaults_applied(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(VALID.format(vault=tmp_path.as_posix()), encoding="utf-8")
    cfg = config_mod.load(path)
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8787
    assert cfg.poll_ms == 500
    assert cfg.max_clip_bytes == 1_048_576
    assert cfg.type_dirs["code"] == "02_Code"
    assert cfg.otp_windows_broker_enabled is False
    assert cfg.otp_pairing_enabled is False
    assert cfg.ime_snapshot_enabled is False
    assert cfg.ime_snapshot_host_path == ""
    assert cfg.ime_snapshot_require_signed_host is True


def test_otp_windows_broker_requires_explicit_boolean(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        VALID.format(vault=tmp_path.as_posix())
        + '\n[otp_relay]\nwindows_broker_enabled = "true"\n',
        encoding="utf-8",
    )
    with pytest.raises(config_mod.ConfigError) as exc:
        config_mod.load(path)
    assert exc.value.field == "otp_relay.windows_broker_enabled"


def test_otp_windows_broker_can_be_explicitly_enabled(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        VALID.format(vault=tmp_path.as_posix())
        + "\n[otp_relay]\nwindows_broker_enabled = true\n",
        encoding="utf-8",
    )
    assert config_mod.load(path).otp_windows_broker_enabled is True


def test_otp_pairing_requires_explicit_boolean_and_can_be_enabled(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        VALID.format(vault=tmp_path.as_posix())
        + '\n[otp_relay]\npairing_enabled = "true"\n',
        encoding="utf-8",
    )
    with pytest.raises(config_mod.ConfigError) as exc:
        config_mod.load(path)
    assert exc.value.field == "otp_relay.pairing_enabled"

    path.write_text(
        VALID.format(vault=tmp_path.as_posix())
        + "\n[otp_relay]\npairing_enabled = true\n",
        encoding="utf-8",
    )
    assert config_mod.load(path).otp_pairing_enabled is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("enabled", '"true"'),
        ("require_signed_host", '"false"'),
    ],
)
def test_ime_snapshot_flags_require_explicit_booleans(tmp_path, field, value):
    path = tmp_path / "config.toml"
    path.write_text(
        VALID.format(vault=tmp_path.as_posix())
        + f"\n[ime_snapshot]\n{field} = {value}\n",
        encoding="utf-8",
    )

    with pytest.raises(config_mod.ConfigError) as exc:
        config_mod.load(path)

    assert exc.value.field == f"ime_snapshot.{field}"


def test_ime_snapshot_enabled_requires_absolute_windows_host_path(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        VALID.format(vault=tmp_path.as_posix())
        + "\n[ime_snapshot]\nenabled = true\nhost_path = \"ClipVaultImeHost.exe\"\n",
        encoding="utf-8",
    )

    with pytest.raises(config_mod.ConfigError) as exc:
        config_mod.load(path)

    assert exc.value.field == "ime_snapshot.host_path"


@pytest.mark.parametrize(
    "host_path",
    [
        "//server/share/ClipVaultImeHost.exe",
        "//?/C:/ClipVault/ClipVaultImeHost.exe",
        "//./C:/ClipVault/ClipVaultImeHost.exe",
    ],
)
def test_ime_snapshot_rejects_unc_and_device_namespace_paths(
    tmp_path,
    host_path,
):
    path = tmp_path / "config.toml"
    path.write_text(
        VALID.format(vault=tmp_path.as_posix())
        + "\n[ime_snapshot]\nenabled = true\n"
        + f'host_path = "{host_path}"\n',
        encoding="utf-8",
    )

    with pytest.raises(config_mod.ConfigError) as exc:
        config_mod.load(path)

    assert exc.value.field == "ime_snapshot.host_path"


def test_ime_snapshot_accepts_explicit_absolute_windows_host(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        VALID.format(vault=tmp_path.as_posix())
        + "\n[ime_snapshot]\n"
        + "enabled = true\n"
        + 'host_path = "C:/Program Files/ClipVault/ClipVaultImeHost.exe"\n'
        + "require_signed_host = false\n",
        encoding="utf-8",
    )

    cfg = config_mod.load(path)

    assert cfg.ime_snapshot_enabled is True
    assert cfg.ime_snapshot_host_path == (
        "C:/Program Files/ClipVault/ClipVaultImeHost.exe"
    )
    assert cfg.ime_snapshot_require_signed_host is False
