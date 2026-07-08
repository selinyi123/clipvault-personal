"""SQLite transaction boundary helpers.

Application services should wrap multi-repository write paths in ``unit_of_work``
so one logical command cannot leave partial state behind.  The helper supports
being called inside an existing transaction by using a savepoint; otherwise it
owns the top-level BEGIN/COMMIT/ROLLBACK.
"""

from __future__ import annotations

from contextlib import contextmanager
from itertools import count
import sqlite3
from typing import Iterator

_SAVEPOINT_COUNTER = count(1)


@contextmanager
def unit_of_work(conn: sqlite3.Connection) -> Iterator[None]:
    """Run a group of SQLite writes atomically.

    If the connection is already in a transaction, a SAVEPOINT is used so inner
    failures roll back only the work done by this unit.  Otherwise the helper
    starts an IMMEDIATE transaction to acquire the writer lock before any repo
    method performs partial writes.
    """

    if conn.in_transaction:
        savepoint = f"clipvault_uow_{next(_SAVEPOINT_COUNTER)}"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            yield
        except BaseException:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        else:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()
