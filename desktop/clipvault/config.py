"""CFG-1 config loading (CONTRACTS §12). Fail fast on invalid values."""

import math
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from clipvault.core import origin_metadata, ulid
from clipvault.obsidian.writer import DEFAULT_TYPE_DIRS

_DEVICE_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,80}$")

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

[otp_relay]
windows_broker_enabled = false
pairing_enabled = false

[ime_snapshot]
enabled = false
host_path = ""
require_signed_host = true

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


def _table(data: dict, name: str) -> dict:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(name, "must be a TOML table")
    return value


def _string(
    table: dict,
    key: str,
    default: str,
    *,
    fieldname: str,
    allow_empty: bool = True,
) -> str:
    value = table.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(fieldname, "must be a string")
    value = value.strip()
    if not allow_empty and not value:
        raise ConfigError(fieldname, "must be a non-empty string")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ConfigError(fieldname, "must not contain control characters")
    return value


def _integer(
    table: dict,
    key: str,
    default: int,
    *,
    fieldname: str,
    minimum: int,
    maximum: int | None = None,
) -> int:
    value = table.get(key, default)
    if type(value) is not int or value < minimum or (
        maximum is not None and value > maximum
    ):
        upper = "" if maximum is None else f" and at most {maximum}"
        raise ConfigError(
            fieldname,
            f"must be an integer of at least {minimum}{upper}",
        )
    return value


def _finite_number(
    table: dict,
    key: str,
    default: float,
    *,
    fieldname: str,
    positive: bool = False,
) -> float:
    value = table.get(key, default)
    if type(value) not in (int, float):
        raise ConfigError(fieldname, "must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0) or (
        not positive and result < 0
    ):
        qualifier = "positive finite" if positive else "non-negative finite"
        raise ConfigError(fieldname, f"must be a {qualifier} number")
    return result


def _vault_relative_directory(value: object, *, fieldname: str) -> str:
    """Return a canonical directory that cannot escape an Obsidian vault."""

    if not isinstance(value, str):
        raise ConfigError(fieldname, "values must be non-empty strings")
    value = value.strip()
    if not value or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ConfigError(fieldname, "values must be non-empty safe strings")
    path = PureWindowsPath(value)
    unsafe_windows_segment = any(
        not part.rstrip(" .") or part.endswith((" ", "."))
        for part in path.parts
    )
    if (
        path.is_absolute()
        or bool(path.drive)
        or bool(path.root)
        or any(part in (".", "..") for part in path.parts)
        or unsafe_windows_segment
        or ":" in value
    ):
        raise ConfigError(
            fieldname,
            "values must be relative directories contained by the vault",
        )
    return "/".join(path.parts)


