-- SYNC-2 / v1.8: make clip meta LWW per-field. A single coarse timestamp per
-- clip let a newer change to one field (e.g. un-pin) be masked by an older change
-- to another (e.g. favorite) that had bumped the shared timestamp. Track the
-- timestamp per (content_hash, field) instead. The existing coarse ts seeds every
-- field, so nothing previously rejected becomes accepted retroactively.

CREATE TABLE clip_meta_ts_v2 (
  content_hash TEXT NOT NULL,
  field        TEXT NOT NULL,
  ts           TEXT NOT NULL,
  PRIMARY KEY (content_hash, field)
);

INSERT INTO clip_meta_ts_v2 (content_hash, field, ts)
  SELECT m.content_hash, f.field, m.ts
  FROM clip_meta_ts AS m,
       (SELECT 'pinned' AS field UNION ALL SELECT 'favorite' UNION ALL SELECT 'deleted') AS f;

DROP TABLE clip_meta_ts;
ALTER TABLE clip_meta_ts_v2 RENAME TO clip_meta_ts;
