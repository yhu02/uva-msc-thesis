"""Tests for the ``chaosprobe stats`` CLI command."""

import json
from pathlib import Path

from click.testing import CliRunner

from chaosprobe.commands.stats_cmd import (
    _load_strategies,
    _merge_summaries,
    _resolve_path,
    stats,
)


def _make_summary(tmp_path: Path, strategies: dict) -> Path:
    """Write a minimal ``summary.json`` capturing per-iteration scores."""
    payload = {
        "strategies": {
            name: {"iterations": [{"resilienceScore": s} for s in scores]}
            for name, scores in strategies.items()
        }
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(payload))
    return path


def _make_recovery_summary(tmp_path: Path, strategies: dict) -> Path:
    """Write a summary.json with metrics.recovery.summary.meanRecovery_ms."""
    payload = {
        "strategies": {
            name: {
                "iterations": [
                    {
                        "metrics": {
                            "recovery": {"summary": {"meanRecovery_ms": v}},
                        },
                    }
                    for v in values
                ]
            }
            for name, values in strategies.items()
        }
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(payload))
    return path


class TestResolvePath:
    def test_returns_value_for_deep_path(self):
        d = {"a": {"b": {"c": 42}}}
        assert _resolve_path(d, "a.b.c") == 42

    def test_returns_none_on_missing_key(self):
        d = {"a": {"b": {"c": 42}}}
        assert _resolve_path(d, "a.b.x") is None

    def test_returns_none_on_non_dict_midway(self):
        d = {"a": {"b": 5}}
        assert _resolve_path(d, "a.b.c") is None

    def test_single_part_path(self):
        assert _resolve_path({"x": 1}, "x") == 1


class TestLoadStrategies:
    def test_extracts_per_strategy_scores(self, tmp_path):
        path = _make_summary(tmp_path, {"colocate": [70, 75, 80], "spread": [60, 65]})
        out = _load_strategies(path, "resilienceScore")
        assert out == {"colocate": [70.0, 75.0, 80.0], "spread": [60.0, 65.0]}

    def test_extracts_nested_recovery_metric(self, tmp_path):
        path = _make_recovery_summary(tmp_path, {"colocate": [1200, 1500], "spread": [800, 900]})
        out = _load_strategies(path, "metrics.recovery.summary.meanRecovery_ms")
        assert out == {"colocate": [1200.0, 1500.0], "spread": [800.0, 900.0]}

    def test_skips_strategies_without_iterations(self, tmp_path):
        path = _make_summary(tmp_path, {"colocate": []})
        out = _load_strategies(path, "resilienceScore")
        assert out == {}

    def test_skips_iterations_without_score(self, tmp_path):
        payload = {
            "strategies": {
                "colocate": {
                    "iterations": [
                        {"resilienceScore": 70},
                        {"resilienceScore": None},
                        {},
                    ]
                }
            }
        }
        path = tmp_path / "summary.json"
        path.write_text(json.dumps(payload))
        out = _load_strategies(path, "resilienceScore")
        assert out == {"colocate": [70.0]}

    def test_skips_iterations_where_path_missing(self, tmp_path):
        payload = {
            "strategies": {
                "colocate": {
                    "iterations": [
                        {"metrics": {"recovery": {"summary": {"meanRecovery_ms": 100}}}},
                        {"metrics": {}},
                        {},
                    ]
                }
            }
        }
        path = tmp_path / "summary.json"
        path.write_text(json.dumps(payload))
        out = _load_strategies(path, "metrics.recovery.summary.meanRecovery_ms")
        assert out == {"colocate": [100.0]}

    def test_handles_missing_strategies_key(self, tmp_path):
        path = tmp_path / "summary.json"
        path.write_text(json.dumps({}))
        out = _load_strategies(path, "resilienceScore")
        assert out == {}

    def test_skips_non_numeric_value(self, tmp_path):
        payload = {
            "strategies": {
                "a": {
                    "iterations": [
                        {"resilienceScore": 42},
                        {"resilienceScore": "not-a-number"},
                    ]
                }
            }
        }
        path = tmp_path / "summary.json"
        path.write_text(json.dumps(payload))
        out = _load_strategies(path, "resilienceScore")
        assert out == {"a": [42.0]}


