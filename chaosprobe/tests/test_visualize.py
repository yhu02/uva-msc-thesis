"""Tests for the visualization module."""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from chaosprobe.output.visualize import (
    generate_from_summary,
    generate_all_charts,
    _strategy_colors,
    _chart_resilience_scores,
    _chart_recovery_times,
    _chart_latency_by_strategy,
    _chart_latency_degradation,
    _extract_latency_data,
    _chart_throughput_by_strategy,
    _chart_throughput_degradation,
    _extract_throughput_data,
    _generate_html_summary,
)


# Skip all tests if matplotlib is not installed
pytest.importorskip("matplotlib")


class TestStrategyColors:
    def test_known_strategies(self):
        colors = _strategy_colors(["baseline", "colocate", "spread"])
        assert len(colors) == 3
        assert colors[0] == "#2196F3"  # baseline
        assert colors[1] == "#F44336"  # colocate
        assert colors[2] == "#4CAF50"  # spread

    def test_unknown_strategies(self):
        colors = _strategy_colors(["custom1", "custom2"])
        assert len(colors) == 2


class TestChartGeneration:
    @pytest.fixture
    def strategies(self):
        return {
            "baseline": {
                "avgResilienceScore": 85.0,
                "passRate": 0.8,
                "avgMeanRecovery_ms": 1200.0,
                "avgP95Recovery_ms": 2000.0,
                "runCount": 5,
                "avgLoadP95_ms": None,
                "avgLoadErrorRate": None,
            },
            "colocate": {
                "avgResilienceScore": 70.0,
                "passRate": 0.6,
                "avgMeanRecovery_ms": 2500.0,
                "avgP95Recovery_ms": 4000.0,
                "runCount": 5,
                "avgLoadP95_ms": None,
                "avgLoadErrorRate": None,
            },
            "spread": {
                "avgResilienceScore": 95.0,
                "passRate": 1.0,
                "avgMeanRecovery_ms": 800.0,
                "avgP95Recovery_ms": 1200.0,
                "runCount": 5,
                "avgLoadP95_ms": None,
                "avgLoadErrorRate": None,
            },
        }

    def test_resilience_scores_chart(self, strategies, tmp_path):
        path = _chart_resilience_scores(strategies, tmp_path)
        assert path is not None
        assert os.path.exists(path)
        assert path.endswith(".png")

    def test_recovery_times_chart(self, strategies, tmp_path):
        path = _chart_recovery_times(strategies, tmp_path)
        assert path is not None
        assert os.path.exists(path)

    def test_resilience_scores_empty(self, tmp_path):
        path = _chart_resilience_scores(
            {"baseline": {"avgResilienceScore": 0}}, tmp_path
        )
        assert path is None

    def test_recovery_times_no_data(self, tmp_path):
        path = _chart_recovery_times(
            {"baseline": {"avgMeanRecovery_ms": None}}, tmp_path
        )
        assert path is None

    def test_html_summary(self, strategies, tmp_path):
        # Create a fake PNG file
        fake_chart = str(tmp_path / "resilience_scores.png")
        open(fake_chart, "w").close()

        path = _generate_html_summary([fake_chart], strategies, tmp_path)
        assert path is not None
        assert path.endswith(".html")

        with open(path) as f:
            content = f.read()
        assert "ChaosProbe" in content
        assert "baseline" in content
        assert "colocate" in content

    def test_html_summary_no_charts(self, strategies, tmp_path):
        path = _generate_html_summary([], strategies, tmp_path)
        assert path is None


