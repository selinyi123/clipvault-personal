-- R000: explicit, bounded Obsidian retry queue.
-- Content-safe: stores clip ids and write metadata only, never clip content.

CREATE TABLE obsidian_queue (
  clip_id TEXT PRIMARY KEY,
  state TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT NOT NULL,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(clip_id) REFERENCES clips(id) ON DELETE CASCADE
);

CREATE INDEX idx_obsidian_queue_ready
  ON obsidian_queue(state, next_attempt_at, created_at);

INSERT OR IGNORE INTO obsidian_queue(
  clip_id, state, attempts, next_attempt_at, created_at, updated_at
)
SELECT id, 'pending', 0, created_at, created_at, created_at
FROM clips
WHERE obsidian_path IS NULL AND is_secret = 0 AND deleted = 0;