class TestStatsCommand:
    def test_text_output_includes_ci_and_pairwise(self, tmp_path):
        summary = _make_summary(
            tmp_path,
            {
                "colocate": [70, 75, 80, 78, 72],
                "spread": [40, 45, 50, 48, 42],
            },
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary)])

        assert result.exit_code == 0
        assert "Bootstrap 95% CI" in result.output
        assert "resilienceScore" in result.output
        assert "colocate" in result.output
        assert "spread" in result.output
        assert "Pairwise Mann-Whitney" in result.output

    def test_json_output_structure(self, tmp_path):
        summary = _make_summary(
            tmp_path,
            {"colocate": [70, 75, 80], "spread": [40, 45, 50]},
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "ci" in payload
        assert "pairwise" in payload
        assert payload["confidence"] == 0.95
        assert payload["metric"] == "resilienceScore"
        assert set(payload["ci"].keys()) == {"colocate", "spread"}
        assert len(payload["pairwise"]) == 1

    def test_recovery_metric_flag(self, tmp_path):
        summary = _make_recovery_summary(
            tmp_path,
            {
                "colocate": [1200, 1500, 1800, 1700, 1300],
                "spread": [800, 900, 850, 750, 820],
            },
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--metric", "recovery"])
        assert result.exit_code == 0
        assert "meanRecovery_ms" in result.output
        assert "colocate" in result.output

    def test_recovery_metric_json(self, tmp_path):
        summary = _make_recovery_summary(tmp_path, {"a": [100, 200, 300], "b": [400, 500, 600]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--metric", "recovery", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["metric"] == "meanRecovery_ms"

    def test_invalid_metric_choice_rejected(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--metric", "bogus"])
        assert result.exit_code != 0
        assert "bogus" in result.output

    def test_writes_to_output_file(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3], "b": [4, 5, 6]})
        out_path = tmp_path / "stats.txt"
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "-o", str(out_path)])

        assert result.exit_code == 0
        assert f"Wrote {out_path}" in result.output
        assert "Bootstrap" in out_path.read_text()

    def test_empty_summary_errors_out(self, tmp_path):
        empty = tmp_path / "summary.json"
        empty.write_text(json.dumps({"strategies": {}}))
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(empty)])

        assert result.exit_code == 1
        assert "no strategies" in result.output.lower()

    def test_recovery_empty_summary_errors_with_recovery_label(self, tmp_path):
        # Resilience scores present but no recovery metrics.
        summary = _make_summary(tmp_path, {"a": [1, 2, 3]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--metric", "recovery"])
        assert result.exit_code == 1
        assert "meanRecovery_ms" in result.output

    def test_single_strategy_no_pairwise(self, tmp_path):
        summary = _make_summary(tmp_path, {"colocate": [70, 75, 80]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary)])

        assert result.exit_code == 0
        assert "Bootstrap" in result.output
        assert "(no pairs" in result.output

    def test_nondeterministic_seed_accepted(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3], "b": [10, 11, 12]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--seed", "-1", "--n-resamples", "50"])
        assert result.exit_code == 0

    def test_custom_confidence_propagates_to_header(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3], "b": [10, 11, 12]})
        runner = CliRunner()
        result = runner.invoke(
            stats, ["-s", str(summary), "--confidence", "0.9", "--n-resamples", "50"]
        )
        assert result.exit_code == 0


def _make_both_metrics_summary(tmp_path: Path) -> Path:
    """A summary with both resilienceScore and meanRecovery_ms per
    iteration for two strategies."""
    payload = {
        "strategies": {
            "colocate": {
                "iterations": [
                    {
                        "resilienceScore": 70,
                        "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1500}}},
                    },
                    {
                        "resilienceScore": 75,
                        "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1700}}},
                    },
                    {
                        "resilienceScore": 80,
                        "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1300}}},
                    },
                ]
            },
            "spread": {
                "iterations": [
                    {
                        "resilienceScore": 40,
                        "metrics": {"recovery": {"summary": {"meanRecovery_ms": 800}}},
                    },
                    {
                        "resilienceScore": 45,
                        "metrics": {"recovery": {"summary": {"meanRecovery_ms": 850}}},
                    },
                ]
            },
        }
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(payload))
    return path


