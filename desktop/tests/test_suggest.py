"""S010 gates F1-F10: SUG-1 scoring (pure) + /api/suggest integration."""

from datetime import datetime, timedelta, timezone

import pytest

from clipvault.api.handlers import Api
from clipvault.config import Config
from clipvault.core import suggest as sg
from clipvault.service import ClipVaultService
from clipvault.store.memory_repo import MemoryRepo

NOW = datetime(2026, 6, 13, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def cand(text, **kw):
    return sg.Candidate(id=text, kind="term", text=text, **kw)


def test_f1_prefix_beats_substring_and_drops_nonmatch():
    w = sg.Weights()
    cands = [cand("github clone"), cand("see github here"), cand("unrelated")]
    ranked = sg.rank(cands, "github", None, w, NOW)
    texts = [c.text for c, _ in ranked]
    assert texts == ["github clone", "see github here"]  # prefix first, nonmatch dropped


def test_f2_empty_query_keeps_all_sorted_by_freq():
    w = sg.Weights()
    cands = [
        cand("a", use_count=1, last_used_at=_iso(NOW)),
        cand("b", use_count=50, last_used_at=_iso(NOW)),
    ]
    ranked = sg.rank(cands, "", None, w, NOW)
    assert [c.text for c, _ in ranked] == ["b", "a"]  # nothing dropped, higher freq first


def test_f3_pinned_floats_to_top():
    w = sg.Weights()
    cands = [
        cand("frequent", use_count=100, last_used_at=_iso(NOW)),
        cand("pinned one", pinned=True, use_count=0),
    ]
    ranked = sg.rank(cands, "", None, w, NOW)
    assert ranked[0][0].text == "pinned one"


def test_f4_recency_decay():
    w = sg.Weights(half_life_days=14.0)
    recent = cand("recent", use_count=10, last_used_at=_iso(NOW - timedelta(days=1)))
    stale = cand("stale", use_count=10, last_used_at=_iso(NOW - timedelta(days=120)))
    ranked = sg.rank([stale, recent], "", None, w, NOW)
    assert ranked[0][0].text == "recent"
    assert sg.score(recent, "", None, w, NOW) > sg.score(stale, "", None, w, NOW)


def test_f5_app_bonus():
    w = sg.Weights()
    base = cand("deploy", use_count=5, last_used_at=_iso(NOW))
    appd = cand("deploy", use_count=5, last_used_at=_iso(NOW), source_app="wt.exe")
    assert sg.score(appd, "", "wt.exe", w, NOW) > sg.score(base, "", "wt.exe", w, NOW)


def test_f6_tie_break_by_last_used():
    w = sg.Weights(freq=0.0)  # neutralize freq so scores tie on match only
    a = cand("xterm", last_used_at=_iso(NOW - timedelta(days=5)))
    b = cand("xterm note", last_used_at=_iso(NOW))
    # both substring-match "term"? "xterm" prefix? no -> substr. equal match score.
    ranked = sg.rank([a, b], "term", None, w, NOW)
    # equal score -> later last_used_at first
    assert ranked[0][0].text == "xterm note"


def test_f10_weights_configurable():
    # Two non-pinned candidates: one prefix-matches "git", one substring-matches.
    # Freq neutralized so only match weights decide order.
    prefix_match = cand("git status")          # prefix of "git"
    substr_match = cand("see git here")         # substring only
    favor_prefix = sg.Weights(freq=0.0, prefix=2.0, substr=0.1)
    favor_substr = sg.Weights(freq=0.0, prefix=0.1, substr=2.0)
    assert sg.rank([substr_match, prefix_match], "git", None, favor_prefix, NOW)[0][0].text == "git status"
    assert sg.rank([prefix_match, substr_match], "git", None, favor_substr, NOW)[0][0].text == "see git here"


def test_f3b_pinned_tier_regardless_of_weight():
    # SUG-1.1: pinned is a hard tier; even a zero pinned-weight keeps it on top.
    w = sg.Weights(pinned=0.0)
    pinned = cand("p", pinned=True, use_count=0)
    plain = cand("q", use_count=99, last_used_at=_iso(NOW))
    assert sg.rank([plain, pinned], "", None, w, NOW)[0][0].text == "p"


def _origin_cand(cid, origin, **kw):
    return sg.Candidate(id=cid, kind="term", text=cid, origin=origin, **kw)


def test_f11_source_cap_keeps_minority_origin():
    # SUG-1.2: a flood of high-score memory candidates must not fully starve clips.
    w = sg.Weights()
    mems = [_origin_cand(f"m{i}", "memory", use_count=100, last_used_at=_iso(NOW)) for i in range(12)]
    clips = [_origin_cand(f"c{i}", "clip", use_count=1, last_used_at=_iso(NOW)) for i in range(4)]
    ranked = sg.rank(mems + clips, "", None, w, NOW, limit=8)
    origins = [c.origin for c, _ in ranked]
    assert len(ranked) == 8
    assert origins.count("clip") >= 2     # reserve = max(1, 8 // 4) = 2
    assert origins.count("memory") >= 2


def test_f11b_no_cap_when_all_fit():
    w = sg.Weights()
    mems = [_origin_cand(f"m{i}", "memory", use_count=10, last_used_at=_iso(NOW)) for i in range(3)]
    clip = _origin_cand("c0", "clip", use_count=100, last_used_at=_iso(NOW))
    ranked = sg.rank(mems + [clip], "", None, w, NOW, limit=10)
    assert len(ranked) == 4
    assert ranked[0][0].id == "c0"        # highest freq leads; no cap reshuffle


def test_f11c_single_origin_overflow_is_plain_topn():
    w = sg.Weights()
    mems = [_origin_cand(f"m{i}", "memory", use_count=100 - i, last_used_at=_iso(NOW)) for i in range(20)]
    ranked = sg.rank(mems, "", None, w, NOW, limit=5)
    assert [c.id for c, _ in ranked] == ["m0", "m1", "m2", "m3", "m4"]  # plain top-5


# --- integration: candidate assembly + API ---

@pytest.fixture
def cfg(tmp_path):
    return Config(device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", device_name="d",
                  db_path=":memory:", max_clip_bytes=1_048_576, poll_ms=500,
                  vault_path=str(tmp_path / "vault"))


@pytest.fixture
def api(conn, cfg):
    return Api(ClipVaultService(conn, cfg))


def test_f7_candidate_set_merges_memory_and_highuse_clips(api, conn):
    MemoryRepo(conn).upsert("command", "git pull origin main")
    # low-use clip (times_seen=1, not favorite) -> excluded
    api.create_clip({"content": "low use clip about git"})
    # high-use clip via repeated ingest (times_seen reaches 3)
    for _ in range(3):
        api.create_clip({"content": "git frequent command"})
    code, body = api.suggest({"prefix": "git"})
    assert code == 200
    texts = [s["text"] for s in body["suggestions"]]
    assert "git pull origin main" in texts          # memory candidate
    assert "git frequent command" in texts          # high-use clip candidate
    assert "low use clip about git" not in texts     # low-use excluded


def test_f8_memory_use_bumps_count(api, conn):
    _, created = api.create_memory({"kind": "term", "text": "kubectl"})
    mid = created["memory"]["id"]
    code, _ = api.use_memory(mid)
    assert code == 200
    assert MemoryRepo(conn).get(mid).use_count == 1


def test_f8_use_missing_404(api):
    code, _ = api.use_memory("01NOPE0000000000000000000")
    assert code == 404