class TestGenerateFromSummary:
    def test_generate_from_summary_file(self, tmp_path):
        summary = {
            "comparison": [
                {
                    "strategy": "baseline",
                    "verdict": "PASS",
                    "resilienceScore": 90.0,
                    "avgRecovery_ms": 1500.0,
                    "maxRecovery_ms": 2500.0,
                    "status": "completed",
                },
                {
                    "strategy": "colocate",
                    "verdict": "FAIL",
                    "resilienceScore": 60.0,
                    "avgRecovery_ms": 3000.0,
                    "maxRecovery_ms": 5000.0,
                    "status": "completed",
                },
            ]
        }

        summary_file = tmp_path / "summary.json"
        summary_file.write_text(json.dumps(summary))
        charts_dir = str(tmp_path / "charts")

        generated = generate_from_summary(str(summary_file), charts_dir)
        assert len(generated) > 0
        assert any(p.endswith(".html") for p in generated)
        assert any(p.endswith(".png") for p in generated)

    def test_generate_from_empty_summary(self, tmp_path):
        summary = {"comparison": []}
        summary_file = tmp_path / "summary.json"
        summary_file.write_text(json.dumps(summary))

        generated = generate_from_summary(str(summary_file), str(tmp_path / "charts"))
        assert generated == []


class TestGenerateAllCharts:
    def test_generate_from_db(self, tmp_path):
        from chaosprobe.storage.sqlite import SQLiteStore

        db_path = str(tmp_path / "test.db")
        store = SQLiteStore(db_path=db_path)

        # Insert sample runs
        for strategy in ["baseline", "colocate", "spread"]:
            store.save_run({
                "runId": f"run-{strategy}",
                "timestamp": "2026-03-20T12:00:00+00:00",
                "scenario": {"directory": "/scenarios/test"},
                "infrastructure": {"namespace": "test"},
                "experiments": [],
                "summary": {
                    "totalExperiments": 1,
                    "passed": 1,
                    "failed": 0,
                    "resilienceScore": 90.0 if strategy == "spread" else 70.0,
                    "overallVerdict": "PASS",
                },
                "metrics": {
                    "recovery": {
                        "summary": {
                            "meanRecovery_ms": 1000.0,
                            "p95Recovery_ms": 2000.0,
                        }
                    }
                },
                "placement": {"strategy": strategy, "assignments": {}},
            })

        charts_dir = str(tmp_path / "charts")
        generated = generate_all_charts(store, charts_dir)
        store.close()

        assert len(generated) > 0
        assert any(p.endswith(".html") for p in generated)