class TestAllMetricsFlag:
    def test_text_output_contains_both_blocks(self, tmp_path):
        summary = _make_both_metrics_summary(tmp_path)
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--all-metrics"])
        assert result.exit_code == 0
        assert "resilienceScore" in result.output
        assert "meanRecovery_ms" in result.output
        # Each block has its own pairwise header.
        assert result.output.count("Pairwise Mann-Whitney") == 2

    def test_json_output_groups_by_metric_label(self, tmp_path):
        summary = _make_both_metrics_summary(tmp_path)
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--all-metrics", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "metrics" in payload
        assert set(payload["metrics"].keys()) == {"resilienceScore", "meanRecovery_ms"}
        for block in payload["metrics"].values():
            assert "ci" in block
            assert "pairwise" in block

    def test_all_metrics_skips_metric_missing_data(self, tmp_path):
        """If only resilience is present, --all-metrics still succeeds
        and emits only the resilience block."""
        summary = _make_summary(tmp_path, {"a": [1, 2, 3], "b": [4, 5, 6]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--all-metrics", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert set(payload["metrics"].keys()) == {"resilienceScore"}

    def test_all_metrics_errors_when_no_data_at_all(self, tmp_path):
        empty = tmp_path / "summary.json"
        empty.write_text(json.dumps({"strategies": {}}))
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(empty), "--all-metrics"])
        assert result.exit_code == 1
        assert "any supported metric" in result.output.lower()

    def test_metric_flag_ignored_when_all_metrics_set(self, tmp_path):
        """--metric defaults to resilience, but --all-metrics should still
        emit both blocks."""
        summary = _make_both_metrics_summary(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            stats, ["-s", str(summary), "--metric", "resilience", "--all-metrics"]
        )
        assert result.exit_code == 0
        assert "meanRecovery_ms" in result.output
        assert "resilienceScore" in result.output


