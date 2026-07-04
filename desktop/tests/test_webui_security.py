"""Web UI security regression checks.

The local UI renders clipboard, memory, and paired-device data returned from
the API. Keep it on safe DOM APIs so stored user content is never parsed as
HTML.
"""

from pathlib import Path
import shutil
import subprocess

import pytest


WEBUI_JS = Path(__file__).parents[1] / "clipvault" / "api" / "webui" / "app.js"


def test_webui_avoids_html_injection_sinks():
    src = WEBUI_JS.read_text(encoding="utf-8")

    forbidden = [
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML(",
        "document.write(",
        "document.writeln(",
    ]
    for sink in forbidden:
        assert sink not in src


def test_webui_avoids_dynamic_code_execution():
    src = WEBUI_JS.read_text(encoding="utf-8")

    forbidden = [
        "eval(",
        "new Function",
        'setTimeout("',
        "setTimeout('",
        'setInterval("',
        "setInterval('",
    ]
    for sink in forbidden:
        assert sink not in src


def test_webui_keeps_search_debounce_state_module_local():
    src = WEBUI_JS.read_text(encoding="utf-8")

    assert "window._t" not in src
    assert "globalThis._t" not in src
    assert "let searchRefreshTimer = null;" in src
    assert "searchRefreshTimer = setTimeout(refresh, 200);" in src


def test_webui_surfaces_sync_blocked_status_without_html_sinks():
    src = WEBUI_JS.read_text(encoding="utf-8")

    assert "sync.blocked_pull" in src
    assert "同步阻塞" in src
    assert ".textContent = statusText;" in src


def test_webui_javascript_is_parseable_when_node_is_available():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node executable not available for JavaScript syntax check")

    result = subprocess.run(
        [node, "--check", str(WEBUI_JS)],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
