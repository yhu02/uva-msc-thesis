"""Tests for the `recommend` command (statistics-driven placement recommender)."""

import json

from click.testing import CliRunner

from chaosprobe.commands.recommend_cmd import (
    _find_comparison,
    _fmt,
    _recommend,
    _samples_by_strategy,
    recommend,
)


def _summary(strategies):
    """Build a summary.json-shaped dict from {name: [iteration-dict, ...]}."""
    return {"strategies": {name: {"iterations": iters} for name, iters in strategies.items()}}


def _resilience(scores):
    return [{"resilienceScore": s} for s in scores]


def _recovery(values):
    return [{"metrics": {"recovery": {"summary": {"meanRecovery_ms": v}}}} for v in values]


# ---------------------------------------------------------------------------
# _samples_by_strategy
# ---------------------------------------------------------------------------


class TestSamplesByStrategy:
    def test_extracts_resilience_samples(self):
        raw = _summary({"spread": _resilience([90, 92]), "colocate": _resilience([50])})
        out = _samples_by_strategy(raw, "resilience")
        assert out == {"spread": [90.0, 92.0], "colocate": [50.0]}

    def test_extracts_recovery_via_nested_path(self):
        raw = _summary({"fast": _recovery([1000, 1100])})
        assert _samples_by_strategy(raw, "recovery") == {"fast": [1000.0, 1100.0]}

    def test_skips_strategies_without_samples(self):
        raw = _summary(
            {
                "good": _resilience([80]),
                "empty": [],  # no iterations
                "missing": [{"somethingElse": 1}],  # no resilienceScore
            }
        )
        assert _samples_by_strategy(raw, "resilience") == {"good": [80.0]}

    def test_skips_non_dict_strategy_and_iteration(self):
        raw = {
            "strategies": {
                "bad": "not-a-dict",
                "ok": {"iterations": ["not-a-dict", {"resilienceScore": 70}]},
            }
        }
        assert _samples_by_strategy(raw, "resilience") == {"ok": [70.0]}

    def test_skips_non_numeric_and_broken_path(self):
        raw = {
            "strategies": {
                "s": {
                    "iterations": [
                        {"resilienceScore": "abc"},  # not floatable
                        {"resilienceScore": 88},
                    ]
                },
                # nested path walks into a non-dict -> _resolve_path returns None
                "r": {"iterations": [{"metrics": "not-a-dict"}]},
            }
        }
        assert _samples_by_strategy(raw, "resilience") == {"s": [88.0]}
        assert _samples_by_strategy(raw, "recovery") == {}


# ---------------------------------------------------------------------------
# _find_comparison
# ---------------------------------------------------------------------------


class TestFindComparison:
    def test_finds_pair_either_order(self):
        pw = [{"a": "x", "b": "y", "p_raw": 0.1}]
        assert _find_comparison(pw, "y", "x")["p_raw"] == 0.1

    def test_returns_none_when_absent(self):
        assert _find_comparison([{"a": "x", "b": "y"}], "x", "z") is None


# ---------------------------------------------------------------------------
# _recommend
# ---------------------------------------------------------------------------


class TestRecommend:
    def test_clear_winner_is_significant(self):
        samples = {
            "spread": [95, 96, 97, 95, 96, 97],
            "colocate": [40, 41, 42, 40, 41, 42],
        }
        result = _recommend(samples, higher_is_better=True, alpha=0.05)
        assert result["recommended"] == "spread"
        assert result["status"] == "significant"
        assert result["decisiveComparison"]["significant"] is True
        assert result["decisiveComparison"]["a"] == "spread"
        assert result["decisiveComparison"]["b"] == "colocate"
        # leader is first in the ranking and carries a CI
        assert result["ranking"][0]["name"] == "spread"
        assert result["ranking"][0]["ciLow"] is not None

    def test_overlapping_means_is_tentative(self):
        samples = {"spread": [80, 81, 82], "colocate": [79, 80, 81]}
        result = _recommend(samples, higher_is_better=True, alpha=0.05)
        assert result["recommended"] == "spread"  # higher mean
        assert result["status"] == "tentative"
        assert result["decisiveComparison"]["significant"] is False
        assert "chaosprobe power" in result["rationale"]

    def test_recovery_lower_is_better(self):
        samples = {
            "fast": [1000, 1050, 1010, 1020, 1030, 1040],
            "slow": [3000, 3050, 3010, 3020, 3030, 3040],
        }
        result = _recommend(samples, higher_is_better=False, alpha=0.05)
        assert result["recommended"] == "fast"
        assert result["status"] == "significant"

    def test_single_strategy(self):
        result = _recommend({"only": [70, 80]}, higher_is_better=True, alpha=0.05)
        assert result["recommended"] == "only"
        assert result["status"] == "single-strategy"
        assert result["decisiveComparison"] is None

    def test_no_data(self):
        result = _recommend({}, higher_is_better=True, alpha=0.05)
        assert result["recommended"] is None
        assert result["status"] == "no-data"
        assert result["ranking"] == []

    def test_exact_tie_breaks_on_name(self):
        samples = {"bbb": [80, 81, 82], "aaa": [80, 81, 82]}
        result = _recommend(samples, higher_is_better=True, alpha=0.05)
        # equal means -> alphabetical tiebreak -> "aaa" leads
        assert result["recommended"] == "aaa"
        assert result["status"] == "tentative"


# ---------------------------------------------------------------------------
# _fmt
# ---------------------------------------------------------------------------


class TestFmt:
    def test_formats_number(self):
        assert _fmt(3.14159) == "3.14"

    def test_none_becomes_dash(self):
        assert _fmt(None) == "—"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestRecommendCli:
    def _write(self, tmp_path, strategies):
        path = tmp_path / "summary.json"
        path.write_text(json.dumps(_summary(strategies)))
        return str(path)

    def test_human_output_names_recommendation(self, tmp_path):
        path = self._write(
            tmp_path,
            {"spread": _resilience([95, 96, 97]), "colocate": _resilience([40, 41, 42])},
        )
        result = CliRunner().invoke(recommend, ["-s", path])
        assert result.exit_code == 0
        assert "Recommended: spread" in result.output

    def test_json_output_has_documented_keys(self, tmp_path):
        path = self._write(
            tmp_path,
            {"spread": _resilience([95, 96, 97]), "colocate": _resilience([40, 41, 42])},
        )
        result = CliRunner().invoke(recommend, ["-s", path, "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        for key in (
            "recommended",
            "metric",
            "status",
            "ranking",
            "decisiveComparison",
            "rationale",
        ):
            assert key in payload
        assert payload["metric"] == "resilienceScore"
        assert payload["source"] == path

    def test_recovery_metric_and_alpha_honored(self, tmp_path):
        path = self._write(
            tmp_path,
            {"fast": _recovery([1000, 1010, 1020]), "slow": _recovery([3000, 3010, 3020])},
        )
        result = CliRunner().invoke(
            recommend, ["-s", path, "--metric", "recovery", "--alpha", "0.01", "--json"]
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["metric"] == "meanRecovery_ms"
        assert payload["recommended"] == "fast"

    def test_no_data_human_output(self, tmp_path):
        path = self._write(tmp_path, {"x": [{"nope": 1}]})
        result = CliRunner().invoke(recommend, ["-s", path])
        assert result.exit_code == 0
        assert "No strategy has data" in result.output

    def test_missing_summary_file_errors(self):
        result = CliRunner().invoke(recommend, ["-s", "/nonexistent/summary.json"])
        assert result.exit_code != 0
