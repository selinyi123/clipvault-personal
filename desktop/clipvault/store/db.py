"""Connection management and sequential migrations (DB-1)."""

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(db_path: str | Path) -> sqlite3.Connection:
    p = str(db_path)
    if p != ":memory:":
        Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT version FROM schema_meta").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> int:
    current = schema_version(conn)
    for script in sorted(migrations_dir.glob("[0-9]*.sql")):
        number = int(script.name.split("_", 1)[0])
        if number <= current:
            continue
        conn.executescript(script.read_text(encoding="utf-8"))
        conn.execute("DELETE FROM schema_meta")
        conn.execute("INSERT INTO schema_meta(version) VALUES (?)", (number,))
        conn.commit()
        current = number
    return current
