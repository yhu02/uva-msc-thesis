"""Tests for pure helpers across the codebase.

Covers small standalone functions that previously had no coverage:
- ``metrics/throughput._parse_dd_elapsed_seconds`` (regex parser)
- ``output/charts.strategy_colors`` (color mapping)
- ``output/charts._extract_metric`` (metric extraction with median fallback)
- ``probes/builder.RustProbeBuilder.discover_probes`` (filesystem walk)
- ``output/visualize._normalize_strategy`` (run-shape projection)
"""

from pathlib import Path
from typing import Any, Dict

from chaosprobe.metrics.throughput import _parse_dd_elapsed_seconds
from chaosprobe.output.charts import _extract_metric, strategy_colors
from chaosprobe.output.visualize import _collect_iteration_data, _normalize_strategy
from chaosprobe.probes.builder import RustProbeBuilder

# ---------------------------------------------------------------------------
# _parse_dd_elapsed_seconds
# ---------------------------------------------------------------------------


class TestParseDdElapsed:
    def test_gnu_dd_format(self):
        out = "262144 bytes (262 kB, 256 KiB) copied, 0.00213 s, 123 MB/s"
        assert _parse_dd_elapsed_seconds(out) == 0.00213

    def test_busybox_dd_format(self):
        out = "262144 bytes (256.0KB) copied, 0.000876 seconds, 285.0MB/s"
        assert _parse_dd_elapsed_seconds(out) == 0.000876

    def test_scientific_notation(self):
        out = "1 bytes copied, 1.5e-3 s, 0 MB/s"
        assert _parse_dd_elapsed_seconds(out) == 0.0015

    def test_missing_summary_returns_none(self):
        # dd failed before printing its report
        assert _parse_dd_elapsed_seconds("dd: cannot open '/foo': No such file") is None
        assert _parse_dd_elapsed_seconds("") is None

    def test_garbage_in_elapsed_field_returns_none(self):
        # The regex requires a numeric literal ([0-9.eE+-]+), so "NaN"
        # in the elapsed slot doesn't match the summary pattern and the
        # parser returns None.  Good: NaN downstream would have broken
        # mean()/min()/max() in the aggregator.
        out = "0 bytes copied, NaN s, 0 MB/s"
        assert _parse_dd_elapsed_seconds(out) is None

    def test_integer_seconds(self):
        out = "262144 bytes copied, 1 s, 256 MB/s"
        assert _parse_dd_elapsed_seconds(out) == 1.0


# ---------------------------------------------------------------------------
# strategy_colors
# ---------------------------------------------------------------------------


class TestStrategyColors:
    def test_known_strategies_get_canonical_colors(self):
        result = strategy_colors(["baseline", "colocate", "spread"])
        assert result == ["#607D8B", "#F44336", "#4CAF50"]

    def test_unknown_strategy_cycles_through_default_palette(self):
        result = strategy_colors(["custom-a", "custom-b"])
        # First two from default palette
        assert result[0] == "#607D8B"
        assert result[1] == "#795548"

    def test_mixed_known_and_unknown_keeps_known_colors(self):
        result = strategy_colors(["colocate", "custom", "spread"])
        assert result[0] == "#F44336"  # canonical for colocate
        assert result[2] == "#4CAF50"  # canonical for spread
        # custom got the first default-palette color
        assert result[1] == "#607D8B"

    def test_empty_list(self):
        assert strategy_colors([]) == []

    def test_default_palette_wraps_after_5_unknowns(self):
        result = strategy_colors([f"custom-{i}" for i in range(7)])
        # The 6th unknown wraps back to index 0 in the default palette
        assert result[5] == "#607D8B"
        assert result[6] == "#795548"


# ---------------------------------------------------------------------------
# _extract_metric
# ---------------------------------------------------------------------------


