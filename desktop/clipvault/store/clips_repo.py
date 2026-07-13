"""Clip persistence. Owns the FTS invariant:
rows with is_secret=1 or deleted=1 never enter clips_fts (GATES G1).
"""

from contextlib import contextmanager
import json
import sqlite3
from typing import Iterator

from clipvault.core.models import Clip
from clipvault.store.unit_of_work import unit_of_work

_COLUMNS = (
    "id, content, content_hash, content_type, is_secret, secret_level, "
    "secret_reasons, released, released_at, source_device, source_app, "
    "created_at, last_seen_at, times_seen, pinned, favorite, deleted, "
    "obsidian_path, backed_up_at"
)
_COLUMN_NAMES = tuple(_COLUMNS.split(", "))

# The clips_fts trigram tokenizer (migration 0005) indexes 3-char sequences, so
# it can only match queries of length >= 3. Shorter queries (common for CJK, e.g.
# 2-char words like "天气") fall back to a LIKE scan, which is fine at personal
# scale and the only option for secret-view search (secrets are never in FTS).
_FTS_MIN_LEN = 3
_FTS_RECENT_PROBE_SIZE = 256
_FTS_COMMON_MATCH_THRESHOLD = 4_096
_FTS_HINT_CONTENT_CHARS = 4_096


def _qualified_columns(alias: str) -> str:
    return ", ".join(f"{alias}.{name} AS {name}" for name in _COLUMN_NAMES)


@contextmanager
def _read_snapshot(conn: sqlite3.Connection) -> Iterator[None]:
    """Keep multi-statement adaptive searches on one SQLite snapshot."""

    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN")
    try:
        yield
    except BaseException:
        if owns_transaction:
            conn.rollback()
        raise
    else:
        if owns_transaction:
            try:
                conn.commit()
            except BaseException:
                conn.rollback()
                raise


def _fts_match(query: str) -> str:
    """Wrap a query as an FTS5 phrase so the whole thing is matched as one literal
    substring (spaces and query operators included), escaping embedded quotes.
    With the trigram tokenizer this yields literal substring search."""
    return '"' + query.replace('"', '""') + '"'


