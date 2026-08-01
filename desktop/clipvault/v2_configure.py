"""Atomic local configuration for the packaged v2 Windows IME."""

from __future__ import annotations

import codecs
import json
import os
import re
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path

from clipvault import config as config_mod
from clipvault import launcher

_BARE_ASSIGNMENT = re.compile(r"^([ \t]*)([A-Za-z0-9_-]+)[ \t]*=")
_BARE_TABLE = re.compile(
    r"^[ \t]*\[([A-Za-z0-9_-]+)\][ \t]*(?:#.*)?(?:\r?\n)?$"
)
_ANY_TABLE = re.compile(r"^[ \t]*\[.+\][ \t]*(?:#.*)?(?:\r?\n)?$")


class V2ConfigurationError(RuntimeError):
    """The local configuration could not be safely updated."""


def configure_v2_ime_host(
    host_path: object,
    *,
    enable_otp_relay: bool = False,
    config_path: Path | None = None,
    replace: Callable[[str | os.PathLike[str], str | os.PathLike[str]], None]
    | None = None,
) -> Path:
    """Enable the signed Host snapshot and explicitly set OTP opt-in state."""

    canonical_host = config_mod.validate_ime_snapshot_host_path(
        host_path,
        enabled=True,
    )
    if type(enable_otp_relay) is not bool:
        raise V2ConfigurationError("OTP opt-in must be a boolean")

    path = Path(config_path or launcher.default_config_path())
    if not path.exists():
        launcher.ensure_config(path)

    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig", "strict")
        tomllib.loads(text)
        updated = _upsert_table(
            text,
            "ime_snapshot",
            {
                "enabled": "true",
                "host_path": json.dumps(canonical_host, ensure_ascii=False),
                "require_signed_host": "true",
            },
        )
        enabled = "true" if enable_otp_relay else "false"
        updated = _upsert_table(
            updated,
            "otp_relay",
            {
                "windows_broker_enabled": enabled,
                "pairing_enabled": enabled,
            },
        )
        parsed = tomllib.loads(updated)
        _verify_projection(parsed, canonical_host, enable_otp_relay)
        payload = updated.encode("utf-8", "strict")
        if raw.startswith(codecs.BOM_UTF8):
            payload = codecs.BOM_UTF8 + payload
        _atomic_replace(path, payload, replace=replace)
    except (config_mod.ConfigError, V2ConfigurationError):
        raise
    except Exception as exc:
        raise V2ConfigurationError("configuration update failed") from exc
    return path


def _verify_projection(
    parsed: Mapping[str, object],
    host_path: str,
    otp_enabled: bool,
) -> None:
    snapshot = parsed.get("ime_snapshot")
    otp = parsed.get("otp_relay")
    if not isinstance(snapshot, dict) or not isinstance(otp, dict):
        raise V2ConfigurationError("configuration projection missing")
    if (
        snapshot.get("enabled") is not True
        or snapshot.get("host_path") != host_path
        or snapshot.get("require_signed_host") is not True
        or otp.get("windows_broker_enabled") is not otp_enabled
        or otp.get("pairing_enabled") is not otp_enabled
    ):
        raise V2ConfigurationError("configuration projection mismatch")


def _upsert_table(
    text: str,
    section: str,
    updates: Mapping[str, str],
) -> str:
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    outside, headers = _toml_line_context(lines)
    matching = [index for index, name in headers if name == section]
    if len(matching) > 1:
        raise V2ConfigurationError("duplicate configuration table")

    if not matching:
        prefix = text
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += newline
        if prefix and not prefix.endswith(newline * 2):
            prefix += newline
        body = "".join(f"{key} = {value}{newline}" for key, value in updates.items())
        return f"{prefix}[{section}]{newline}{body}"

    start = matching[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if outside[index] and _ANY_TABLE.match(lines[index]):
            end = index
            break

    found: set[str] = set()
    for index in range(start + 1, end):
        if not outside[index]:
            continue
        match = _BARE_ASSIGNMENT.match(lines[index])
        if match is None or match.group(2) not in updates:
            continue
        key = match.group(2)
        if key in found:
            raise V2ConfigurationError("duplicate configuration key")
        found.add(key)
        lines[index] = _replace_assignment(
            lines[index],
            match.group(1),
            key,
            updates[key],
        )

    missing = [key for key in updates if key not in found]
    if missing:
        if end > 0 and lines[end - 1] and not lines[end - 1].endswith(("\n", "\r")):
            lines[end - 1] += newline
        lines[end:end] = [
            f"{key} = {updates[key]}{newline}"
            for key in missing
        ]
    return "".join(lines)


def _replace_assignment(
    line: str,
    indent: str,
    key: str,
    value: str,
) -> str:
    body, ending = _split_ending(line)
    if '"""' in body or "'''" in body:
        raise V2ConfigurationError("multiline managed value is unsupported")
    comment = _comment_suffix(body)
    return f"{indent}{key} = {value}{comment}{ending}"


def _split_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1]
    return line, ""


def _comment_suffix(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if quote == '"':
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif quote == "'":
            if char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == "#":
            start = index
            while start > 0 and line[start - 1] in " \t":
                start -= 1
            return line[start:]
    return ""


def _toml_line_context(
    lines: list[str],
) -> tuple[list[bool], list[tuple[int, str | None]]]:
    outside: list[bool] = []
    headers: list[tuple[int, str | None]] = []
    multiline: str | None = None
    for index, line in enumerate(lines):
        is_outside = multiline is None
        outside.append(is_outside)
        if is_outside:
            match = _BARE_TABLE.match(line)
            if match is not None:
                headers.append((index, match.group(1)))
            elif _ANY_TABLE.match(line):
                headers.append((index, None))
        multiline = _advance_multiline(line, multiline)
    if multiline is not None:
        raise V2ConfigurationError("unterminated multiline value")
    return outside, headers


def _advance_multiline(line: str, delimiter: str | None) -> str | None:
    index = 0
    quote: str | None = None
    escaped = False
    while index < len(line):
        if delimiter is not None:
            closing = line.find(delimiter, index)
            if closing < 0:
                return delimiter
            if delimiter == '"""' and _is_escaped(line, closing):
                index = closing + 3
                continue
            delimiter = None
            index = closing + 3
            continue

        if quote == '"':
            char = line[index]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if quote == "'":
            if line[index] == quote:
                quote = None
            index += 1
            continue
        if line[index] == "#":
            break
        if line.startswith('"""', index) or line.startswith("'''", index):
            delimiter = line[index : index + 3]
            index += 3
            continue
        if line[index] in ('"', "'"):
            quote = line[index]
        index += 1
    return delimiter


def _is_escaped(value: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and value[index] == "\\":
        backslashes += 1
        index -= 1
    return bool(backslashes % 2)


def _atomic_replace(
    path: Path,
    payload: bytes,
    *,
    replace: Callable[[str | os.PathLike[str], str | os.PathLike[str]], None]
    | None,
) -> None:
    replacer = replace or os.replace
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        replacer(temporary, path)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
