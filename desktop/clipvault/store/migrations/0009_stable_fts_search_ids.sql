-- DB-1.4: give every searchable clip a stable integer FTS rowid.  A clips
-- table rowid is not stable across VACUUM because clips.id is a TEXT primary
-- key, so it cannot safely be used by the bounded recent-search probe.
CREATE TABLE clip_search_map (
  search_id INTEGER PRIMARY KEY AUTOINCREMENT,
  clip_id   TEXT NOT NULL UNIQUE
            REFERENCES clips(id) ON DELETE CASCADE
);

-- Preserve one valid legacy FTS rowid per eligible clip.  MIN(rowid) also
-- makes the upgrade repair duplicate legacy FTS rows instead of failing the
-- UNIQUE clip_id constraint.
INSERT INTO clip_search_map(search_id, clip_id)
  SELECT MIN(clips_fts.rowid), clips.id
  FROM clips_fts
  JOIN clips ON clips.id = clips_fts.id
  WHERE clips.is_secret = 0
    AND clips.deleted = 0
  GROUP BY clips.id;

-- Schema 8 could miss a row after deleted=true -> false.  Allocate stable IDs
-- for every eligible clip that was absent from the legacy FTS table.
INSERT INTO clip_search_map(clip_id)
  SELECT clips.id
  FROM clips
  LEFT JOIN clip_search_map ON clip_search_map.clip_id = clips.id
  WHERE clips.is_secret = 0
    AND clips.deleted = 0
    AND clip_search_map.clip_id IS NULL
  ORDER BY clips.id;

-- Rebuild the rows inside the existing trigram virtual table with explicit
-- rowids.  This removes legacy secret/deleted/orphan/duplicate rows and repairs
-- stale or missing indexed content without changing the tokenizer.
DELETE FROM clips_fts;
INSERT INTO clips_fts(rowid, id, content)
  SELECT clip_search_map.search_id, clips.id, clips.content
  FROM clip_search_map
  JOIN clips ON clips.id = clip_search_map.clip_id;

-- These partial indexes let the exact recent probe scan in the same total
-- order exposed by the repository, without sorting the full FTS match set.
CREATE INDEX idx_clips_public_search_recent
  ON clips(last_seen_at DESC, id DESC)
  WHERE is_secret = 0 AND deleted = 0;

CREATE INDEX idx_clips_public_list_recent
  ON clips(pinned DESC, last_seen_at DESC, id DESC)
  WHERE is_secret = 0 AND deleted = 0;

-- This schema-9 trigger supersedes the schema-1 store-only note for deletion:
-- removing a map row is the single cleanup operation for normal soft deletes
-- and physical FK cascades.  Delete only by the indexed rowid: id is UNINDEXED
-- in clips_fts, so legacy orphan/duplicate cleanup belongs to the one-time
-- startup repair rather than every normal delete.
CREATE TRIGGER clip_search_map_delete_fts
AFTER DELETE ON clip_search_map
BEGIN
  DELETE FROM clips_fts WHERE rowid = OLD.search_id;
END;
