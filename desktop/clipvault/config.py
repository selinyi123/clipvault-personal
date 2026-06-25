"""CFG-1 config loading (CONTRACTS §12). Fail fast on invalid values."""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from clipvault.core import ulid
from clipvault.obsidian.writer import DEFAULT_TYPE_DIRS

TEMPLATE = """[device]
device_id   = ""            # 留空首次启动自动生成并回写
device_name = "desktop-main"

[storage]
db_path        = "data/clipvault.db"
max_clip_bytes = 1048576

[watcher]
poll_fallback_ms = 500

[obsidian]
vault_path = ""             # 必填：Obsidian Vault 绝对路径

[backup]
repo_path        = ""
interval_minutes = 15
enabled          = false

[server]
host = "127.0.0.1"          # 改为 0.0.0.0 前请确认只在可信 LAN/Tailscale 使用
port = 8787

[log]
dir = "logs"
retention_days = 14
"""


class ConfigMissing(Exception):
    def __init__(self, path: Path):
        self.path = path
        super().__init__(f"config not found, template written: {path}")


class ConfigError(Exception):
    def __init__(self, fieldname: str, message: str):
        self.field = fieldname
        self.message = message
        super().__init__(f"{fieldname}: {message}")


@dataclass
class Config:
    device_id: str
    device_name: str
    db_path: str
    max_clip_bytes: int
    poll_ms: int
    vault_path: str
    type_dirs: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TYPE_DIRS))
    backup_repo_path: str = ""
    backup_interval_minutes: int = 15
    backup_enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8787
    log_dir: str = "logs"
    log_retention_days: int = 14
    # SUG-1 weights (CONTRACTS §11/§12)
    suggest_half_life_days: float = 14.0
    suggest_w_pinned: float = 3.0
    suggest_w_prefix: float = 1.5
    suggest_w_substr: float = 0.6
    suggest_w_freq: float = 1.0
    suggest_w_app: float = 0.5

    def weights(self):
        from clipvault.core.suggest import Weights
        return Weights(
            pinned=self.suggest_w_pinned, prefix=self.suggest_w_prefix,
            substr=self.suggest_w_substr, freq=self.suggest_w_freq,
            app=self.suggest_w_app, half_life_days=self.suggest_half_life_days,
        )


def load(path: Path) -> Config:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE, encoding="utf-8", newline="\n")
        raise ConfigMissing(path)

    # utf-8-sig: tolerate the BOM that Notepad / PowerShell 5 prepend
    data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    device = data.get("device", {})
    storage = data.get("storage", {})
    watcher = data.get("watcher", {})
    obsidian = data.get("obsidian", {})
    backup = data.get("backup", {})
    server = data.get("server", {})
    log = data.get("log", {})

    vault_path = str(obsidian.get("vault_path", "")).strip()
    if not vault_path:
        raise ConfigError("obsidian.vault_path", "must be set to your Obsidian vault path")

    port = server.get("port", 8787)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConfigError("server.port", f"must be an integer in 1..65535, got {port!r}")

    max_clip_bytes = storage.get("max_clip_bytes", 1_048_576)
    if not isinstance(max_clip_bytes, int) or max_clip_bytes <= 0:
        raise ConfigError("storage.max_clip_bytes", "must be a positive integer")

    poll_ms = watcher.get("poll_fallback_ms", 500)
    if not isinstance(poll_ms, int) or poll_ms < 50:
        raise ConfigError("watcher.poll_fallback_ms", "must be an integer >= 50")

    device_id = str(device.get("device_id", "")).strip()
    if not device_id:
        device_id = ulid.new()
        _persist_device_id(path, device_id)

    type_dirs = dict(DEFAULT_TYPE_DIRS)
    type_dirs.update({k: str(v) for k, v in data.get("obsidian", {}).get("type_dirs", {}).items()})

    sug = data.get("suggest", {})

    return Config(
        device_id=device_id,
        device_name=str(device.get("device_name", "desktop-main")),
        db_path=str(storage.get("db_path", "data/clipvault.db")),
        max_clip_bytes=max_clip_bytes,
        poll_ms=poll_ms,
        vault_path=vault_path,
        type_dirs=type_dirs,
        backup_repo_path=str(backup.get("repo_path", "")),
        backup_interval_minutes=int(backup.get("interval_minutes", 15)),
        backup_enabled=bool(backup.get("enabled", False)),
        host=str(server.get("host", "127.0.0.1")),
        port=port,
        log_dir=str(log.get("dir", "logs")),
        log_retention_days=int(log.get("retention_days", 14)),
        suggest_half_life_days=float(sug.get("half_life_days", 14.0)),
        suggest_w_pinned=float(sug.get("w_pinned", 3.0)),
        suggest_w_prefix=float(sug.get("w_prefix", 1.5)),
        suggest_w_substr=float(sug.get("w_substr", 0.6)),
        suggest_w_freq=float(sug.get("w_freq", 1.0)),
        suggest_w_app=float(sug.get("w_app", 0.5)),
    )


def _persist_device_id(path: Path, device_id: str) -> None:
    text = path.read_text(encoding="utf-8-sig")
    new_text, n = re.subn(
        r'(?m)^(device_id\s*=\s*)""', rf'\g<1>"{device_id}"', text, count=1
    )
    if n == 1:
        path.write_text(new_text, encoding="utf-8", newline="\n")