class TestLatencyCharts:
    @pytest.fixture
    def latency_by_strategy(self):
        return {
            "baseline": {
                "phases": {
                    "pre-chaos": {
                        "sampleCount": 5,
                        "routes": {
                            "/": {"mean_ms": 50.0, "median_ms": 48.0, "p95_ms": 65.0,
                                  "min_ms": 40.0, "max_ms": 70.0, "sampleCount": 5, "errorCount": 0},
                            "/product/OLJCESPC7Z": {"mean_ms": 80.0, "median_ms": 75.0, "p95_ms": 100.0,
                                                     "min_ms": 60.0, "max_ms": 110.0, "sampleCount": 5, "errorCount": 0},
                        },
                    },
                    "during-chaos": {
                        "sampleCount": 10,
                        "routes": {
                            "/": {"mean_ms": 120.0, "median_ms": 110.0, "p95_ms": 200.0,
                                  "min_ms": 80.0, "max_ms": 250.0, "sampleCount": 10, "errorCount": 2},
                            "/product/OLJCESPC7Z": {"mean_ms": 350.0, "median_ms": 300.0, "p95_ms": 500.0,
                                                     "min_ms": 150.0, "max_ms": 600.0, "sampleCount": 10, "errorCount": 3},
                        },
                    },
                    "post-chaos": {
                        "sampleCount": 3,
                        "routes": {
                            "/": {"mean_ms": 55.0, "median_ms": 52.0, "p95_ms": 68.0,
                                  "min_ms": 42.0, "max_ms": 72.0, "sampleCount": 3, "errorCount": 0},
                        },
                    },
                },
            },
            "colocate": {
                "phases": {
                    "pre-chaos": {
                        "sampleCount": 5,
                        "routes": {
                            "/": {"mean_ms": 30.0, "median_ms": 28.0, "p95_ms": 40.0,
                                  "min_ms": 20.0, "max_ms": 45.0, "sampleCount": 5, "errorCount": 0},
                        },
                    },
                    "during-chaos": {
                        "sampleCount": 10,
                        "routes": {
                            "/": {"mean_ms": 250.0, "median_ms": 230.0, "p95_ms": 400.0,
                                  "min_ms": 100.0, "max_ms": 500.0, "sampleCount": 10, "errorCount": 4},
                        },
                    },
                    "post-chaos": {"sampleCount": 0, "routes": {}},
                },
            },
        }

    def test_chart_latency_by_strategy(self, latency_by_strategy, tmp_path):
        path = _chart_latency_by_strategy(latency_by_strategy, tmp_path)
        assert path is not None
        assert os.path.exists(path)
        assert path.endswith("latency_by_strategy.png")

    def test_chart_latency_degradation(self, latency_by_strategy, tmp_path):
        path = _chart_latency_degradation(latency_by_strategy, tmp_path)
        assert path is not None
        assert os.path.exists(path)
        assert path.endswith("latency_degradation.png")

    def test_chart_latency_by_strategy_no_data(self, tmp_path):
        path = _chart_latency_by_strategy({}, tmp_path)
        assert path is None

    def test_chart_latency_degradation_no_pre_chaos(self, tmp_path):
        data = {
            "baseline": {
                "phases": {
                    "during-chaos": {
                        "sampleCount": 5,
                        "routes": {"/": {"mean_ms": 100.0}},
                    },
                },
            },
        }
        path = _chart_latency_degradation(data, tmp_path)
        assert path is None

    def test_html_summary_with_latency(self, latency_by_strategy, tmp_path):
        fake_chart = str(tmp_path / "latency_by_strategy.png")
        open(fake_chart, "w").close()

        strategies = {
            "baseline": {"avgResilienceScore": 80.0, "passRate": 0.8,
                         "avgMeanRecovery_ms": 1200.0, "avgP95Recovery_ms": 2000.0,
                         "medianRecovery_ms": 1100.0, "runCount": 1},
        }

        path = _generate_html_summary(
            [fake_chart], strategies, tmp_path,
            latency_data=latency_by_strategy
        )
        assert path is not None
        with open(path) as f:
            content = f.read()
        assert "Inter-Service Latency" in content
        assert "Pre-Chaos Mean" in content
        assert "During Chaos Mean" in content

    def test_extract_latency_data_single(self):
        raw = {
            "baseline": {
                "metrics": {
                    "latency": {
                        "phases": {"during-chaos": {"routes": {"/": {"mean_ms": 100}}}},
                    },
                },
            },
        }
        result = _extract_latency_data(raw)
        assert "baseline" in result
        assert result["baseline"]["phases"]["during-chaos"]["routes"]["/"]["mean_ms"] == 100

    def test_extract_latency_data_iterations(self):
        raw = {
            "spread": {
                "iterations": [
                    {"metrics": {"recovery": {}}},
                    {"metrics": {"latency": {"phases": {"during-chaos": {"routes": {}}}}}},
                ],
            },
        }
        result = _extract_latency_data(raw)
        assert "spread" in result

    def test_extract_latency_data_empty(self):
        result = _extract_latency_data({"baseline": {"metrics": {}}})
        assert result == {}


