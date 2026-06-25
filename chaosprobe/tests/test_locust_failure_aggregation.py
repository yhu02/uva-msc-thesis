"""Tests for the Locust failure-class parsing and per-strategy
aggregation.

Aggregate errorRate tells us how often requests failed; failureClasses
tell us *why*.  Different failure classes imply different mechanisms
(timeout = network programming SLO; ConnectionError = conntrack churn;
HTTP 5xx = app circuit breaker), so the per-strategy roll-up
distinguishes one bad-iteration noise from a strategy-specific failure
mode.
"""

import csv

from chaosprobe.loadgen.runner import LoadStats, LocustRunner
from chaosprobe.orchestrator.run_phases import aggregate_iterations


def _make_failures_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Method", "Name", "Error", "Occurrences"])
        for row in rows:
            w.writerow(row)


class TestParseFailuresCSV:
    def test_returns_empty_list_for_missing_file(self, tmp_path):
        runner = LocustRunner.__new__(LocustRunner)
        out = runner._parse_failures_csv(str(tmp_path / "missing.csv"))
        assert out == []

    def test_parses_well_formed_rows(self, tmp_path):
        path = tmp_path / "stats_failures.csv"
        _make_failures_csv(
            path,
            [
                ("GET", "/cart", "ConnectionError(...)", 10),
                ("POST", "/checkout", "ReadTimeout(...)", 3),
                ("GET", "/", "HTTPError 503", 5),
            ],
        )
        runner = LocustRunner.__new__(LocustRunner)
        out = runner._parse_failures_csv(str(path))
        assert len(out) == 3
        assert out[0] == {
            "method": "GET",
            "name": "/cart",
            "error": "ConnectionError(...)",
            "occurrences": 10,
        }

    def test_malformed_occurrences_defaults_to_zero(self, tmp_path):
        path = tmp_path / "stats_failures.csv"
        _make_failures_csv(
            path,
            [
                ("GET", "/x", "Error", "not-an-int"),
                ("GET", "/y", "Error", 7),
            ],
        )
        runner = LocustRunner.__new__(LocustRunner)
        out = runner._parse_failures_csv(str(path))
        assert out[0]["occurrences"] == 0
        assert out[1]["occurrences"] == 7


class TestLoadStatsDict:
    def test_to_dict_includes_failure_classes(self):
        stats = LoadStats()
        stats.failure_classes = [{"method": "GET", "name": "/", "error": "X", "occurrences": 1}]
        d = stats.to_dict()
        assert d["failureClasses"] == [
            {"method": "GET", "name": "/", "error": "X", "occurrences": 1}
        ]

    def test_to_dict_empty_failure_classes_default(self):
        d = LoadStats().to_dict()
        assert d["failureClasses"] == []


def _iter_with_failures(failures, mean_r=1000.0, score=80.0):
    return {
        "resilienceScore": score,
        "verdict": "PASS",
        "metrics": {"recovery": {"summary": {"meanRecovery_ms": mean_r}}},
        "loadGeneration": {"stats": {"requestsPerSecond": 10.0, "failureClasses": failures}},
    }


class TestLoadFailureClassAggregation:
    def test_no_failures_no_block(self):
        agg = aggregate_iterations([_iter_with_failures([])])
        assert "loadFailureClasses" not in agg

    def test_totals_summed_across_iterations(self):
        iters = [
            _iter_with_failures(
                [
                    {"error": "ConnectionError", "name": "/cart", "occurrences": 5},
                    {"error": "Timeout", "name": "/", "occurrences": 2},
                ]
            ),
            _iter_with_failures([{"error": "ConnectionError", "name": "/cart", "occurrences": 8}]),
        ]
        agg = aggregate_iterations(iters)
        classes = {(c["error"], c["name"]): c for c in agg["loadFailureClasses"]}
        cart = classes[("ConnectionError", "/cart")]
        assert cart["totalOccurrences"] == 13
        assert cart["iterationsObserved"] == 2
        timeout = classes[("Timeout", "/")]
        assert timeout["totalOccurrences"] == 2
        assert timeout["iterationsObserved"] == 1

    def test_sort_order_by_total_descending(self):
        iters = [
            _iter_with_failures(
                [
                    {"error": "Big", "name": "x", "occurrences": 100},
                    {"error": "Small", "name": "y", "occurrences": 1},
                    {"error": "Medium", "name": "z", "occurrences": 10},
                ]
            )
        ]
        out = aggregate_iterations(iters)["loadFailureClasses"]
        assert [r["error"] for r in out] == ["Big", "Medium", "Small"]

    def test_non_dict_entries_skipped(self):
        iters = [
            _iter_with_failures(
                [
                    {"error": "Real", "name": "x", "occurrences": 3},
                    "not-a-dict",
                    None,
                ]
            )
        ]
        out = aggregate_iterations(iters)["loadFailureClasses"]
        assert len(out) == 1
        assert out[0]["error"] == "Real"

    def test_missing_occurrences_skipped(self):
        iters = [
            _iter_with_failures(
                [
                    {"error": "Real", "name": "x", "occurrences": 3},
                    {"error": "NoOcc", "name": "y"},  # no occurrences
                ]
            )
        ]
        out = aggregate_iterations(iters)["loadFailureClasses"]
        assert len(out) == 1

    def test_iterations_observed_counted_per_class_not_per_entry(self):
        """Two entries for the same (error, name) in one iteration → that
        iteration counts once toward iterationsObserved for that class."""
        iters = [
            _iter_with_failures(
                [
                    {"error": "X", "name": "y", "occurrences": 1},
                    {"error": "X", "name": "y", "occurrences": 2},
                ]
            )
        ]
        out = aggregate_iterations(iters)["loadFailureClasses"]
        assert out[0]["iterationsObserved"] == 1
        assert out[0]["totalOccurrences"] == 3
