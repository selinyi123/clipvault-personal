"""C7: the git wrapper must not expose history-rewriting operations."""

import inspect

from clipvault.backup import git_repo

FORBIDDEN = {"pull", "force", "rebase", "amend", "reset", "fetch"}


def test_c7_no_history_rewriting_functions():
    public = {
        name for name, obj in inspect.getmembers(git_repo, inspect.isfunction)
        if not name.startswith("_")
    }
    assert public.isdisjoint(FORBIDDEN), f"forbidden git ops present: {public & FORBIDDEN}"


def test_c7_source_has_no_force_or_pull():
    source = inspect.getsource(git_repo)
    # The wrapper builds git argv lists; none may contain these tokens.
    for token in ('"pull"', '"--force"', '"-f"', '"rebase"', '"--amend"', '"reset"'):
        assert token not in source, f"git_repo must not reference {token}"
