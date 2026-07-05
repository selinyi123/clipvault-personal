import pytest

from clipvault import main as clipvault_main


def test_help_renders_literal_localappdata_placeholder(capsys):
    with pytest.raises(SystemExit) as exc:
        clipvault_main.main(["--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "%LOCALAPPDATA%/ClipVault/config.toml" in output
