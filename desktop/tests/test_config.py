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
    assert "vault_path" in path.read_text(encoding="utf-8")
    assert 'host = "127.0.0.1"' in path.read_text(encoding="utf-8")


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