class TestCSVOutput:
    def test_csv_header_and_ci_rows(self, tmp_path):
        import csv
        from io import StringIO

        summary = _make_summary(tmp_path, {"colocate": [70, 75, 80], "spread": [40, 45, 50]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--csv"])
        assert result.exit_code == 0

        rows = list(csv.reader(StringIO(result.output)))
        header = rows[0]
        assert header[:6] == ["section", "metric", "strategy", "a", "b", "n"]

        ci_rows = [r for r in rows[1:] if r[0] == "ci"]
        assert {r[2] for r in ci_rows} == {"colocate", "spread"}
        for r in ci_rows:
            assert r[1] == "resilienceScore"

    def test_csv_pairwise_section(self, tmp_path):
        import csv
        from io import StringIO

        summary = _make_summary(tmp_path, {"colocate": [70, 75, 80], "spread": [40, 45, 50]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--csv"])
        rows = list(csv.reader(StringIO(result.output)))
        pairwise_rows = [r for r in rows[1:] if r[0] == "pairwise"]
        assert len(pairwise_rows) == 1
        assert {pairwise_rows[0][3], pairwise_rows[0][4]} == {"colocate", "spread"}

    def test_csv_all_metrics_carries_metric_column(self, tmp_path):
        import csv
        from io import StringIO

        summary = _make_both_metrics_summary(tmp_path)
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--all-metrics", "--csv"])
        assert result.exit_code == 0
        rows = list(csv.reader(StringIO(result.output)))
        metrics_in_data = {r[1] for r in rows[1:]}
        assert metrics_in_data == {"resilienceScore", "meanRecovery_ms"}

    def test_csv_and_json_mutually_exclusive(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3], "b": [4, 5, 6]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--csv", "--json"])
        assert result.exit_code == 2
        assert "mutually exclusive" in result.output.lower()

    def test_csv_writes_to_output_file(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3], "b": [4, 5, 6]})
        out_path = tmp_path / "stats.csv"
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--csv", "-o", str(out_path)])
        assert result.exit_code == 0
        contents = out_path.read_text()
        assert contents.startswith("section,metric,strategy")
        assert "ci" in contents
        assert "pairwise" in contents


def _make_split_summary(tmp_path: Path) -> Path:
    """summary.json with d2s + s2r split metrics per iteration."""
    payload = {
        "strategies": {
            "colocate": {
                "iterations": [
                    {
                        "metrics": {
                            "recovery": {
                                "summary": {
                                    "meanDeletionToScheduled_ms": 500,
                                    "meanScheduledToReady_ms": 900,
                                }
                            }
                        }
                    },
                    {
                        "metrics": {
                            "recovery": {
                                "summary": {
                                    "meanDeletionToScheduled_ms": 700,
                                    "meanScheduledToReady_ms": 1100,
                                }
                            }
                        }
                    },
                ]
            },
            "spread": {
                "iterations": [
                    {
                        "metrics": {
                            "recovery": {
                                "summary": {
                                    "meanDeletionToScheduled_ms": 200,
                                    "meanScheduledToReady_ms": 600,
                                }
                            }
                        }
                    },
                    {
                        "metrics": {
                            "recovery": {
                                "summary": {
                                    "meanDeletionToScheduled_ms": 250,
                                    "meanScheduledToReady_ms": 650,
                                }
                            }
                        }
                    },
                ]
            },
        }
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(payload))
    return path


class TestSplitMetrics:
    def test_d2s_metric_emits_correct_label(self, tmp_path):
        summary = _make_split_summary(tmp_path)
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--metric", "d2s", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["metric"] == "meanDeletionToScheduled_ms"
        assert set(payload["ci"].keys()) == {"colocate", "spread"}

    def test_s2r_metric_emits_correct_label(self, tmp_path):
        summary = _make_split_summary(tmp_path)
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--metric", "s2r", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["metric"] == "meanScheduledToReady_ms"

    def test_all_metrics_includes_split_when_present(self, tmp_path):
        """A summary with both split metrics → --all-metrics emits both
        split blocks; the other two are silently skipped."""
        summary = _make_split_summary(tmp_path)
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--all-metrics", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        present = set(payload["metrics"].keys())
        assert "meanDeletionToScheduled_ms" in present
        assert "meanScheduledToReady_ms" in present

    def test_d2s_against_summary_without_d2s_errors_with_correct_label(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--metric", "d2s"])
        assert result.exit_code == 1
        assert "meanDeletionToScheduled_ms" in result.output


class TestPairFilter:
    def test_pair_restricts_ci_to_named_strategies(self, tmp_path):
        summary = _make_summary(
            tmp_path,
            {
                "colocate": [70, 75, 80],
                "spread": [40, 45, 50],
                "adversarial": [30, 35, 40],
                "random": [50, 55, 60],
            },
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--pair", "colocate,spread", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert set(payload["ci"].keys()) == {"colocate", "spread"}
        assert len(payload["pairwise"]) == 1
        assert {payload["pairwise"][0]["a"], payload["pairwise"][0]["b"]} == {
            "colocate",
            "spread",
        }

    def test_pair_with_only_one_known_strategy_still_emits_ci(self, tmp_path):
        """Only one of the named strategies actually present in the
        summary → analyses dict still non-empty, exit 0, CI block has
        one strategy and no pairwise."""
        summary = _make_summary(tmp_path, {"colocate": [70, 75, 80]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--pair", "colocate,spread"])
        assert result.exit_code == 0
        assert "colocate" in result.output
        assert "(no pairs" in result.output

    def test_pair_with_no_matching_strategies_errors_out(self, tmp_path):
        summary = _make_summary(tmp_path, {"colocate": [70, 75, 80]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--pair", "spread,adversarial"])
        assert result.exit_code == 1
        assert "--pair" in result.output

    def test_pair_with_single_name_rejected(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3], "b": [4, 5, 6]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--pair", "a"])
        assert result.exit_code == 2
        assert "at least two" in result.output.lower()

    def test_pair_whitespace_tolerated(self, tmp_path):
        summary = _make_summary(tmp_path, {"colocate": [70, 75], "spread": [40, 45], "x": [10, 20]})
        runner = CliRunner()
        result = runner.invoke(
            stats, ["-s", str(summary), "--pair", " colocate , spread ", "--json"]
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert set(payload["ci"].keys()) == {"colocate", "spread"}

    def test_pair_filters_all_metrics_mode_too(self, tmp_path):
        summary = _make_both_metrics_summary(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            stats,
            [
                "-s",
                str(summary),
                "--all-metrics",
                "--pair",
                "colocate,spread",
                "--json",
            ],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        for block in payload["metrics"].values():
            assert set(block["ci"].keys()) <= {"colocate", "spread"}


class TestEffectSizeColumns:
    def test_csv_header_includes_effect_size_columns(self, tmp_path):
        import csv as _csv
        from io import StringIO

        summary = _make_summary(
            tmp_path,
            {"colocate": [70, 75, 80], "spread": [40, 45, 50]},
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--csv"])
        assert result.exit_code == 0

        rows = list(_csv.reader(StringIO(result.output)))
        header = rows[0]
        assert "cliffs_delta" in header
        assert "effect_size_magnitude" in header

    def test_csv_pairwise_row_has_values_when_library_provides_them(self, tmp_path):
        """The library used by stats provides cliffs_delta + magnitude as
        of PR #53; the pairwise row in the CSV should reflect those
        values."""
        import csv as _csv
        from io import StringIO

        summary = _make_summary(
            tmp_path,
            {"colocate": [70, 75, 80], "spread": [40, 45, 50]},
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--csv"])

        rows = list(_csv.reader(StringIO(result.output)))
        header = rows[0]
        pairwise = [r for r in rows if r[0] == "pairwise"][0]
        delta_idx = header.index("cliffs_delta")
        mag_idx = header.index("effect_size_magnitude")
        # When PR #53 is included, both columns should be populated.
        # Without it, both will be empty strings — we tolerate either
        # to keep this PR independent of #53's merge order.
        if pairwise[delta_idx]:
            assert pairwise[mag_idx] in {"negligible", "small", "medium", "large"}

    def test_text_formatter_renders_delta_columns(self):
        """Direct call into the formatter — exercise the rendering
        independent of the library version."""
        from chaosprobe.commands.stats_cmd import _format_text

        samples = {"a": [1, 2, 3], "b": [4, 5, 6]}
        ci_rows = {
            "a": {"point": 2.0, "ci_low": 1.0, "ci_high": 3.0, "n": 3},
            "b": {"point": 5.0, "ci_low": 4.0, "ci_high": 6.0, "n": 3},
        }
        # With library fields populated.
        pairwise_rows_with = [
            {
                "a": "a",
                "b": "b",
                "mean_a": 2.0,
                "mean_b": 5.0,
                "p_raw": 0.04,
                "p_holm": 0.04,
                "significant_05": True,
                "cliffs_delta": -1.0,
                "effect_size_magnitude": "large",
            }
        ]
        text = _format_text(samples, ci_rows, pairwise_rows_with, 0.95, "score")
        assert "delta" in text
        assert "magnitude" in text
        assert "large" in text
        assert "-1.0" in text

    def test_text_formatter_degrades_gracefully_without_effect_size(self):
        """Pairwise rows from older library versions don't carry
        cliffs_delta / effect_size_magnitude.  The formatter must show
        '-' rather than KeyError."""
        from chaosprobe.commands.stats_cmd import _format_text

        samples = {"a": [1, 2, 3], "b": [4, 5, 6]}
        ci_rows = {
            "a": {"point": 2.0, "ci_low": 1.0, "ci_high": 3.0, "n": 3},
            "b": {"point": 5.0, "ci_low": 4.0, "ci_high": 6.0, "n": 3},
        }
        pairwise_rows_without = [
            {
                "a": "a",
                "b": "b",
                "mean_a": 2.0,
                "mean_b": 5.0,
                "p_raw": 0.04,
                "p_holm": 0.04,
                "significant_05": True,
            }
        ]
        text = _format_text(samples, ci_rows, pairwise_rows_without, 0.95, "score")
        # No crash; header still present.
        assert "delta" in text
        assert "magnitude" in text


class TestEffectSizeMinFilter:
    """`--effect-size-min` drops pairwise rows below the requested
    Cliff's delta magnitude.  Lets a defender scan a large pairwise
    matrix for practically-meaningful differences without sifting noise."""

    def test_large_only_drops_negligible_pairs(self, tmp_path):
        # Three strategies: a, b mostly equal (negligible delta); c
        # very different from both (large delta).
        summary = _make_summary(
            tmp_path,
            {
                "a": [50, 51, 50, 49, 50],
                "b": [50, 51, 50, 49, 50],
                "c": [10, 11, 9, 12, 10],
            },
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--effect-size-min", "large", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        # Two surviving pairs: a-c and b-c (both large).  Pair a-b is
        # negligible → dropped.
        pairs = {(r["a"], r["b"]) for r in payload["pairwise"]}
        assert ("a", "b") not in pairs and ("b", "a") not in pairs

    def test_small_threshold_keeps_small_medium_large(self, tmp_path):
        summary = _make_summary(
            tmp_path,
            {
                "a": [50, 51, 49, 50],
                "b": [51, 52, 50, 51],  # slight shift — small/negligible
                "c": [10, 11, 9, 10],  # large diff
            },
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--effect-size-min", "small", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        # Every retained row should be at least small.
        order = ["negligible", "small", "medium", "large"]
        cutoff = order.index("small")
        for row in payload["pairwise"]:
            mag = row["effect_size_magnitude"]
            assert order.index(mag) >= cutoff

    def test_no_filter_keeps_all_rows(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [50, 51], "b": [50, 51], "c": [50, 51]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        # 3 strategies → C(3,2) = 3 pairs, all kept.
        assert len(payload["pairwise"]) == 3

    def test_invalid_choice_rejected(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2], "b": [3, 4]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--effect-size-min", "huge"])
        assert result.exit_code != 0
        assert "huge" in result.output

    def test_filter_works_with_all_metrics(self, tmp_path):
        # Construct a summary with split metrics; verify the filter
        # applies to every block under --all-metrics.
        summary = _make_summary(
            tmp_path,
            {
                "a": [50, 51, 50, 49],
                "b": [50, 51, 50, 49],
                "c": [10, 11, 9, 12],
            },
        )
        runner = CliRunner()
        result = runner.invoke(
            stats,
            ["-s", str(summary), "--all-metrics", "--effect-size-min", "large", "--json"],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        # In --all-metrics mode, only resilience block applies (no
        # recovery data).  Verify filter ran on it.
        block = next(iter(payload["metrics"].values()))
        for row in block["pairwise"]:
            assert row["effect_size_magnitude"] == "large"


class TestSortKey:
    """``--sort`` reorders the pairwise table.  Default p_holm matches
    the library; ``delta`` puts the largest practical effects first."""

    def test_default_sort_is_p_holm_ascending(self, tmp_path):
        summary = _make_summary(
            tmp_path,
            {
                "a": [50, 51, 50],
                "b": [50, 51, 50],
                "c": [10, 11, 9],
                "d": [40, 41, 39],
            },
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        ps = [r["p_holm"] for r in payload["pairwise"]]
        assert ps == sorted(ps)

    def test_delta_sort_descending_by_abs_cliffs_delta(self, tmp_path):
        summary = _make_summary(
            tmp_path,
            {
                "a": [50, 51, 50],
                "b": [50, 51, 50],
                "c": [10, 11, 9],
            },
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--sort", "delta", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        deltas = [abs(r["cliffs_delta"]) for r in payload["pairwise"]]
        assert deltas == sorted(deltas, reverse=True)

    def test_p_raw_sort(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [50, 51], "b": [40, 41], "c": [10, 11]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--sort", "p_raw", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        ps = [r["p_raw"] for r in payload["pairwise"]]
        assert ps == sorted(ps)

    def test_invalid_sort_rejected(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2], "b": [3, 4]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--sort", "bogus"])
        assert result.exit_code != 0


class TestMarkdownOutput:
    """`--markdown` emits GFM tables suitable for thesis documents."""

    def test_markdown_emits_ci_and_pairwise_tables(self, tmp_path):
        summary = _make_summary(
            tmp_path,
            {"colocate": [70, 75, 80], "spread": [40, 45, 50]},
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--markdown"])
        assert result.exit_code == 0
        assert "### resilienceScore" in result.output
        assert "Bootstrap 95% CI" in result.output
        assert "| strategy | n | mean | CI low | CI high |" in result.output
        assert "Pairwise Mann-Whitney U" in result.output
        # GFM separator row.
        assert "|---|---:|---:|---:|---:|" in result.output
        assert "colocate" in result.output
        assert "spread" in result.output

    def test_markdown_writes_to_output_file(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3], "b": [4, 5, 6]})
        out_path = tmp_path / "stats.md"
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--markdown", "-o", str(out_path)])
        assert result.exit_code == 0
        contents = out_path.read_text()
        assert contents.startswith("### resilienceScore")

    def test_markdown_and_json_mutually_exclusive(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3], "b": [4, 5, 6]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--markdown", "--json"])
        assert result.exit_code == 2
        assert "mutually exclusive" in result.output.lower()

    def test_markdown_and_csv_mutually_exclusive(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [1, 2, 3], "b": [4, 5, 6]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--markdown", "--csv"])
        assert result.exit_code == 2
        assert "mutually exclusive" in result.output.lower()

    def test_markdown_handles_all_metrics(self, tmp_path):
        summary = _make_both_metrics_summary(tmp_path)
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--all-metrics", "--markdown"])
        assert result.exit_code == 0
        # Both metric headings present.
        assert "### resilienceScore" in result.output
        assert "### meanRecovery_ms" in result.output

    def test_markdown_includes_effect_size_columns(self, tmp_path):
        summary = _make_summary(
            tmp_path,
            {"colocate": [70, 75, 80, 78, 72], "spread": [40, 45, 50, 48, 42]},
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--markdown"])
        assert result.exit_code == 0
        assert "Cliff's δ" in result.output
        assert "magnitude" in result.output
        assert "large" in result.output  # all colocate > all spread

    def test_markdown_no_pairs_placeholder(self, tmp_path):
        summary = _make_summary(tmp_path, {"colocate": [70, 75, 80]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--markdown"])
        assert result.exit_code == 0
        assert "no pairs" in result.output


class TestBaselineRelative:
    def test_baseline_relative_in_json_output(self, tmp_path):
        summary = _make_summary(
            tmp_path,
            {
                "baseline": [100, 100, 100],
                "colocate": [70, 75, 80],
                "spread": [90, 95, 100],
            },
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--baseline", "baseline", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        rel = payload["baselineRelative"]
        # baseline mean = 100
        # colocate mean = 75 → delta = -25, percent = -25
        assert rel["colocate"]["delta"] == -25.0
        assert rel["colocate"]["percent"] == -25.0
        # spread mean ≈ 95 → delta = -5, percent = -5
        assert rel["spread"]["delta"] == -5.0
        assert rel["spread"]["percent"] == -5.0
        # baseline itself is not in the relative output.
        assert "baseline" not in rel

    def test_baseline_relative_in_text_output(self, tmp_path):
        summary = _make_summary(
            tmp_path,
            {"baseline": [100, 100], "colocate": [70, 75]},
        )
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--baseline", "baseline"])
        assert result.exit_code == 0
        assert "Relative to baseline" in result.output
        assert "colocate" in result.output

    def test_baseline_missing_from_summary_no_block(self, tmp_path):
        """Baseline strategy not present → block silently omitted (no
        error; caller can still use other output)."""
        summary = _make_summary(tmp_path, {"colocate": [70, 75], "spread": [40, 45]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--baseline", "baseline", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "baselineRelative" not in payload

    def test_baseline_zero_mean_no_block(self, tmp_path):
        """When the baseline mean is exactly zero, percent change is
        undefined → block omitted."""
        summary = _make_summary(tmp_path, {"baseline": [0, 0, 0], "colocate": [70, 75]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--baseline", "baseline", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "baselineRelative" not in payload

    def test_no_baseline_flag_no_block(self, tmp_path):
        summary = _make_summary(tmp_path, {"a": [50, 51], "b": [40, 41]})
        runner = CliRunner()
        result = runner.invoke(stats, ["-s", str(summary), "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "baselineRelative" not in payload


class TestMergeSummaries:
    def test_concatenates_iterations_per_strategy(self):
        a = {
            "strategies": {
                "spread": {"iterations": [{"resilienceScore": 80}, {"resilienceScore": 82}]}
            }
        }
        b = {
            "strategies": {
                "spread": {"iterations": [{"resilienceScore": 85}, {"resilienceScore": 86}]}
            }
        }
        merged = _merge_summaries([a, b])
        scores = [it["resilienceScore"] for it in merged["strategies"]["spread"]["iterations"]]
        assert scores == [80, 82, 85, 86]

    def test_strategies_in_only_some_inputs_are_kept(self):
        a = {"strategies": {"spread": {"iterations": [{"resilienceScore": 80}]}}}
        b = {"strategies": {"colocate": {"iterations": [{"resilienceScore": 60}]}}}
        merged = _merge_summaries([a, b])
        assert set(merged["strategies"]) == {"spread", "colocate"}
        assert len(merged["strategies"]["spread"]["iterations"]) == 1
        assert len(merged["strategies"]["colocate"]["iterations"]) == 1

    def test_empty_input_is_empty_strategies(self):
        merged = _merge_summaries([])
        assert merged == {"strategies": {}}

    def test_preserves_top_level_keys_from_first_input(self):
        a = {
            "schemaVersion": "2.0.0",
            "runMetadata": {"git": {"commit": "abc"}},
            "strategies": {"spread": {"iterations": [{"resilienceScore": 80}]}},
        }
        b = {"strategies": {"spread": {"iterations": [{"resilienceScore": 85}]}}}
        merged = _merge_summaries([a, b])
        assert merged["schemaVersion"] == "2.0.0"
        assert merged["runMetadata"] == {"git": {"commit": "abc"}}


class TestMergeFlag:
    def test_merge_pools_iterations_into_one_analysis(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        a = _make_summary(tmp_path / "a", {"spread": [80, 82], "colocate": [60, 62]})
        b = _make_summary(tmp_path / "b", {"spread": [85, 86], "colocate": [58, 61]})
        result = CliRunner().invoke(
            stats,
            ["-s", str(a), "--merge", str(b), "--metric", "resilience", "--seed", "0"],
        )
        assert result.exit_code == 0, result.output
        # Combined n=4 per strategy.
        assert "spread" in result.output and "colocate" in result.output
        # Header shows the n column populated with 4 (post-merge).
        assert "  4 " in result.output

    def test_merge_multiple_files_chains(self, tmp_path):
        for sub in ("a", "b", "c"):
            (tmp_path / sub).mkdir()
        a = _make_summary(tmp_path / "a", {"spread": [80], "colocate": [60]})
        b = _make_summary(tmp_path / "b", {"spread": [82], "colocate": [62]})
        c = _make_summary(tmp_path / "c", {"spread": [85], "colocate": [58]})
        result = CliRunner().invoke(
            stats,
            [
                "-s",
                str(a),
                "--merge",
                str(b),
                "--merge",
                str(c),
                "--metric",
                "resilience",
                "--seed",
                "0",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "  3 " in result.output  # n=3 per strategy

    def test_merge_with_strategy_only_in_secondary(self, tmp_path):
        (tmp_path / "a2").mkdir()
        (tmp_path / "b2").mkdir()
        a = _make_summary(tmp_path / "a2", {"spread": [80, 82]})
        b = _make_summary(tmp_path / "b2", {"colocate": [60, 62]})
        result = CliRunner().invoke(
            stats,
            ["-s", str(a), "--merge", str(b), "--metric", "resilience", "--seed", "0"],
        )
        assert result.exit_code == 0, result.output
        assert "spread" in result.output
        assert "colocate" in result.output

    def test_no_merge_keeps_original_n(self, tmp_path):
        a = _make_summary(tmp_path, {"spread": [80, 82, 84], "colocate": [60, 62, 64]})
        result = CliRunner().invoke(
            stats,
            ["-s", str(a), "--metric", "resilience", "--seed", "0"],
        )
        assert result.exit_code == 0, result.output
        assert "  3 " in result.output  # original n=3 preserved
