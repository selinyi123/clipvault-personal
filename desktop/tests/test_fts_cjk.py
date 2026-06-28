"""Migration 0005: FTS5 trigram tokenizer + LIKE fallback make substring search
work over CJK (and English) content. Before this, the default unicode61 tokenizer
treated a whole run of Chinese characters as one token, so e.g. searching "天气"
could not find "今天天气很好". Secrets must still never surface via either path."""

from clipvault.pipeline.ingest import ingest
from clipvault.store.clips_repo import ClipsRepo

FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


def _add(conn, text):
    return ingest(conn, text, source_device="test").clip


def _texts(clips):
    return [c.content for c in clips]


def test_cjk_phrase_search_3plus_chars(conn):
    _add(conn, "今天天气很好啊")
    repo = ClipsRepo(conn)
    assert _texts(repo.search_fts("天气很")) == ["今天天气很好啊"]   # FTS trigram path
    assert _texts(repo.search_fts("今天天")) == ["今天天气很好啊"]


def test_cjk_two_char_word_uses_like_fallback(conn):
    # The regression that motivated this fix: a 2-char CJK word (< trigram min).
    _add(conn, "服务器部署文档")
    repo = ClipsRepo(conn)
    assert _texts(repo.search_fts("部署")) == ["服务器部署文档"]
    assert _texts(repo.search_fts("文档")) == ["服务器部署文档"]


def test_english_substring_now_matches(conn):
    _add(conn, "deploy the server today")
    repo = ClipsRepo(conn)
    assert _texts(repo.search_fts("epl")) == ["deploy the server today"]   # mid-word substring
    assert _texts(repo.list_clips(query="erver")) == ["deploy the server today"]


def test_secret_never_leaks_via_short_query_like_path(conn):
    _add(conn, FAKE_AWS_KEY)            # quarantined: is_secret=1
    _add(conn, "make AK happen soon")   # public, also contains "AK"
    repo = ClipsRepo(conn)
    hits = _texts(repo.search_fts("AK"))            # 2-char -> LIKE fallback
    assert hits == ["make AK happen soon"]
    assert FAKE_AWS_KEY not in hits
    # default list view (secret=False) also excludes it
    assert FAKE_AWS_KEY not in _texts(repo.list_clips(query="AK"))


def test_secret_view_search_uses_like_not_empty_fts(conn):
    # Secrets are never in clips_fts, so a query in the secret view must use LIKE.
    _add(conn, FAKE_AWS_KEY)
    repo = ClipsRepo(conn)
    found = repo.list_clips(query="AKIA", secret=True)
    assert _texts(found) == [FAKE_AWS_KEY]


def test_special_chars_in_query_do_not_crash(conn):
    _add(conn, 'he said "100%_ok" loudly')
    _add(conn, "plain ascii text here")
    repo = ClipsRepo(conn)
    # FTS phrase path (>=3): embedded quote is escaped, matched literally
    assert _texts(repo.search_fts('"100%')) == ['he said "100%_ok" loudly']
    # LIKE path (<3): % is a literal, not a wildcard -> matches only the clip that
    # actually contains "%", never the plain one (which a bare % wildcard would).
    assert _texts(repo.search_fts("%")) == ['he said "100%_ok" loudly']
    assert _texts(repo.search_fts("0%")) == ['he said "100%_ok" loudly']


def test_deleted_clip_drops_out_of_search(conn):
    clip = _add(conn, "服务器部署文档")
    repo = ClipsRepo(conn)
    repo.set_flag(clip.id, "deleted", True)
    assert repo.search_fts("部署") == []        # LIKE path filters deleted
    assert repo.search_fts("服务器部") == []     # FTS path: removed from index
