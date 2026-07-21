"""Pure validation for clip origin metadata.

Origin metadata is exported alongside clip content by sync, Obsidian, and the
private JSONL backup.  It therefore needs the same Secret Guard treatment as
content, while keeping its existing wire-size and control-character contract.
"""

from clipvault.core import secret_guard

SOURCE_DEVICE_MAX_CHARS = 256
SOURCE_APP_MAX_CHARS = 1024


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def source_device_is_safe(value: object) -> bool:
    """Return whether a required source-device value is safe to persist/export."""

    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= SOURCE_DEVICE_MAX_CHARS
        and not _has_control_chars(value)
        and not secret_guard.scan(value).is_secret
    )


def source_app_is_safe(value: object) -> bool:
    """Return whether an optional source-application value is safe."""

    return value is None or (
        isinstance(value, str)
        and len(value) <= SOURCE_APP_MAX_CHARS
        and not _has_control_chars(value)
        and not secret_guard.scan(value).is_secret
    )


def origin_metadata_is_safe(source_device: object, source_app: object) -> bool:
    """Return whether both clip-origin fields can cross an export boundary."""

    return source_device_is_safe(source_device) and source_app_is_safe(source_app)
