-- DB-1 v1 (CONTRACTS §9)

CREATE TABLE schema_meta (version INTEGER NOT NULL);

CREATE TABLE clips (
  id            TEXT PRIMARY KEY,
  content       TEXT NOT NULL,
  content_hash  TEXT NOT NULL UNIQUE,
  content_type  TEXT NOT NULL DEFAULT 'text',
  is_secret     INTEGER NOT NULL DEFAULT 0,
  secret_level  TEXT,
  secret_reasons TEXT,
  released      INTEGER NOT NULL DEFAULT 0,
  released_at   TEXT,
  source_device TEXT NOT NULL,
  source_app    TEXT,
  created_at    TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  times_seen    INTEGER NOT NULL DEFAULT 1,
  pinned        INTEGER NOT NULL DEFAULT 0,
  favorite      INTEGER NOT NULL DEFAULT 0,
  deleted       INTEGER NOT NULL DEFAULT 0,
  obsidian_path TEXT,
  backed_up_at  TEXT
);
CREATE INDEX idx_clips_created ON clips(created_at DESC);
CREATE INDEX idx_clips_type    ON clips(content_type);

-- Maintained by store-layer code, never by triggers.
-- Invariant: rows with is_secret=1 or deleted=1 must never exist here.
CREATE VIRTUAL TABLE clips_fts USING fts5(id UNINDEXED, content);

CREATE TABLE memory_items (
  id           TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,
  text         TEXT NOT NULL,
  label        TEXT,
  pinned       INTEGER NOT NULL DEFAULT 0,
  use_count    INTEGER NOT NULL DEFAULT 0,
  last_used_at TEXT,
  source       TEXT NOT NULL DEFAULT 'manual',
  created_at   TEXT NOT NULL,
  deleted      INTEGER NOT NULL DEFAULT 0,
  UNIQUE(kind, text)
);

CREATE TABLE sync_outbox (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  kind       TEXT NOT NULL,
  payload    TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE sync_peers (
  device_id    TEXT PRIMARY KEY,
  device_name  TEXT,
  token_hash   TEXT NOT NULL,
  my_acked_seq INTEGER NOT NULL DEFAULT 0,
  peer_cursor  INTEGER NOT NULL DEFAULT 0,
  paired_at    TEXT NOT NULL,
  last_seen_at TEXT
);

CREATE TABLE backup_queue (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  clip_id    TEXT NOT NULL UNIQUE,
  state      TEXT NOT NULL DEFAULT 'pending',
  attempts   INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TEXT NOT NULL,
  done_at    TEXT
);