def _like_term(query: str) -> str:
    """LIKE pattern matching `query` as a literal substring; %/_/\\ are escaped so
    they are not treated as wildcards (use with ESCAPE '\\')."""
    esc = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


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

    def _index_public_clip(self, clip_id: str, content: str) -> None:
        """Create or repair the stable map + FTS row for one public clip."""

        self.conn.execute(
            "INSERT OR IGNORE INTO clip_search_map(clip_id) VALUES (?)",
            (clip_id,),
        )
        row = self.conn.execute(
            "SELECT search_id FROM clip_search_map WHERE clip_id = ?",
            (clip_id,),
        ).fetchone()
        if row is None:
            raise sqlite3.IntegrityError("search map row was not created")
        search_id = int(row[0])
        # Normal lifecycle calls have at most this one stable rowid.  Replacing
        # it also repairs an interrupted legacy/manual index entry atomically.
        self.conn.execute("DELETE FROM clips_fts WHERE rowid = ?", (search_id,))
        self.conn.execute(
            "INSERT INTO clips_fts(rowid, id, content) VALUES (?, ?, ?)",
            (search_id, clip_id, content),
        )

    def _unindex_clip(self, clip_id: str) -> None:
        row = self.conn.execute(
            "SELECT search_id FROM clip_search_map WHERE clip_id = ?",
            (clip_id,),
        ).fetchone()
        if row is None:
            # Defensive cleanup for a pre-schema-9 drifted database.  This is
            # intentionally off the normal hot path because id is UNINDEXED.
            self.conn.execute("DELETE FROM clips_fts WHERE id = ?", (clip_id,))
            return
        # Schema 9 owns physical/soft-delete cleanup through the map trigger.
        # It deletes by stable rowid; full startup repair owns any legacy
        # orphan/duplicate cleanup so normal deletes never scan UNINDEXED id.
        self.conn.execute("DELETE FROM clip_search_map WHERE clip_id = ?", (clip_id,))

    def remove_from_search_index(
        self, clip_id: str, *, commit: bool = True
    ) -> None:
        """Enforce Gate C for a row reclassified by a newer Secret Guard."""

        try:
            self._unindex_clip(clip_id)
            if commit:
                self.conn.commit()
        except BaseException:
            if commit:
                self.conn.rollback()
            raise

    def _search_index_drifted(self) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM ("
            "SELECT c.id FROM clips AS c "
            "LEFT JOIN clip_search_map AS m ON m.clip_id=c.id "
            "LEFT JOIN clips_fts ON clips_fts.rowid=m.search_id "
            "AND clips_fts.id=m.clip_id "
            "WHERE c.is_secret=0 AND c.deleted=0 "
            "AND (m.clip_id IS NULL OR clips_fts.rowid IS NULL "
            "OR clips_fts.content IS NOT c.content) "
            "UNION ALL "
            "SELECT m.clip_id FROM clip_search_map AS m "
            "LEFT JOIN clips AS c ON c.id=m.clip_id "
            "WHERE c.id IS NULL OR c.is_secret=1 OR c.deleted=1 "
            "UNION ALL "
            "SELECT clips_fts.id FROM clips_fts "
            "LEFT JOIN clip_search_map AS m "
            "ON m.search_id=clips_fts.rowid AND m.clip_id=clips_fts.id "
            "WHERE m.search_id IS NULL"
            ") LIMIT 1"
        ).fetchone()
        return row is not None

    def repair_search_index(self) -> bool:
        """Repair drift left by legacy writers after a schema-9 upgrade.

        Schema-8 binaries do not know about ``clip_search_map``.  If one is
        accidentally used against the upgraded database, the next current
        service start rebuilds the complete map/FTS bijection before serving.
        """

        if not self._search_index_drifted():
            return False
        with unit_of_work(self.conn):
            # A complete rebuild follows.  Clear FTS first so map-delete
            # triggers never repeat work while stale mappings are removed.
            self.conn.execute("DELETE FROM clips_fts")
            self.conn.execute(
                "DELETE FROM clip_search_map "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM clips WHERE clips.id=clip_search_map.clip_id "
                "AND clips.is_secret=0 AND clips.deleted=0"
                ")"
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO clip_search_map(clip_id) "
                "SELECT clips.id FROM clips "
                "LEFT JOIN clip_search_map ON clip_search_map.clip_id=clips.id "
                "WHERE clips.is_secret=0 AND clips.deleted=0 "
                "AND clip_search_map.clip_id IS NULL ORDER BY clips.id"
            )
            self.conn.execute(
                "INSERT INTO clips_fts(rowid,id,content) "
                "SELECT m.search_id,c.id,c.content "
                "FROM clip_search_map AS m "
                "JOIN clips AS c ON c.id=m.clip_id"
            )
        return True

    def insert(self, clip: Clip, *, commit: bool = True) -> None:
        try:
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
                self._index_public_clip(clip.id, clip.content)
            if commit:
                self.conn.commit()
        except BaseException:
            if commit:
                self.conn.rollback()
            raise

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

    def touch_seen(self, clip_id: str, when: str, *, commit: bool = True) -> None:
        self.conn.execute(
            "UPDATE clips SET times_seen = times_seen + 1, last_seen_at = ? WHERE id = ?",
            (when, clip_id),
        )
        if commit:
            self.conn.commit()

    def set_obsidian_path(
        self, clip_id: str, path: str, *, commit: bool = True
    ) -> None:
        self.conn.execute(
            "UPDATE clips SET obsidian_path = ? WHERE id = ?", (path, clip_id)
        )
        if commit:
            self.conn.commit()

    def set_backed_up_at(self, clip_id: str, when: str) -> None:
        self.conn.execute(
            "UPDATE clips SET backed_up_at = ? WHERE id = ?", (when, clip_id)
        )
        self.conn.commit()

    def all_clips(self) -> list[Clip]:
        rows = self.conn.execute(
            f"SELECT {_COLUMNS} FROM clips ORDER BY created_at"
        ).fetchall()
        return [_row_to_clip(r) for r in rows]

    @staticmethod
    def _public_filters(
        alias: str,
        *,
        content_type: str | None,
        before_id: str | None,
    ) -> tuple[list[str], list]:
        where = [f"{alias}.is_secret = 0", f"{alias}.deleted = 0"]
        params: list = []
        if content_type:
            where.append(f"{alias}.content_type = ?")
            params.append(content_type)
        if before_id:
            where.append(f"{alias}.id < ?")
            params.append(before_id)
        return where, params

    def _fts_fallback_rows(
        self,
        query: str,
        *,
        content_type: str | None,
        before_id: str | None,
        limit: int,
        api_order: bool,
    ) -> list[sqlite3.Row]:
        where, params = self._public_filters(
            "c", content_type=content_type, before_id=before_id
        )
        where.extend(
            [
                "clips_fts MATCH ?",
                "clips_fts.id = clip_search_map.clip_id",
            ]
        )
        params.extend((_fts_match(query), limit))
        order = (
            "c.pinned DESC, c.last_seen_at DESC, c.id DESC"
            if api_order
            else "c.last_seen_at DESC, c.id DESC"
        )
        return self.conn.execute(
            f"SELECT {_qualified_columns('c')} "
            "FROM clips_fts "
            "JOIN clip_search_map "
            "ON clip_search_map.search_id = clips_fts.rowid "
            "JOIN clips AS c ON c.id = clip_search_map.clip_id "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY {order} LIMIT ?",
            params,
        ).fetchall()

    def _fts_recent_probe_rows(
        self,
        query: str,
        *,
        content_type: str | None,
        before_id: str | None,
        limit: int,
        probe_size: int,
        api_order: bool,
    ) -> list[sqlite3.Row]:
        where, params = self._public_filters(
            "c", content_type=content_type, before_id=before_id
        )
        inner_order = (
            "c.pinned DESC, c.last_seen_at DESC, c.id DESC"
            if api_order
            else "c.last_seen_at DESC, c.id DESC"
        )
        outer_order = (
            "recent.pinned DESC, recent.last_seen_at DESC, recent.id DESC"
            if api_order
            else "recent.last_seen_at DESC, recent.id DESC"
        )
        params.extend((probe_size, _fts_match(query), limit))
        return self.conn.execute(
            f"SELECT {_qualified_columns('recent')} FROM ("
            f"SELECT {_qualified_columns('c')} FROM clips AS c "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY {inner_order} LIMIT ?"
            ") AS recent "
            "JOIN clip_search_map ON clip_search_map.clip_id = recent.id "
            "WHERE EXISTS ("
            "SELECT 1 FROM clips_fts "
            "WHERE clips_fts.rowid = clip_search_map.search_id "
            "AND clips_fts.id = recent.id AND clips_fts MATCH ?"
            ") "
            f"ORDER BY {outer_order} LIMIT ?",
            params,
        ).fetchall()

    def _recent_probe_likely_full(
        self,
        query: str,
        *,
        limit: int,
        probe_size: int,
        api_order: bool,
    ) -> bool:
        """Cheap path-selection hint for the bounded recent FTS probe.

        The LIKE result is never returned to callers: FTS remains the source of
        search semantics, including Unicode case folding.  Applying LIKE only
        after the ordered candidate LIMIT and to a fixed content prefix keeps
        this hint bounded by both rows and inspected text.  Prefix misses safely
        select the exact fallback.
        """

        if limit < 1 or probe_size < limit:
            return False
        where, params = self._public_filters(
            "c", content_type=None, before_id=None
        )
        if api_order:
            index_name = "idx_clips_public_list_recent"
            order = "c.pinned DESC, c.last_seen_at DESC, c.id DESC"
        else:
            index_name = "idx_clips_public_search_recent"
            order = "c.last_seen_at DESC, c.id DESC"
        params.extend((probe_size, _like_term(query), limit - 1))
        return self.conn.execute(
            "SELECT 1 FROM ("
            f"SELECT substr(c.content, 1, {_FTS_HINT_CONTENT_CHARS}) AS content "
            f"FROM clips AS c INDEXED BY {index_name} "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY {order} LIMIT ?"
            ") AS recent "
            "WHERE recent.content LIKE ? ESCAPE '\\' "
            "LIMIT 1 OFFSET ?",
            params,
        ).fetchone() is not None

    def _public_fts_rows(
        self,
        query: str,
        *,
        content_type: str | None = None,
        before_id: str | None = None,
        limit: int = 50,
        api_order: bool,
    ) -> list[sqlite3.Row]:
        """Exact adaptive FTS search over one read snapshot.

        Rare match sets use the normal FTS-first integer-map join.  For a
        common term, a bounded candidate prefix is checked in final result
        order.  The prefix is returned only when it already contains the full
        requested page; otherwise the exact fallback is used.
        """

        with _read_snapshot(self.conn):
            # Filters can make SQLite select/sort an unbounded type/id range
            # before applying LIMIT.  Keep the recent probe strictly to the
            # unfiltered API hot path and use the exact FTS-first join for all
            # filtered or non-API-sized repository requests.
            if (
                limit < 1
                or limit > _FTS_RECENT_PROBE_SIZE
                or content_type is not None
                or before_id is not None
            ):
                return self._fts_fallback_rows(
                    query,
                    content_type=content_type,
                    before_id=before_id,
                    limit=limit,
                    api_order=api_order,
                )
            probe_size = _FTS_RECENT_PROBE_SIZE
            common = self.conn.execute(
                "SELECT 1 FROM clips_fts WHERE clips_fts MATCH ? "
                "LIMIT 1 OFFSET ?",
                (_fts_match(query), _FTS_COMMON_MATCH_THRESHOLD),
            ).fetchone()
            if common is not None and self._recent_probe_likely_full(
                query,
                limit=limit,
                probe_size=probe_size,
                api_order=api_order,
            ):
                recent = self._fts_recent_probe_rows(
                    query,
                    content_type=content_type,
                    before_id=before_id,
                    limit=limit,
                    probe_size=probe_size,
                    api_order=api_order,
                )
                if len(recent) >= limit:
                    return recent
            return self._fts_fallback_rows(
                query,
                content_type=content_type,
                before_id=before_id,
                limit=limit,
                api_order=api_order,
            )

    def search_fts(self, query: str, limit: int = 50) -> list[Clip]:
        q = query.strip()
        if not q or limit <= 0:
            return []
        if len(q) >= _FTS_MIN_LEN:
            rows = self._public_fts_rows(q, limit=limit, api_order=False)
        else:
            # Short query (e.g. a 2-char CJK word): trigram cannot match < 3 chars,
            # so scan with LIKE. Filter is_secret/deleted explicitly here because we
            # bypass clips_fts, which is the index that normally excludes them (G1).
            rows = self.conn.execute(
                f"SELECT {_COLUMNS} FROM clips "
                "WHERE is_secret = 0 AND deleted = 0 AND content LIKE ? ESCAPE '\\' "
                "ORDER BY last_seen_at DESC, id DESC LIMIT ?",
                (_like_term(q), limit),
            ).fetchall()
        return [_row_to_clip(r) for r in rows]

    def fts_contains(self, clip_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM clip_search_map "
            "JOIN clips_fts ON clips_fts.rowid = clip_search_map.search_id "
            "WHERE clip_search_map.clip_id = ? "
            "AND clips_fts.id = clip_search_map.clip_id",
            (clip_id,),
        ).fetchone()
        return row is not None

    # --- S004: query / flags / release ---

    def list_clips(self, *, query: str | None = None, content_type: str | None = None,
                   secret: bool = False, limit: int = 50,
                   before_id: str | None = None) -> list[Clip]:
        """List clips newest-first. secret=False excludes quarantined clips
        (API-1: secrets only surface when explicitly requested)."""
        if limit <= 0:
            return []
        where = ["deleted = 0"]
        params: list = []
        where.append("is_secret = 1" if secret else "is_secret = 0")
        if content_type:
            where.append("content_type = ?")
            params.append(content_type)
        if before_id:
            where.append("id < ?")
            params.append(before_id)
        q = (query or "").strip()
        if q:
            # FTS (trigram) only for the non-secret view with a >= 3-char query;
            # otherwise LIKE — secrets are never in clips_fts, and trigram can't
            # match < 3 chars (common for CJK).
            if not secret and len(q) >= _FTS_MIN_LEN:
                rows = self._public_fts_rows(
                    q,
                    content_type=content_type,
                    before_id=before_id,
                    limit=limit,
                    api_order=True,
                )
                return [_row_to_clip(r) for r in rows]
            else:
                where.append("content LIKE ? ESCAPE '\\'")
                params.append(_like_term(q))
        sql = (f"SELECT {_COLUMNS} FROM clips WHERE " + " AND ".join(where)
               + " ORDER BY pinned DESC, last_seen_at DESC, id DESC LIMIT ?")
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_clip(r) for r in rows]

    def set_flag(
        self,
        clip_id: str,
        field: str,
        value: bool,
        *,
        commit: bool = True,
    ) -> bool:
        if field not in ("pinned", "favorite", "deleted"):
            raise ValueError(f"not a settable flag: {field}")
        previous = self.get(clip_id) if field == "deleted" else None
        try:
            cur = self.conn.execute(
                f"UPDATE clips SET {field} = ? WHERE id = ?", (int(value), clip_id)
            )
            if (
                field == "deleted"
                and cur.rowcount > 0
                and previous is not None
                and previous.deleted != value
            ):
                if value:
                    self._unindex_clip(clip_id)
                else:
                    clip = self.get(clip_id)
                    if clip is not None and not clip.is_secret:
                        self._index_public_clip(clip.id, clip.content)
            if commit:
                self.conn.commit()
            return cur.rowcount > 0
        except BaseException:
            if commit:
                self.conn.rollback()
            raise

    def release_secret(
        self, clip_id: str, when: str, *, commit: bool = True
    ) -> Clip | None:
        """Mark a quarantined clip as not-secret and re-index it (DB-1 §4.3)."""
        clip = self.get(clip_id)
        if clip is None or not clip.is_secret:
            return None
        try:
            self.conn.execute(
                "UPDATE clips SET is_secret = 0, secret_level = NULL, "
                "secret_reasons = '[]', released = 1, released_at = ? "
                "WHERE id = ?",
                (when, clip_id),
            )
            if not clip.deleted:
                self._index_public_clip(clip_id, clip.content)
            if commit:
                self.conn.commit()
            return self.get(clip_id)
        except BaseException:
            if commit:
                self.conn.rollback()
            raise

    def suggest_candidates(self, since_iso: str, limit: int = 200) -> list[Clip]:
        """High-use recent clips eligible as suggestion candidates (SUG-1):
        non-secret, non-deleted, favorite OR times_seen>=3, seen since `since_iso`."""
        rows = self.conn.execute(
            f"SELECT {_COLUMNS} FROM clips INDEXED BY idx_clips_suggest_recent "
            "WHERE is_secret = 0 AND deleted = 0 AND last_seen_at >= ? "
            "AND (favorite = 1 OR times_seen >= 3) "
            "ORDER BY last_seen_at DESC, id DESC LIMIT ?",
            (since_iso, limit),
        ).fetchall()
        return [_row_to_clip(r) for r in rows]

    def counts(self) -> dict:
        total = self.conn.execute(
            "SELECT COUNT(*) FROM clips WHERE deleted = 0"
        ).fetchone()[0]
        secret = self.conn.execute(
            "SELECT COUNT(*) FROM clips WHERE deleted = 0 AND is_secret = 1"
        ).fetchone()[0]
        return {"total": total, "secret": secret}