class TestThroughputCharts:
    @pytest.fixture
    def throughput_by_strategy(self):
        return {
            "baseline": {
                "phases": {
                    "pre-chaos": {
                        "sampleCount": 3,
                        "redis": {
                            "write": {"meanOpsPerSecond": 5000, "sampleCount": 3, "errorCount": 0},
                            "read": {"meanOpsPerSecond": 8000, "sampleCount": 3, "errorCount": 0},
                        },
                        "disk": {
                            "write": {"meanOpsPerSecond": 100, "meanBytesPerSecond": 100000000,
                                      "sampleCount": 3, "errorCount": 0},
                        },
                    },
                    "during-chaos": {
                        "sampleCount": 5,
                        "redis": {
                            "write": {"meanOpsPerSecond": 2000, "sampleCount": 5, "errorCount": 1},
                            "read": {"meanOpsPerSecond": 3500, "sampleCount": 5, "errorCount": 0},
                        },
                        "disk": {
                            "write": {"meanOpsPerSecond": 30, "meanBytesPerSecond": 30000000,
                                      "sampleCount": 5, "errorCount": 2},
                        },
                    },
                    "post-chaos": {
                        "sampleCount": 2,
                        "redis": {
                            "write": {"meanOpsPerSecond": 4800, "sampleCount": 2, "errorCount": 0},
                        },
                        "disk": {},
                    },
                },
            },
            "colocate": {
                "phases": {
                    "pre-chaos": {
                        "sampleCount": 3,
                        "redis": {
                            "write": {"meanOpsPerSecond": 5200, "sampleCount": 3, "errorCount": 0},
                        },
                        "disk": {},
                    },
                    "during-chaos": {
                        "sampleCount": 5,
                        "redis": {
                            "write": {"meanOpsPerSecond": 1500, "sampleCount": 5, "errorCount": 2},
                        },
                        "disk": {},
                    },
                    "post-chaos": {"sampleCount": 0, "redis": {}, "disk": {}},
                },
            },
        }

    def test_chart_throughput_by_strategy(self, throughput_by_strategy, tmp_path):
        path = _chart_throughput_by_strategy(throughput_by_strategy, tmp_path)
        assert path is not None
        assert os.path.exists(path)
        assert path.endswith("throughput_by_strategy.png")

    def test_chart_throughput_degradation(self, throughput_by_strategy, tmp_path):
        path = _chart_throughput_degradation(throughput_by_strategy, tmp_path)
        assert path is not None
        assert os.path.exists(path)
        assert path.endswith("throughput_degradation.png")

    def test_chart_throughput_by_strategy_no_data(self, tmp_path):
        path = _chart_throughput_by_strategy({}, tmp_path)
        assert path is None

    def test_chart_throughput_degradation_no_pre_chaos(self, tmp_path):
        data = {
            "baseline": {
                "phases": {
                    "during-chaos": {
                        "sampleCount": 5,
                        "redis": {"write": {"meanOpsPerSecond": 2000}},
                    },
                },
            },
        }
        path = _chart_throughput_degradation(data, tmp_path)
        assert path is None

    def test_html_summary_with_throughput(self, throughput_by_strategy, tmp_path):
        fake_chart = str(tmp_path / "throughput_by_strategy.png")
        open(fake_chart, "w").close()

        strategies = {
            "baseline": {"avgResilienceScore": 80.0, "passRate": 0.8,
                         "avgMeanRecovery_ms": 1200.0, "avgP95Recovery_ms": 2000.0,
                         "medianRecovery_ms": 1100.0, "runCount": 1},
        }

        path = _generate_html_summary(
            [fake_chart], strategies, tmp_path,
            throughput_data=throughput_by_strategy,
        )
        assert path is not None
        with open(path) as f:
            content = f.read()
        assert "I/O Throughput" in content
        assert "redis-write" in content or "Ops/sec" in content

    def test_extract_throughput_data_single(self):
        raw = {
            "baseline": {
                "metrics": {
                    "throughput": {
                        "phases": {"during-chaos": {"redis": {"write": {"meanOpsPerSecond": 2000}}}},
                    },
                },
            },
        }
        result = _extract_throughput_data(raw)
        assert "baseline" in result

    def test_extract_throughput_data_iterations(self):
        raw = {
            "spread": {
                "iterations": [
                    {"metrics": {"recovery": {}}},
                    {"metrics": {"throughput": {"phases": {"during-chaos": {"redis": {}}}}}},
                ],
            },
        }
        result = _extract_throughput_data(raw)
        assert "spread" in result

    def test_extract_throughput_data_empty(self):
        result = _extract_throughput_data({"baseline": {"metrics": {}}})
        assert result == {}
