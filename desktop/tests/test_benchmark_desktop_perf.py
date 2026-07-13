import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "benchmark_desktop_perf.py"
_spec = importlib.util.spec_from_file_location("benchmark_desktop_perf_tool", _TOOL)
benchmark = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = benchmark
_spec.loader.exec_module(benchmark)


def test_small_cli_smoke_uses_temporary_database_and_emits_json(capsys):
    assert benchmark.main([
        "--rows", "200",
        "--iterations", "3",
        "--ingest-samples", "5",
    ]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["rows"] == 200
    assert report["report_schema_version"] == 2
    assert report["source_revision"] == "unknown" or re.fullmatch(
        r"[0-9a-f]{40}", report["source_revision"]
    )
    assert report["source_tree_state"] in {"clean", "dirty", "unknown"}
    assert report["database"] == {
        "kind": "temporary_file",
        "user_database_touched": False,
    }
    assert set(report["metrics"]) == set(benchmark.CI_REGRESSION_CEILINGS_MS)
    assert "path" not in report["database"]


def test_library_entrypoint_refuses_existing_database_path(tmp_path):
    existing = tmp_path / "personal.sqlite3"
    existing.write_bytes(b"do not overwrite")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        benchmark.run_benchmark(
            rows=10,
            iterations=3,
            ingest_samples=5,
            database_path=existing,
        )

    assert existing.read_bytes() == b"do not overwrite"


def test_library_entrypoint_rejects_too_few_rows_for_suggestion_path():
    with pytest.raises(ValueError, match="rows must be at least 10"):
        benchmark.run_benchmark(
            rows=9,
            iterations=3,
            ingest_samples=5,
        )


def test_tree_state_is_aggregate_only():
    assert benchmark._source_tree_state() in {"clean", "dirty", "unknown"}


def test_synthetic_suggestion_population_is_sparse_but_has_ten_results():
    rows = [
        benchmark._clip_seed_row(
            index,
            seen="2026-07-13T00:00:00Z",
            created="2026-07-01T00:00:00Z",
        )[0]
        for index in range(10_000)
    ]
    eligible = sum(row[13] >= 3 or bool(row[15]) for row in rows)

    assert 10 <= eligible < 50


@pytest.mark.parametrize(
    "argv",
    [
        ["--rows", "9"],
        ["--rows", "1000001"],
        ["--iterations", "2"],
        ["--ingest-samples", "4"],
    ],
)
def test_cli_rejects_unsafe_or_non_representative_sizes(argv):
    with pytest.raises(SystemExit):
        benchmark.main(argv)