class TestExtractMetric:
    def test_picks_top_level_metric_when_present(self):
        raw = {
            "colocate": {
                "metrics": {"latency": {"phases": {}, "marker": "top-level"}},
                "iterations": [],
            }
        }
        result = _extract_metric(raw, "latency")
        assert result == {"colocate": {"phases": {}, "marker": "top-level"}}

    def test_falls_back_to_median_iteration(self):
        raw = {
            "spread": {
                "metrics": None,
                "iterations": [
                    {
                        "resilienceScore": 20,
                        "metrics": {"latency": {"marker": "low-score"}},
                    },
                    {
                        "resilienceScore": 60,
                        "metrics": {"latency": {"marker": "median"}},
                    },
                    {
                        "resilienceScore": 90,
                        "metrics": {"latency": {"marker": "high-score"}},
                    },
                ],
            }
        }
        result = _extract_metric(raw, "latency")
        # Median of 3 items at index len/2 = 1 → "high-score"... wait,
        # the code uses sorted_iters[len(sorted_iters) // 2]. With 3 iters
        # sorted by score, that's index 1 → "median" (the actual median).
        assert result["spread"]["marker"] == "median"

    def test_require_available_filters_top_level(self):
        raw = {
            "spread": {
                "metrics": {"resources": {"available": False, "marker": "top"}},
                "iterations": [],
            }
        }
        result = _extract_metric(raw, "resources", require_available=True)
        assert result == {}

    def test_require_available_accepts_when_true(self):
        raw = {
            "spread": {
                "metrics": {"resources": {"available": True, "data": "x"}},
                "iterations": [],
            }
        }
        result = _extract_metric(raw, "resources", require_available=True)
        assert result == {"spread": {"available": True, "data": "x"}}

    def test_strategy_with_no_metric_data_omitted(self):
        raw = {
            "baseline": {"metrics": {}, "iterations": []},
        }
        assert _extract_metric(raw, "latency") == {}

    def test_iteration_without_target_metric_skipped_in_fallback(self):
        raw = {
            "spread": {
                "metrics": None,
                "iterations": [
                    {"resilienceScore": 50, "metrics": {"other": "x"}},
                ],
            }
        }
        # No iteration has 'latency', so result is empty
        assert _extract_metric(raw, "latency") == {}

    def test_error_iterations_excluded_from_median_fallback(self):
        raw = {
            "default": {
                "metrics": None,
                "iterations": [
                    {
                        "verdict": "ERROR",
                        "resilienceScore": 0,
                        "metrics": {"latency": {"marker": "error-iter"}},
                    },
                    {
                        "verdict": "FAIL",
                        "resilienceScore": 50,
                        "metrics": {"latency": {"marker": "valid"}},
                    },
                ],
            }
        }
        # The ERROR iteration is ineligible; only the valid one can be picked.
        assert _extract_metric(raw, "latency")["default"]["marker"] == "valid"


# ---------------------------------------------------------------------------
# RustProbeBuilder.discover_probes
# ---------------------------------------------------------------------------


class TestDiscoverProbes:
    def test_no_probes_dir_returns_empty(self, tmp_path):
        # Scenario without a probes/ subdirectory
        assert RustProbeBuilder.discover_probes(str(tmp_path)) == []

    def test_single_file_probes_discovered(self, tmp_path):
        probes = tmp_path / "probes"
        probes.mkdir()
        (probes / "check-db.rs").write_text("fn main() {}")
        (probes / "check-cache.rs").write_text("fn main() {}")

        result = RustProbeBuilder.discover_probes(str(tmp_path))
        names = sorted(p["name"] for p in result)
        assert names == ["check-cache", "check-db"]
        assert all(p["kind"] == "single_file" for p in result)
        assert all(p["path"].endswith(".rs") for p in result)

    def test_cargo_probes_discovered(self, tmp_path):
        probes = tmp_path / "probes"
        probes.mkdir()
        cargo_probe = probes / "fancy-probe"
        cargo_probe.mkdir()
        (cargo_probe / "Cargo.toml").write_text("[package]\nname='fancy-probe'\nversion='0.1.0'\n")

        result = RustProbeBuilder.discover_probes(str(tmp_path))
        assert len(result) == 1
        assert result[0]["name"] == "fancy-probe"
        assert result[0]["kind"] == "cargo"

    def test_cargo_dir_without_manifest_skipped(self, tmp_path):
        probes = tmp_path / "probes"
        probes.mkdir()
        # Directory without Cargo.toml is not a probe
        (probes / "not-a-probe").mkdir()
        (probes / "not-a-probe" / "README.md").write_text("nope")

        assert RustProbeBuilder.discover_probes(str(tmp_path)) == []

    def test_mixed_single_file_and_cargo(self, tmp_path):
        probes = tmp_path / "probes"
        probes.mkdir()
        (probes / "quick.rs").write_text("fn main() {}")
        cargo = probes / "deep"
        cargo.mkdir()
        (cargo / "Cargo.toml").write_text("[package]\nname='deep'\nversion='0.1.0'\n")

        result = RustProbeBuilder.discover_probes(str(tmp_path))
        kinds = {p["name"]: p["kind"] for p in result}
        assert kinds == {"quick": "single_file", "deep": "cargo"}

    def test_sorted_alphabetically(self, tmp_path):
        probes = tmp_path / "probes"
        probes.mkdir()
        for name in ["zebra.rs", "alpha.rs", "mike.rs"]:
            (probes / name).write_text("fn main() {}")

        result = RustProbeBuilder.discover_probes(str(tmp_path))
        names = [p["name"] for p in result]
        assert names == ["alpha", "mike", "zebra"]


# ---------------------------------------------------------------------------
# visualize._normalize_strategy / _collect_iteration_data
# ---------------------------------------------------------------------------


