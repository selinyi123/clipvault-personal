-- DB-1 search fix: the default FTS5 unicode61 tokenizer treats a run of CJK
-- characters as a single token, so substring/phrase search over Chinese clipboard
-- content never matched (e.g. searching "天气" could not find "今天天气很好").
-- Rebuild clips_fts with the trigram tokenizer — built into SQLite (FTS5), so no
-- new runtime dependency — which indexes 3-character sequences and supports CJK +
-- English substring search for queries of length >= 3. Queries of 1-2 characters
-- fall back to a LIKE scan in the repo (ClipsRepo). Secrets and deleted clips are
-- repopulated out of the index, preserving the gate-A/G1 invariant.
DROP TABLE IF EXISTS clips_fts;
CREATE VIRTUAL TABLE clips_fts USING fts5(id UNINDEXED, content, tokenize='trigram');
INSERT INTO clips_fts(id, content)
  SELECT id, content FROM clips WHERE is_secret = 0 AND deleted = 0;
