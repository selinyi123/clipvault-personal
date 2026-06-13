-- SYNC-2: per-clip last meta-change timestamp, for field-level LWW on
-- pin/favorite/delete across devices (CONTRACTS §5.2). Kept in a side table so
-- the clips schema (DB-1) is untouched.

CREATE TABLE clip_meta_ts (
  content_hash TEXT PRIMARY KEY,
  ts           TEXT NOT NULL
);