def _strategy(
    *,
    iterations=None,
    exp=None,
    agg=None,
    metrics=None,
) -> Dict[str, Any]:
    """Build a raw_strategies[name] entry."""
    return {
        "iterations": iterations or [],
        "experiment": exp or {},
        "aggregated": agg or {},
        "metrics": metrics or {},
    }


class TestNormalizeStrategy:
    def test_single_iteration_uses_experiment_score(self):
        # No ``meanResilienceScore`` key — exp.get() falls through to
        # ``resilienceScore``.  (If we passed meanResilienceScore=None
        # explicitly, dict.get returns None rather than the default,
        # which is the historical behaviour the rest of the pipeline
        # depends on.)
        sdata = _strategy(
            exp={
                "resilienceScore": 85.0,
                "totalExperiments": 1,
                "meanRecoveryTime_ms": 1500.0,
            },
        )
        result = _normalize_strategy("spread", sdata, iterations_count=1)
        assert result["avgResilienceScore"] == 85.0
        assert result["avgMeanRecovery_ms"] == 1500.0
        assert result["runCount"] == 1

    def test_multi_iteration_uses_aggregated_stddev(self):
        sdata = _strategy(
            iterations=[
                {"resilienceScore": 50},
                {"resilienceScore": 70},
                {"resilienceScore": 90},
            ],
            exp={"meanResilienceScore": 70.0},
            agg={
                "stddevResilienceScore": 20.0,
                "minResilienceScore": 50,
                "maxResilienceScore": 90,
            },
        )
        result = _normalize_strategy("colocate", sdata, iterations_count=3)
        assert result["avgResilienceScore"] == 70.0
        assert result["stddevResilienceScore"] == 20.0
        assert result["minResilienceScore"] == 50
        assert result["maxResilienceScore"] == 90

    def test_stddev_computed_from_iterations_when_not_pre_aggregated(self):
        sdata = _strategy(
            iterations=[
                {"resilienceScore": 60},
                {"resilienceScore": 80},
            ],
            exp={"meanResilienceScore": 70.0},
            agg={},  # no pre-aggregated stddev
        )
        result = _normalize_strategy("spread", sdata, iterations_count=2)
        # stdev([60, 80]) ≈ 14.14
        assert abs(result["stddevResilienceScore"] - 14.1) < 0.1

    def test_healthy_only_mean_preferred_when_tainted(self):
        sdata = _strategy(
            iterations=[{"resilienceScore": 90}, {"resilienceScore": 30}],
            exp={"meanResilienceScore": 60.0},
            agg={
                "taintedIterations": 1,
                "allIterationsTainted": False,
                "meanResilienceScore_healthyOnly": 90.0,
            },
        )
        result = _normalize_strategy("adversarial", sdata, iterations_count=2)
        assert result["avgResilienceScore"] == 90.0

    def test_all_tainted_falls_back_to_overall_mean(self):
        sdata = _strategy(
            iterations=[{"resilienceScore": 30}],
            exp={"meanResilienceScore": 30.0},
            agg={
                "taintedIterations": 1,
                "allIterationsTainted": True,
                "meanResilienceScore_healthyOnly": None,
            },
        )
        result = _normalize_strategy("adversarial", sdata, iterations_count=1)
        # all_tainted=True → use exp meanResilienceScore (30.0), not healthy_only
        assert result["avgResilienceScore"] == 30.0

    def test_recovery_falls_back_to_metrics_summary_when_exp_missing(self):
        sdata = _strategy(
            exp={},
            metrics={"recovery": {"summary": {"meanRecovery_ms": 2200.0}}},
        )
        result = _normalize_strategy("baseline", sdata, iterations_count=1)
        assert result["avgMeanRecovery_ms"] == 2200.0

    def test_all_error_strategy_null_score_moments_coerced_to_numeric(self):
        # An all-ERROR strategy reports null score moments (PR #188 keeps null
        # distinct from a fabricated 0.0 in the stats). The chart view must not
        # leak that None to matplotlib, so it coerces to numeric for rendering
        # only.  Single-iteration shape so min/max/stddev
        # come straight from the (null) aggregated fields rather than the
        # iteration-score fallback — exercising every coercion branch.
        sdata = _strategy(
            iterations=[{"resilienceScore": 0, "verdict": "ERROR"}],
            exp={
                "meanResilienceScore": None,
                "stddevResilienceScore": None,
                "minResilienceScore": None,
                "maxResilienceScore": None,
                "totalExperiments": 1,
            },
            agg={
                "meanResilienceScore": None,
                "stddevResilienceScore": None,
                "minResilienceScore": None,
                "maxResilienceScore": None,
                "allIterationsError": True,
            },
        )
        result = _normalize_strategy("colocate", sdata, iterations_count=1)
        assert result["avgResilienceScore"] == 0.0
        assert result["stddevResilienceScore"] == 0.0
        assert result["minResilienceScore"] == 0.0
        assert result["maxResilienceScore"] == 0.0
        # Every score moment must be numeric, not None, so downstream
        # min()/max()/arithmetic in the chart layer can't crash.
        for key in (
            "avgResilienceScore",
            "stddevResilienceScore",
            "minResilienceScore",
            "maxResilienceScore",
        ):
            assert isinstance(result[key], (int, float))


