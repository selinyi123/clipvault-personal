"""A10 / G8: clipvault/core must stay IO-free."""

import ast
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent.parent / "clipvault" / "core"

FORBIDDEN = {
    "sqlite3", "requests", "httpx", "socket", "urllib", "http", "subprocess",
    "shutil", "asyncio", "aiohttp", "os", "pathlib", "io", "tempfile",
}


def test_core_has_no_io_imports():
    violations = []
    for source in CORE_DIR.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if name in FORBIDDEN:
                    violations.append(f"{source.name}: {name}")
    assert violations == []
