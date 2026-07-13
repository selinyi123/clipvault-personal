"""Reproducible, stdlib-only Desktop performance baseline for ClipVault.

The command-line path always creates a fresh SQLite database inside a private
temporary directory.  It never opens or modifies a user's ClipVault database.
Dataset construction is reported separately and is not included in operation
latencies.

This tool is a regression baseline, not a hardware-independent SLA verifier.
In particular, the sync measurement covers local pagination/serialization and
does not include LAN latency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "desktop"
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from clipvault.api.handlers import Api
from clipvault.config import Config
from clipvault.pipeline.ingest import STATUS_NEW, ingest
from clipvault.service import ClipVaultService
from clipvault.store import db
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.outbox_repo import OutboxRepo
from clipvault.sync import engine as sync_engine


DEFAULT_ROWS = 100_000
DEFAULT_ITERATIONS = 7
DEFAULT_INGEST_SAMPLES = 20
SYNC_EVENT_COUNT = 100
_SEED_BATCH_SIZE = 1_000
_MIN_ROWS = 10
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")

# These intentionally generous ceilings are for the 10k hosted-CI smoke test.
# They catch order-of-magnitude regressions while tolerating shared-runner
# scheduling noise.  They are not the product budgets from ARCHITECTURE.md.
CI_REGRESSION_CEILINGS_MS = {
    "search_cjk_1_char": 250.0,
    "search_cjk_2_char": 250.0,
    "search_cjk_3_char_common": 300.0,
    "suggest_request": 300.0,
    "ingest_new": 500.0,
    "sync_pull_100_events": 1_000.0,
}

# Reference values copied from docs/ARCHITECTURE.md.  Results from this tool
# must not be represented as proof of those end-to-end budgets.
ARCHITECTURE_REFERENCE_BUDGETS_MS = {
    "search": 50.0,
    "suggest": 30.0,
    "ingest": 100.0,
    "sync_100_lan": 2_000.0,
}


def _percentile(samples: list[float], fraction: float) -> float:
    """Nearest-rank percentile, suitable for the small fixed sample sets here."""

    if not samples:
        raise ValueError("samples must not be empty")
    ordered = sorted(samples)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _summary(samples: list[float]) -> dict:
    return {
        "samples": len(samples),
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(_percentile(samples, 0.95), 3),
        "max_ms": round(max(samples), 3),
    }


def _source_revision() -> str:
    """Return a non-sensitive source HEAD revision when it is available."""

    github_sha = os.environ.get("GITHUB_SHA", "").strip()
    if _SHA_RE.fullmatch(github_sha):
        return github_sha.lower()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    revision = result.stdout.strip()
    return revision.lower() if _SHA_RE.fullmatch(revision) else "unknown"


def _source_tree_state() -> str:
    """Report whether the Git worktree matches HEAD without exposing paths."""

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return "dirty" if result.stdout else "clean"


def _measure(operation: Callable[[], None], iterations: int) -> dict:
    # Untimed warm-up pays one-time statement preparation and page-cache costs.
    operation()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        operation()
        samples.append((time.perf_counter_ns() - started) / 1_000_000.0)
    return _summary(samples)


def _clip_seed_row(index: int, *, seen: str, created: str) -> tuple[tuple, tuple]:
    clip_id = f"P{index:025d}"
    content = f"记录 {index:06d} 服务器部署文档 alpha beta"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    # New clips start with times_seen=1 in production. Keep the suggestion
    # population deliberately sparse so a recency-only index cannot hide an
    # O(N) residual eligibility scan. The first ten rows keep small CLI runs
    # capable of exercising a real ten-result suggestion response.
    times_seen = 3 if index < 10 or index % 1_000 == 0 else 1
    clip = (
        clip_id,
        content,
        content_hash,
        "text",
        0,
        None,
        "[]",
        0,
        None,
        "performance-baseline",
        None,
        created,
        seen,
        times_seen,
        0,
        0,
        0,
        None,
        None,
    )
    return clip, (clip_id, content)


def _seed_dataset(conn: sqlite3.Connection, rows: int) -> None:
    """Populate a deterministic public dataset without timing setup work.

    Direct inserts are intentional: the steady-state operations are measured on
    a populated database without making CI spend time benchmarking data setup.
    Both clips and FTS rows are inserted so store invariants match production.
    """

    insert_clip = (
        "INSERT INTO clips ("
        "id, content, content_hash, content_type, is_secret, secret_level, "
        "secret_reasons, released, released_at, source_device, source_app, "
        "created_at, last_seen_at, times_seen, pinned, favorite, deleted, "
        "obsidian_path, backed_up_at"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    insert_fts = "INSERT INTO clips_fts(id, content) VALUES (?, ?)"
    reference_now = datetime.now(timezone.utc).replace(microsecond=0)
    seen_values = [
        (reference_now - timedelta(days=day)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for day in range(28)
    ]
    created = (reference_now - timedelta(days=28)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for start in range(0, rows, _SEED_BATCH_SIZE):
        clips = []
        fts_rows = []
        for index in range(start, min(rows, start + _SEED_BATCH_SIZE)):
            clip, fts = _clip_seed_row(
                index,
                seen=seen_values[index % len(seen_values)],
                created=created,
            )
            clips.append(clip)
            fts_rows.append(fts)
        conn.executemany(insert_clip, clips)
        conn.executemany(insert_fts, fts_rows)

    # /suggest is deliberately capped at 500 Memory rows.  Populate that cap so
    # the request benchmark includes Secret Guard defence-in-depth scanning and
    # candidate construction, not only the clips query.
    memory_count = min(rows, 500)
    memory_rows = [
        (
            f"M{index:025d}",
            "command",
            f"git command {index}",
            None,
            0,
            index,
            None,
            "manual",
            created,
            0,
        )
        for index in range(memory_count)
    ]
    conn.executemany(
        "INSERT INTO memory_items ("
        "id, kind, text, label, pinned, use_count, last_used_at, source, "
        "created_at, deleted"
        ") VALUES (?,?,?,?,?,?,?,?,?,?)",
        memory_rows,
    )
    conn.commit()


def _expect_result_count(operation: Callable[[], list], expected: int) -> None:
    result = operation()
    if len(result) != expected:
        raise RuntimeError(f"benchmark correctness check failed: {len(result)} != {expected}")


def _measure_ingest(conn: sqlite3.Connection, rows: int, samples: int) -> dict:
    warmup = ingest(
        conn,
        f"performance baseline new item rows {rows} warmup",
        source_device="performance-baseline",
    )
    if warmup.status != STATUS_NEW:
        raise RuntimeError(f"ingest benchmark warm-up expected new, got {warmup.status}")
    timings = []
    for index in range(samples):
        text = f"performance baseline new item rows {rows} sample {index}"
        started = time.perf_counter_ns()
        outcome = ingest(conn, text, source_device="performance-baseline")
        timings.append((time.perf_counter_ns() - started) / 1_000_000.0)
        if outcome.status != STATUS_NEW:
            raise RuntimeError(f"ingest benchmark expected new, got {outcome.status}")
    return _summary(timings)


def _suggest_request(api: Api) -> None:
    status, body = api.suggest({"prefix": "服务器", "limit": "10"})
    suggestions = body.get("suggestions") if isinstance(body, dict) else None
    if status != 200 or not isinstance(suggestions, list) or len(suggestions) != 10:
        raise RuntimeError("suggest benchmark did not exercise a 10-result success path")


def _seed_sync_events(conn: sqlite3.Connection) -> int:
    start_seq = OutboxRepo(conn).max_seq()
    rows = [
        (
            "clip_meta",
            json.dumps(
                {
                    "content_hash": hashlib.sha256(f"sync-{index}".encode()).hexdigest(),
                    "patch": {"pinned": True},
                    "ts": "2026-06-01T12:00:00Z",
                },
                ensure_ascii=False,
            ),
            "2026-06-01T12:00:00Z",
        )
        for index in range(SYNC_EVENT_COUNT)
    ]
    conn.executemany(
        "INSERT INTO sync_outbox(kind, payload, created_at) VALUES (?,?,?)", rows
    )
    conn.commit()
    return start_seq


def _pull_100_events(conn: sqlite3.Connection, start_seq: int) -> None:
    cursor = start_seq
    received = 0
    pages = 0
    while received < SYNC_EVENT_COUNT:
        page = sync_engine.build_pull(conn, cursor)
        events = page["events"]
        if not events:
            raise RuntimeError("sync benchmark stopped before 100 events")
        received += len(events)
        cursor = page["next_seq"]
        pages += 1
        if not page["has_more"]:
            break
    if received != SYNC_EVENT_COUNT:
        raise RuntimeError(f"sync benchmark received {received}, expected 100")
    expected_pages = math.ceil(SYNC_EVENT_COUNT / sync_engine.SYNC_PULL_FETCH_LIMIT)
    if pages != expected_pages:
        raise RuntimeError(f"sync benchmark used {pages} pages, expected {expected_pages}")


def run_benchmark(
    *,
    rows: int = DEFAULT_ROWS,
    iterations: int = DEFAULT_ITERATIONS,
    ingest_samples: int = DEFAULT_INGEST_SAMPLES,
    database_path: Path | None = None,
) -> dict:
    if rows < _MIN_ROWS:
        raise ValueError(f"rows must be at least {_MIN_ROWS}")
    if iterations < 3:
        raise ValueError("iterations must be at least 3")
    if ingest_samples < 5:
        raise ValueError("ingest_samples must be at least 5")
    if database_path is not None and (
        database_path.exists() or database_path.is_symlink()
    ):
        raise ValueError("refusing to overwrite an existing benchmark database path")

    connection_target = ":memory:" if database_path is None else str(database_path)
    conn = db.connect(connection_target)
    try:
        db.migrate(conn)
        setup_started = time.perf_counter_ns()
        _seed_dataset(conn, rows)
        setup_seconds = (time.perf_counter_ns() - setup_started) / 1_000_000_000.0

        repo = ClipsRepo(conn)
        search_1 = lambda: repo.search_fts("部")
        search_2 = lambda: repo.search_fts("部署")
        search_3 = lambda: repo.search_fts("服务器")
        for operation in (search_1, search_2, search_3):
            _expect_result_count(operation, min(rows, 50))

        config = Config(
            device_id="performance-baseline",
            device_name="performance-baseline",
            db_path=connection_target,
            max_clip_bytes=1_048_576,
            poll_ms=500,
            vault_path=str((database_path.parent if database_path else Path(tempfile.gettempdir())) / "vault"),
        )
        api = Api(ClipVaultService(conn, config))

        metrics = {
            "search_cjk_1_char": _measure(search_1, iterations),
            "search_cjk_2_char": _measure(search_2, iterations),
            "search_cjk_3_char_common": _measure(search_3, iterations),
            "suggest_request": _measure(
                lambda: _suggest_request(api),
                iterations,
            ),
            "ingest_new": _measure_ingest(conn, rows, ingest_samples),
        }

        sync_start_seq = _seed_sync_events(conn)
        metrics["sync_pull_100_events"] = _measure(
            lambda: _pull_100_events(conn, sync_start_seq), iterations
        )

        return {
            "report_schema_version": 1,
            "source_revision": _source_revision(),
            "source_tree_state": _source_tree_state(),
            "schema_version": db.schema_version(conn),
            "rows": rows,
            "iterations": iterations,
            "ingest_samples": ingest_samples,
            "setup_seconds_excluded": round(setup_seconds, 3),
            "database": {
                "kind": "temporary_file" if database_path is not None else "memory",
                "user_database_touched": False,
            },
            "runtime": {
                "python": platform.python_version(),
                "sqlite": sqlite3.sqlite_version,
                "platform": platform.platform(),
            },
            "metrics": metrics,
            "ci_regression_ceilings_ms": CI_REGRESSION_CEILINGS_MS,
            "architecture_reference_budgets_ms": ARCHITECTURE_REFERENCE_BUDGETS_MS,
            "interpretation": {
                "regression_ceiling_is_sla": False,
                "sync_includes_lan": False,
                "setup_is_timed_operation": False,
            },
        }
    finally:
        conn.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--ingest-samples", type=int, default=DEFAULT_INGEST_SAMPLES)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.rows < _MIN_ROWS or args.rows > 1_000_000:
        raise SystemExit(f"--rows must be between {_MIN_ROWS} and 1000000")
    if args.iterations < 3 or args.iterations > 100:
        raise SystemExit("--iterations must be between 3 and 100")
    if args.ingest_samples < 5 or args.ingest_samples > 1_000:
        raise SystemExit("--ingest-samples must be between 5 and 1000")

    # No database path option is accepted: the CLI can only touch the fresh
    # temporary file created here.  The path itself is not emitted in JSON.
    with tempfile.TemporaryDirectory(prefix="clipvault-perf-") as temp_dir:
        report = run_benchmark(
            rows=args.rows,
            iterations=args.iterations,
            ingest_samples=args.ingest_samples,
            database_path=Path(temp_dir) / "benchmark.sqlite3",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
