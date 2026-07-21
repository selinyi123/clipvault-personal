# Desktop performance baseline

This document defines the regression measurements added during the R000
stability refactor. Schema and API contracts remain governed by
`docs/CONTRACTS.md`; this file does not redefine the performance budgets in
`docs/ARCHITECTURE.md`.

## Two different kinds of evidence

The automated 10k-row test is a **hosted-CI smoke test**. Its ceilings are
intentionally much wider than the product budgets because GitHub runners are
shared and can be pre-empted. A green smoke test means that no persistent,
order-of-magnitude regression was detected. It does **not** prove that a user
device meets an end-to-end latency target.

The 100k-row command is a **manual local benchmark**. It reports measurements
from the current machine as JSON. Results should record the commit, hardware,
power mode, storage type, and whether other heavy work was running. A local
result is useful release evidence only when those details and the exact command
are preserved; it is never a substitute for device QA or the Issue #36 Owner
gates.

The architecture reference budgets remain:

| Path | Reference budget | What this baseline can prove |
|---|---:|---|
| capture to durable database | `< 100ms` | steady-state Desktop ingest logic only |
| FTS search | `< 50ms` | SQLite query/materialisation only |
| local suggestion query | `< 30ms` | Desktop `/suggest` handler only; not Android IME |
| sync batch of 100 | `< 2s` on LAN | local pull pagination/JSON only; no network |

Do not relabel CI regression ceilings as these budgets.

## Automated 10k smoke

`desktop/tests/perf/test_perf_smoke.py` constructs 10,000 public clips and 500
Memory candidates in an in-memory migrated database. Dataset construction is
not timed. Direct seed inserts populate `clips`, `clip_search_map`, and
`clips_fts` with the same stable-rowid invariant as production; measured ingests
call the production `ClipVaultService.handle_clipboard_text` boundary with a
no-op worker notification, so they include queue/orchestration work without
performing Vault file I/O. Most synthetic clips retain the
production-default `times_seen=1`, while fewer than 0.5% are suggestion-
eligible. This sparse shape prevents a recency-only index from hiding a large
residual eligibility scan. Seed timestamps are anchored to the benchmark start
and spread across the preceding 28 days, so `/suggest` always exercises its
real recent-candidate path instead of becoming an empty fast path as a fixed
fixture date ages. Every 250th clip also contains a distributed medium-density
trigram token. At 100k rows this produces 400 matches and therefore measures
the exact map fallback between the common-probe and rare/no-match extremes.
Half of each 28-day seed cycle also contains `old-skew-token`, restricted to
days 14 through 27. At 10k rows that creates 4,998 historical matches while
the newest 256 public rows contain none, exercising the common-term path whose
matches are concentrated outside the bounded recent candidate window.
One unique two-character CJK marker is placed in the newest id of the oldest
timestamp bucket. Its API query requests one result and must inspect more than
95% of the ordered 10k population before returning that match; a separate absent two-character
marker exercises the complete no-match scan. The benchmark also runs the
production clean FTS drift audit against the full population and fails the
sample if that supposedly read-only check repairs anything.
All three adversarial additions run after the previously established search,
suggestion, ingest, sync, and status workloads, so their full scans cannot warm
the caches used by older report metrics.

Each repeated operation gets one untimed warm-up. Seven measured iterations
produce median, nearest-rank p95, and maximum values. New ingest uses one
untimed unique warm-up followed by 20 unique measured samples so its p95 is
meaningful. Search measurements call the real `Api.list_clips` handler and
validate status/result count before accepting a sample; they are handler
measurements, not HTTP/network timings. The suggestion measurement similarly
verifies a successful ten-result response. CI asserts the median except for
ingest p95.

| Metric | CI regression ceiling |
|---|---:|
| one-character CJK API `LIKE` fallback | 250ms median |
| common two-character CJK API `LIKE` fallback | 250ms median |
| near-tail two-character CJK API `LIKE` fallback | 300ms median |
| no-match two-character CJK API `LIKE` fallback | 300ms median |
| common three-character API trigram query | 300ms median |
| historical-skew common trigram query | 300ms median |
| medium-density trigram query | 300ms median |
| rare trigram API query | 300ms median |
| no-match trigram API query | 300ms median |
| clean FTS drift audit | 1000ms median |
| full Desktop `/suggest` request | 300ms median |
| one new ingest on the populated database | 500ms p95 |
| local pagination/serialization of 100 small sync events | 1000ms median |

These ceilings are deliberately loose. A failure should trigger a rerun and
query/profile inspection, not an automatic relaxation of the threshold.

