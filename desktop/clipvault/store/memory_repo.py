"""Personal Memory persistence (DB-1 memory_items). Candidate source for the
suggestion engine (S010).
"""

import sqlite3

from clipvault.core import ulid
from clipvault.core.models import MemoryItem

KINDS = ("term", "phrase", "prompt", "command", "key_info", "path")

_COLUMNS = (
    "id, kind, text, label, pinned, use_count, last_used_at, source, "
    "created_at, deleted"
)


def _row(r: sqlite3.Row) -> MemoryItem:
    return MemoryItem(
        id=r["id"], kind=r["kind"], text=r["text"], label=r["label"],
        pinned=bool(r["pinned"]), use_count=r["use_count"],
        last_used_at=r["last_used_at"], source=r["source"],
        created_at=r["created_at"], deleted=bool(r["deleted"]),
    )


class MemoryRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert(self, kind: str, text: str, *, label: str | None = None,
               source: str = "manual", pinned: bool = False,
               use_count: int = 0, now: str | None = None) -> MemoryItem:
        if kind not in KINDS:
            raise ValueError(f"invalid memory kind: {kind}")
        text = text.strip()
        if not text:
            raise ValueError("memory text empty")
        now = now or ulid_now()
        existing = self.by_kind_text(kind, text)
        if existing is not None:
            # update label/pinned; use_count never goes backwards
            self.conn.execute(
                "UPDATE memory_items SET label=COALESCE(?, label), pinned=?, "
                "use_count=MAX(use_count, ?), deleted=0 WHERE id=?",
                (label, int(pinned or existing.pinned), max(use_count, existing.use_count),
                 existing.id),
            )
            self.conn.commit()
            return self.get(existing.id)
        item_id = ulid.new()
        self.conn.execute(
            f"INSERT INTO memory_items ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (item_id, kind, text, label, int(pinned), use_count, None, source, now, 0),
        )
        self.conn.commit()
        return self.get(item_id)

    def get(self, item_id: str) -> MemoryItem | None:
        r = self.conn.execute(
            f"SELECT {_COLUMNS} FROM memory_items WHERE id=?", (item_id,)
        ).fetchone()
        return _row(r) if r else None

    def by_kind_text(self, kind: str, text: str) -> MemoryItem | None:
        r = self.conn.execute(
            f"SELECT {_COLUMNS} FROM memory_items WHERE kind=? AND text=?",
            (kind, text.strip()),
        ).fetchone()
        return _row(r) if r else None

    def list(self, *, kind: str | None = None, query: str | None = None,
             limit: int = 200) -> list[MemoryItem]:
        where = ["deleted=0"]
        params: list = []
        if kind:
            where.append("kind=?")
            params.append(kind)
        if query:
            where.append("(text LIKE ? OR label LIKE ?)")
            params += [f"%{query}%", f"%{query}%"]
        sql = (f"SELECT {_COLUMNS} FROM memory_items WHERE " + " AND ".join(where)
               + " ORDER BY pinned DESC, use_count DESC, "
               "COALESCE(last_used_at,'') DESC LIMIT ?")
        params.append(limit)
        return [_row(r) for r in self.conn.execute(sql, params).fetchall()]

    def bump_use(self, item_id: str, when: str) -> None:
        self.conn.execute(
            "UPDATE memory_items SET use_count=use_count+1, last_used_at=? WHERE id=?",
            (when, item_id),
        )
        self.conn.commit()

    def soft_delete(self, item_id: str) -> bool:
        cur = self.conn.execute(
            "UPDATE memory_items SET deleted=1 WHERE id=?", (item_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0


def ulid_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
