-- Keep orphan reconciliation truly bounded.  A persisted keyset cursor lets
-- each maintenance pass inspect only a fixed number of eligible clips instead
-- of re-running an O(N) anti-join when there are no orphans.

CREATE TABLE obsidian_reconcile_state (
  singleton       INTEGER PRIMARY KEY CHECK(singleton = 1),
  last_created_at TEXT NOT NULL,
  last_clip_id    TEXT NOT NULL,
  cleanup_updated_at TEXT NOT NULL,
  cleanup_clip_id TEXT NOT NULL
);

INSERT INTO obsidian_reconcile_state(
  singleton, last_created_at, last_clip_id, cleanup_updated_at, cleanup_clip_id
)
VALUES (1, '', '', '', '');

CREATE INDEX idx_clips_obsidian_reconcile
  ON clips(created_at, id)
  WHERE obsidian_path IS NULL AND is_secret = 0 AND deleted = 0;

CREATE INDEX idx_obsidian_queue_cleanup
  ON obsidian_queue(state, updated_at, clip_id);

-- Claimed states carry a random ownership token (claimed:<token>).  Index only
-- that lexical range so lease recovery never walks the much larger pending set.
CREATE INDEX idx_obsidian_queue_claim_expiry
  ON obsidian_queue(next_attempt_at, clip_id)
  WHERE state >= 'claimed:' AND state < 'claimed;';
