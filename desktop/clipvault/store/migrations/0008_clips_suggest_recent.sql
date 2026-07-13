-- SUG-1: bound the recent eligible public-clip candidate scan used by
-- /api/suggest.  The complete eligibility predicate is part of the index:
-- indexing every public row can make sparse suggestion sets slower because
-- SQLite scans by recency and filters most rows after the lookup.
--
-- The id suffix is the deterministic tie-break used by suggest_candidates()
-- when multiple rows share the same second-precision last_seen_at value.
CREATE INDEX idx_clips_suggest_recent
  ON clips(last_seen_at DESC, id DESC)
  WHERE is_secret = 0
    AND deleted = 0
    AND (favorite = 1 OR times_seen >= 3);
