-- SYNC-2 / CONTRACTS §5.2: per-memory-item last-change timestamp, so
-- memory_delete is applied as last-writer-wins (a stale delete must not remove
-- a locally newer item). Mirrors clip_meta_ts; keyed by (kind, text) like the
-- memory_items uniqueness.

CREATE TABLE memory_meta_ts (
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  ts   TEXT NOT NULL,
  PRIMARY KEY (kind, text)
);
