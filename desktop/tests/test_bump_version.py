"""Unit tests for the single-source version tool (scripts/bump_version.py).

The pure transform functions are tested on sample text; `check()` and the CLI
`--check` path are exercised against the live repo (read-only), which also
asserts the repo is currently aligned.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bump_version.py"
_spec = importlib.util.spec_from_file_location("bump_version", _SCRIPT)
bump_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump_version)


def test_set_init_version():
    assert bump_version.set_init_version('__version__ = "1.5.16"\n', "1.6.0") == '__version__ = "1.6.0"\n'


def test_set_pyproject_version_only_touches_version_line():
    src = 'name = "x"\nversion = "1.5.16"\ndescription = "about 1.5.16"\n'
    out = bump_version.set_pyproject_version(src, "1.6.0")
    assert 'version = "1.6.0"' in out
    assert 'description = "about 1.5.16"' in out  # other lines untouched


def test_set_gradle_version_name_and_code():
    src = "        versionCode = 12\n        versionName = \"1.5.16\"\n"
    out = bump_version.set_gradle_version(src, "1.6.0", 13)
    assert 'versionName = "1.6.0"' in out
    assert "versionCode = 13" in out


def test_set_gradle_keeps_code_when_none():
    src = "        versionCode = 12\n        versionName = \"1.5.16\"\n"
    out = bump_version.set_gradle_version(src, "1.6.0", None)
    assert "versionCode = 12" in out  # left untouched


def test_set_installer_version():
    out = bump_version.set_installer_version('#define AppVersion "1.5.16"\n', "1.6.0")
    assert '#define AppVersion "1.6.0"' in out


def test_setters_raise_when_pattern_missing():
    import pytest
    with pytest.raises(ValueError):
        bump_version.set_init_version("no version here\n", "1.6.0")


def test_check_passes_on_aligned_repo():
    # The live repo must currently be aligned; this also exercises the CLI path.
    assert bump_version.check() == 0
    assert bump_version.main(["--check"]) == 0
