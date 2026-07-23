from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clipvault.core import secret_guard


_ROOT = Path(__file__).resolve().parents[2]
_PROBE_PATH = _ROOT / "tools" / "clipboard_privacy_probe.py"
_SPEC = importlib.util.spec_from_file_location("clipboard_privacy_probe", _PROBE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_PROBE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_PROBE)


@pytest.mark.parametrize("case", sorted(_PROBE.PROBES))
def test_default_marker_is_unique_public_text(case: str) -> None:
    instant = datetime(2026, 7, 23, 1, 2, 3, 4, tzinfo=timezone.utc)
    first = _PROBE._default_probe_text(
        case,
        now=instant,
        nonce="00000000",
    )
    second = _PROBE._default_probe_text(
        case,
        now=instant,
        nonce="00000001",
    )

    assert first != second
    assert case.replace("-", " ") in first
    assert not secret_guard.scan(first).is_secret


def test_default_marker_rejects_unknown_case() -> None:
    with pytest.raises(ValueError, match="unknown probe case"):
        _PROBE._default_probe_text("unknown")


def test_default_marker_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone"):
        _PROBE._default_probe_text("normal", now=datetime(2026, 7, 23))


def test_main_uses_safe_default_without_replacing_explicit_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[str, str]] = []
    monkeypatch.setattr(
        _PROBE,
        "write_probe_clipboard",
        lambda case, text: writes.append((case, text)),
    )

    assert _PROBE.main(["normal"]) == 0
    assert writes[0][0] == "normal"
    assert not secret_guard.scan(writes[0][1]).is_secret

    assert _PROBE.main(["normal", "--text", "explicit control text"]) == 0
    assert writes[1] == ("normal", "explicit control text")
