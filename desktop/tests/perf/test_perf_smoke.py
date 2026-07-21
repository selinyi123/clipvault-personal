"""10k hosted-CI performance smoke tests.

The thresholds below are deliberately broad regression ceilings.  They catch
large complexity regressions without treating a shared GitHub runner as an SLA
measurement lab.  Dataset setup is excluded by the benchmark helper.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parents[3] / "tools" / "benchmark_desktop_perf.py"
_spec = importlib.util.spec_from_file_location("benchmark_desktop_perf_smoke", _TOOL)
benchmark = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = benchmark
_spec.loader.exec_module(benchmark)


@pytest.fixture(scope="module")
def report():
    return benchmark.run_benchmark(
        rows=10_000,
        iterations=7,
        ingest_samples=20,
        database_path=None,
    )


def test_10k_setup_is_excluded_and_database_is_ephemeral(report):
    assert report["rows"] == 10_000
    assert report["database"] == {
        "kind": "memory",
        "user_database_touched": False,
    }
    assert report["interpretation"] == {
        "regression_ceiling_is_sla": False,
        "sync_includes_lan": False,
        "setup_is_timed_operation": False,
    }


@pytest.mark.parametrize(
    ("metric", "statistic"),
    [
        ("api_status_backlog", "median_ms"),
        ("api_search_cjk_1_char", "median_ms"),
        ("api_search_cjk_2_char", "median_ms"),
        ("api_search_cjk_2_char_tail", "median_ms"),
        ("api_search_cjk_2_char_none", "median_ms"),
        ("api_search_cjk_3_char_common", "median_ms"),
        ("api_search_trigram_old_skew", "median_ms"),
        ("api_search_trigram_medium", "median_ms"),
        ("api_search_trigram_rare", "median_ms"),
        ("api_search_trigram_none", "median_ms"),
        ("fts_clean_drift_audit", "median_ms"),
        ("suggest_request", "median_ms"),
        # Ingest gets enough samples for a meaningful nearest-rank p95.
        ("ingest_new", "p95_ms"),
        ("sync_pull_100_events", "median_ms"),
    ],
)
def test_10k_regression_ceiling(report, metric, statistic):
    measured = report["metrics"][metric][statistic]
    ceiling = report["ci_regression_ceilings_ms"][metric]
    assert measured <= ceiling, (
        f"{metric} {statistic} {measured:.3f}ms exceeded the broad CI "
        f"regression ceiling {ceiling:.3f}ms; this ceiling is not an SLA"
    )


def test_every_metric_reports_warm_sample_statistics(report):
    for metric in report["metrics"].values():
        assert metric["samples"] >= 7
        assert 0 <= metric["median_ms"] <= metric["p95_ms"] <= metric["max_ms"]
