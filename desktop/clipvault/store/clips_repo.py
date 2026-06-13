"""Clip persistence. Owns the FTS invariant:
rows with is_secret=1 or deleted=1 never enter clips_fts (GATES G1).
"""

import json
import sqlite3

from clipvault.core.models import Clip

_COLUMNS = (
    "id, content, content_hash, content_type, is_secret, secret_level, "
    "secret_reasons, released, released_at, source_device, source_app, "
    "created_at, last_seen_at, times_seen, pinned, favorite, deleted, "
    "obsidian_path, backed_up_at"
)


def _row_to_clip(row: sqlite3.Row) -> Clip:
    return Clip(
        id=row["id"],
        content=row["content"],
        content_hash=row["content_hash"],
        content_type=row["content_type"],
        is_secret=bool(row["is_secret"]),
        secret_level=row["secret_level"],
        secret_reasons=json.loads(row["secret_reasons"] or "[]"),
        released=bool(row["released"]),
        released_at=row["released_at"],
        source_device=row["source_device"],
        source_app=row["source_app"],
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"],
        times_seen=row["times_seen"],
        pinned=bool(row["pinned"]),
        favorite=bool(row["favorite"]),
        deleted=bool(row["deleted"]),
        obsidian_path=row["obsidian_path"],
        backed_up_at=row["backed_up_at"],
    )


class ClipsRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, clip: Clip) -> None:
        self.conn.execute(
            f"INSERT INTO clips ({_COLUMNS}) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                clip.id,
                clip.content,
                clip.content_hash,
                clip.content_type,
                int(clip.is_secret),
                clip.secret_level,
                json.dumps(clip.secret_reasons, ensure_ascii=False),
                int(clip.released),
                clip.released_at,
                clip.source_device,
                clip.source_app,
                clip.created_at,
                clip.last_seen_at,
                clip.times_seen,
                int(clip.pinned),
                int(clip.favorite),
                int(clip.deleted),
                clip.obsidian_path,
                clip.backed_up_at,
            ),
        )
        if not clip.is_secret and not clip.deleted:
            self.conn.execute(
                "INSERT INTO clips_fts(id, content) VALUES (?, ?)",
                (clip.id, clip.content),
            )
        self.conn.commit()

    def get(self, clip_id: str) -> Clip | None:
        row = self.conn.execute(
            f"SELECT {_COLUMNS} FROM clips WHERE id = ?", (clip_id,)
        ).fetchone()
        return _row_to_clip(row) if row else None

    def get_by_hash(self, content_hash: str) -> Clip | None:
        row = self.conn.execute(
            f"SELECT {_COLUMNS} FROM clips WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return _row_to_clip(row) if row else None

    def touch_seen(self, clip_id: str, when: str) -> None:
        self.conn.execute(
            "UPDATE clips SET times_seen = times_seen + 1, last_seen_at = ? WHERE id = ?",
            (when, clip_id),
        )
        self.conn.commit()

    def set_obsidian_path(self, clip_id: str, path: str) -> None:
        self.conn.execute(
            "UPDATE clips SET obsidian_path = ? WHERE id = ?", (path, clip_id)
        )
        self.conn.commit()

    def search_fts(self, query: str, limit: int = 50) -> list[Clip]:
        rows = self.conn.execute(
            f"""
            SELECT {_COLUMNS} FROM clips
            WHERE id IN (SELECT id FROM clips_fts WHERE clips_fts MATCH ?)
            ORDER BY last_seen_at DESC LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        return [_row_to_clip(r) for r in rows]

    def fts_contains(self, clip_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM clips_fts WHERE id = ?", (clip_id,)
        ).fetchone()
        return row is not None