Run only this smoke:

```powershell
cd desktop
.\.venv\Scripts\python.exe -m pytest -q tests\perf\test_perf_smoke.py
```

The existing full `python -m pytest -q` command discovers it automatically, so
both CI and the release-candidate dry run execute the smoke without workflow or
dependency changes.

## Manual 100k benchmark

From the repository root:

```powershell
desktop\.venv\Scripts\python.exe tools\benchmark_desktop_perf.py --rows 100000
```

The CLI accepts `--rows`, `--iterations`, and `--ingest-samples` for controlled
smaller checks. It intentionally accepts no database path. Every invocation
creates a fresh SQLite/WAL database under a private `TemporaryDirectory`, closes
it, deletes it, and prints no temporary path. It cannot open or overwrite a
user's ClipVault database.

Report schema version 2 names search metrics as `api_search_*` because version 1
measured only the repo helper and did not exercise the Web UI/API path. The
versioned JSON separates `setup_seconds_excluded` from operation latencies
and records the Git HEAD revision when available, plus a path-free
`source_tree_state` (`clean`, `dirty`, or `unknown`) and the Python, SQLite, and
platform versions. A revision with `dirty` state is not an exact reproducible
source snapshot and must not be cited as one. If Git and `GITHUB_SHA` are both
unavailable, the revision is explicitly `unknown` rather than guessed.
Redirect stdout if a durable report is needed:

```powershell
desktop\.venv\Scripts\python.exe tools\benchmark_desktop_perf.py --rows 100000 `
  > desktop-perf-100k.json
```

Review median and p95 together. Investigate a repeatable miss before changing
production SQL. A performance optimisation belongs in a later, separately
reviewable PR with before/after output from this tool.

The JSON field `ci_regression_profile_rows=10000` binds the accompanying
`ci_regression_ceilings_ms` to the hosted-CI dataset. Reports from the default
100k manual run still include those reference ceilings for comparison, but
must not be evaluated against them as a pass/fail profile.

## Known risks this baseline makes visible

- One- and two-character substring search uses leading-wildcard `LIKE`. Schema 9
  can scan the public result-order index and stop after a full page for common
  terms, but a rare or absent short substring can still inspect the full set.
  `api_search_cjk_2_char_tail` and `api_search_cjk_2_char_none` keep both
  adversarial shapes visible without changing search semantics.
- Startup checks the complete stable-map/FTS bijection before the API binds.
  `fts_clean_drift_audit` measures the production clean check and rejects an
  unexpected repair, but the broad hosted-CI ceiling is not an API readiness
  SLA and does not cover a full rebuild of a drifted index.
- Schema 9 gives public FTS rows stable integer IDs. Terms with more than 4096
  matches can use a bounded 256-candidate exact probe; medium/rare/no-match
  terms and any probe that cannot fill the requested page use the exact
  FTS-first fallback. A bounded literal-LIKE hint checks only the first 4,096
  characters of each of at most 256 recent candidates, and skips the 256
  per-candidate FTS checks when that window clearly cannot fill a page. LIKE
  never supplies result rows, so prefix or Unicode false negatives safely use
  the exact fallback and FTS search semantics remain unchanged. All
  paths retain explicit Secret Guard filters and one read snapshot. Type/cursor-
  filtered and oversized repo requests skip the probe so filtering cannot hide
  an unbounded pre-sort behind its candidate limit. A high-frequency term whose
  matches are concentrated in much older records still pays the exact fallback
  sort. `api_search_trigram_old_skew` makes that adversarial distribution visible
  in both the 10k smoke and 100k manual report; its hosted-CI ceiling is not the
  architecture reference budget.
- Suggestion output is bounded, and schema v8 uses a partial index containing
  only eligible, non-secret, non-deleted clips in deterministic recency order.
  Memory ordering has no matching composite index; legacy-secret defence can
  also page past unsafe Memory rows before it finds 500 safe candidates.
- Public sync pull permits 100 events but deliberately materialises at most eight
  outbox rows per internal page. The 100-event metric therefore exercises the
  complete multi-page loop, but only with small events.
- Search/list results materialise full clip content. The path hint inspects at
  most 1,048,576 characters across its 256 fixed-size prefixes, but SQLite may
  still touch pages containing larger stored values. Max-size clips and the
  worst-case escaped 1 MiB sync payload need separate manual memory/IO profiling;
  the lightweight CI dataset intentionally does not allocate them.

The first response to these risks is measurement. Do not add a bigram index,
change search semantics, or change sync pagination in the baseline PR.