def validate_ime_snapshot_host_path(value: object, *, enabled: bool) -> str:
    """Return a canonical input string for the local signed Host boundary."""

    if not isinstance(value, str):
        raise ConfigError(
            "ime_snapshot.host_path",
            "must be a string",
        )
    host_path = value.strip()
    if enabled:
        windows_host_path = PureWindowsPath(host_path)
        if (
            not windows_host_path.is_absolute()
            or re.fullmatch(r"[A-Za-z]:", windows_host_path.drive) is None
        ):
            raise ConfigError(
                "ime_snapshot.host_path",
                "must be a local drive absolute Windows path when enabled",
            )
    return host_path


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
    otp_windows_broker_enabled: bool = False
    otp_pairing_enabled: bool = False
    ime_snapshot_enabled: bool = False
    ime_snapshot_host_path: str = ""
    ime_snapshot_require_signed_host: bool = True
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
    device = _table(data, "device")
    storage = _table(data, "storage")
    watcher = _table(data, "watcher")
    obsidian = _table(data, "obsidian")
    backup = _table(data, "backup")
    server = _table(data, "server")
    otp_relay = _table(data, "otp_relay")
    ime_snapshot = _table(data, "ime_snapshot")
    log = _table(data, "log")
    sug = _table(data, "suggest")

    vault_path = _string(
        obsidian,
        "vault_path",
        "",
        fieldname="obsidian.vault_path",
        allow_empty=False,
    )
    port = _integer(
        server,
        "port",
        8787,
        fieldname="server.port",
        minimum=1,
        maximum=65535,
    )
    max_clip_bytes = _integer(
        storage,
        "max_clip_bytes",
        1_048_576,
        fieldname="storage.max_clip_bytes",
        minimum=1,
    )
    poll_ms = _integer(
        watcher,
        "poll_fallback_ms",
        500,
        fieldname="watcher.poll_fallback_ms",
        minimum=50,
    )

    backup_repo_path = _string(
        backup,
        "repo_path",
        "",
        fieldname="backup.repo_path",
    )
    backup_interval_minutes = backup.get("interval_minutes", 15)
    if type(backup_interval_minutes) is not int or backup_interval_minutes <= 0:
        raise ConfigError(
            "backup.interval_minutes",
            "must be a positive integer",
        )
    backup_enabled = backup.get("enabled", False)
    if type(backup_enabled) is not bool:
        raise ConfigError("backup.enabled", "must be a boolean")
    if backup_enabled and not backup_repo_path:
        raise ConfigError(
            "backup.repo_path",
            "must be a non-empty path when backup is enabled",
        )

    otp_windows_broker_enabled = otp_relay.get("windows_broker_enabled", False)
    if type(otp_windows_broker_enabled) is not bool:
        raise ConfigError(
            "otp_relay.windows_broker_enabled",
            "must be a boolean",
        )
    otp_pairing_enabled = otp_relay.get("pairing_enabled", False)
    if type(otp_pairing_enabled) is not bool:
        raise ConfigError(
            "otp_relay.pairing_enabled",
            "must be a boolean",
        )
    if otp_pairing_enabled and not otp_windows_broker_enabled:
        raise ConfigError(
            "otp_relay.windows_broker_enabled",
            "must be true when otp_relay.pairing_enabled is true",
        )

    ime_snapshot_enabled = ime_snapshot.get("enabled", False)
    if type(ime_snapshot_enabled) is not bool:
        raise ConfigError(
            "ime_snapshot.enabled",
            "must be a boolean",
        )
    ime_snapshot_require_signed_host = ime_snapshot.get(
        "require_signed_host",
        True,
    )
    if type(ime_snapshot_require_signed_host) is not bool:
        raise ConfigError(
            "ime_snapshot.require_signed_host",
            "must be a boolean",
        )
    ime_snapshot_host_path = validate_ime_snapshot_host_path(
        ime_snapshot.get("host_path", ""),
        enabled=ime_snapshot_enabled,
    )

    device_name = device.get("device_name", "desktop-main")
    if (
        not isinstance(device_name, str)
        or not device_name.strip()
        or not origin_metadata.source_device_is_safe(device_name)
    ):
        raise ConfigError(
            "device.device_name",
            "must be a non-empty, content-safe string of at most "
            f"{origin_metadata.SOURCE_DEVICE_MAX_CHARS} characters without "
            "control characters",
        )
    device_name = device_name.strip()

    device_id = device.get("device_id", "")
    if not isinstance(device_id, str):
        raise ConfigError("device.device_id", "must be a string")
    device_id = device_id.strip()
    if not device_id:
        device_id = ulid.new()
        _persist_device_id(path, device_id)
    elif _DEVICE_ID_RE.fullmatch(device_id) is None:
        raise ConfigError(
            "device.device_id",
            "must use 1-80 URL-safe letters, digits, underscore or hyphen",
        )

    type_dirs = dict(DEFAULT_TYPE_DIRS)
    configured_type_dirs = obsidian.get("type_dirs", {})
    if not isinstance(configured_type_dirs, dict):
        raise ConfigError("obsidian.type_dirs", "must be a TOML table")
    for key, value in configured_type_dirs.items():
        if not isinstance(key, str) or not key:
            raise ConfigError(
                "obsidian.type_dirs",
                "keys must be non-empty strings",
            )
        type_dirs[key] = _vault_relative_directory(
            value,
            fieldname="obsidian.type_dirs",
        )

    db_path = _string(
        storage,
        "db_path",
        "data/clipvault.db",
        fieldname="storage.db_path",
        allow_empty=False,
    )
    host = _string(
        server,
        "host",
        "127.0.0.1",
        fieldname="server.host",
        allow_empty=False,
    )
    log_dir = _string(
        log,
        "dir",
        "logs",
        fieldname="log.dir",
        allow_empty=False,
    )
    log_retention_days = _integer(
        log,
        "retention_days",
        14,
        fieldname="log.retention_days",
        minimum=1,
    )
    suggest_half_life_days = _finite_number(
        sug,
        "half_life_days",
        14.0,
        fieldname="suggest.half_life_days",
        positive=True,
    )
    suggest_w_pinned = _finite_number(
        sug, "w_pinned", 3.0, fieldname="suggest.w_pinned"
    )
    suggest_w_prefix = _finite_number(
        sug, "w_prefix", 1.5, fieldname="suggest.w_prefix"
    )
    suggest_w_substr = _finite_number(
        sug, "w_substr", 0.6, fieldname="suggest.w_substr"
    )
    suggest_w_freq = _finite_number(
        sug, "w_freq", 1.0, fieldname="suggest.w_freq"
    )
    suggest_w_app = _finite_number(
        sug, "w_app", 0.5, fieldname="suggest.w_app"
    )

    return Config(
        device_id=device_id,
        device_name=device_name,
        db_path=db_path,
        max_clip_bytes=max_clip_bytes,
        poll_ms=poll_ms,
        vault_path=vault_path,
        type_dirs=type_dirs,
        backup_repo_path=backup_repo_path,
        backup_interval_minutes=backup_interval_minutes,
        backup_enabled=backup_enabled,
        host=host,
        port=port,
        otp_windows_broker_enabled=otp_windows_broker_enabled,
        otp_pairing_enabled=otp_pairing_enabled,
        ime_snapshot_enabled=ime_snapshot_enabled,
        ime_snapshot_host_path=ime_snapshot_host_path,
        ime_snapshot_require_signed_host=ime_snapshot_require_signed_host,
        log_dir=log_dir,
        log_retention_days=log_retention_days,
        suggest_half_life_days=suggest_half_life_days,
        suggest_w_pinned=suggest_w_pinned,
        suggest_w_prefix=suggest_w_prefix,
        suggest_w_substr=suggest_w_substr,
        suggest_w_freq=suggest_w_freq,
        suggest_w_app=suggest_w_app,
    )


def _persist_device_id(path: Path, device_id: str) -> None:
    text = path.read_text(encoding="utf-8-sig")
    new_text, n = re.subn(
        r'(?m)^(device_id\s*=\s*)""', rf'\g<1>"{device_id}"', text, count=1
    )
    if n == 1:
        path.write_text(new_text, encoding="utf-8", newline="\n")
