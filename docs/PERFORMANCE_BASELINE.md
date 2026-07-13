# Desktop performance baseline

This document defines the regression measurements added during the R000
stability refactor. It does not change product behaviour, storage schemas, API
contracts, or the performance budgets in `docs/ARCHITECTURE.md`.

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
not timed. Direct seed inserts populate both `clips` and `clips_fts`; measured
ingests still use the production pipeline. Seed timestamps are anchored to the
benchmark start and spread across the preceding 28 days, so `/suggest` always
exercises its real recent-candidate path instead of becoming an empty fast path
as a fixed fixture date ages.

Each repeated operation gets one untimed warm-up. Seven measured iterations
produce median, nearest-rank p95, and maximum values. New ingest uses one
untimed unique warm-up followed by 20 unique measured samples so its p95 is
meaningful. The suggestion measurement also verifies a successful ten-result
response before accepting a sample. CI asserts the median except for ingest
p95.

| Metric | CI regression ceiling |
|---|---:|
| one-character CJK `LIKE` fallback | 250ms median |
| two-character CJK `LIKE` fallback | 250ms median |
| common three-character trigram query | 300ms median |
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

The versioned JSON separates `setup_seconds_excluded` from operation latencies
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

## Known risks this baseline makes visible

- One- and two-character substring search uses leading-wildcard `LIKE`, which is
  necessarily linear in clip count.
- A common trigram can return a large FTS row-id set; the current query then
  resolves clips and sorts by `last_seen_at`, so three-character FTS is not
  automatically faster for high-frequency terms.
- Suggestion output is bounded, but the current clip filter/order and Memory
  order have no matching composite indexes. Legacy-secret defence can also page
  past unsafe Memory rows before it finds 500 safe candidates.
- Public sync pull permits 100 events but deliberately materialises at most eight
  outbox rows per internal page. The 100-event metric therefore exercises the
  complete multi-page loop, but only with small events.
- Search/list results materialise full clip content. Max-size clips and the
  worst-case escaped 1 MiB sync payload need separate manual memory/IO profiling;
  the lightweight CI dataset intentionally does not allocate them.

The first response to these risks is measurement. Do not add a bigram index,
change search semantics, or change sync pagination in the baseline PR.