class TestCollectIterationData:
    def test_skips_strategies_with_no_iterations(self):
        raw = {
            "no-iters": {"iterations": []},
            "with-iters": {
                "iterations": [
                    {"resilienceScore": 80, "metrics": {}},
                ]
            },
        }
        result = _collect_iteration_data(raw)
        assert "no-iters" not in result
        assert "with-iters" in result

    def test_collects_scores_and_recovery_times(self):
        raw = {
            "spread": {
                "iterations": [
                    {
                        "resilienceScore": 75,
                        "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1000}}},
                    },
                    {
                        "resilienceScore": 80,
                        "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1200}}},
                    },
                ]
            }
        }
        result = _collect_iteration_data(raw)
        assert result["spread"]["resilienceScores"] == [75, 80]
        assert result["spread"]["recoveryTimes"] == [1000, 1200]

    def test_excludes_error_iterations(self):
        raw = {
            "default": {
                "iterations": [
                    {
                        "verdict": "PASS",
                        "resilienceScore": 83,
                        "metrics": {"recovery": {"summary": {"meanRecovery_ms": 900}}},
                    },
                    {"verdict": "ERROR", "resilienceScore": 0, "metrics": {}},
                    {
                        "verdict": "FAIL",
                        "resilienceScore": 33,
                        "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1500}}},
                    },
                ]
            }
        }
        result = _collect_iteration_data(raw)
        # The ERROR iteration's meaningless 0.0 is dropped from the points.
        assert result["default"]["resilienceScores"] == [83, 33]
        assert result["default"]["recoveryTimes"] == [900, 1500]

    def test_strategy_with_only_error_iterations_omitted(self):
        raw = {
            "broken": {"iterations": [{"verdict": "ERROR", "resilienceScore": 0, "metrics": {}}]}
        }
        assert "broken" not in _collect_iteration_data(raw)

    def test_iteration_with_no_recovery_data_skipped_in_recovery_list(self):
        raw = {
            "spread": {
                "iterations": [
                    {"resilienceScore": 75, "metrics": {}},
                    {
                        "resilienceScore": 80,
                        "metrics": {"recovery": {"summary": {"meanRecovery_ms": 1200}}},
                    },
                ]
            }
        }
        result = _collect_iteration_data(raw)
        assert result["spread"]["resilienceScores"] == [75, 80]
        assert result["spread"]["recoveryTimes"] == [1200]


# ---------------------------------------------------------------------------
# setup.UnknownExperimentType
# ---------------------------------------------------------------------------


class TestUnknownExperimentType:
    def test_unknown_type_raises(self):
        from chaosprobe.provisioner.setup import LitmusSetup, UnknownExperimentType

        # skip_k8s_init=True so we don't need a cluster
        setup = LitmusSetup(skip_k8s_init=True)
        try:
            setup.install_experiment("not-a-real-fault", "default")
        except UnknownExperimentType as exc:
            assert "not-a-real-fault" in str(exc)
            assert "Supported:" in str(exc)
            return
        raise AssertionError("expected UnknownExperimentType")

    def test_known_types_in_catalog(self):
        from chaosprobe.provisioner.setup import LitmusSetup

        assert "pod-delete" in LitmusSetup.EXPERIMENT_URLS
        assert "pod-cpu-hog" in LitmusSetup.EXPERIMENT_URLS
        # Catalog covers both pod-level and node-level faults
        assert any(k.startswith("node-") for k in LitmusSetup.EXPERIMENT_URLS)
        assert any(k.startswith("pod-") for k in LitmusSetup.EXPERIMENT_URLS)


# ---------------------------------------------------------------------------
# output.SCHEMA_VERSION
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    def test_constant_exposed_at_package_root(self):
        from chaosprobe.output import SCHEMA_VERSION

        assert SCHEMA_VERSION == "2.0.0"

    def test_output_generator_class_attr_matches(self):
        from chaosprobe.output import SCHEMA_VERSION
        from chaosprobe.output.generator import OutputGenerator

        assert OutputGenerator.SCHEMA_VERSION == SCHEMA_VERSION

    def test_comparison_emits_schema_version(self):
        from chaosprobe.output import SCHEMA_VERSION
        from chaosprobe.output.comparison import compare_runs

        result = compare_runs({"summary": {}}, {"summary": {}})
        assert result["schemaVersion"] == SCHEMA_VERSION


# touched-but-unused fixture for collector ordering — avoid lint warnings
_ = Path
