"""Tests for ``chaosprobe power`` — sample-size calculator."""

import json

from click.testing import CliRunner

from chaosprobe.commands.power_cmd import _analyse_strategy, _required_n, power


class TestRequiredN:
    def test_zero_delta_returns_zero(self):
        assert _required_n(stddev=10, delta=0, alpha=0.05, power=0.80) == 0

    def test_zero_stddev_returns_zero(self):
        assert _required_n(stddev=0, delta=5, alpha=0.05, power=0.80) == 0

    def test_small_delta_needs_many_iterations(self):
        # Small effect size, normal stddev → large required n.
        n = _required_n(stddev=10, delta=1, alpha=0.05, power=0.80)
        assert n > 200

    def test_large_delta_needs_few_iterations(self):
        # Large effect size → small required n (bounded at >= 2).
        n = _required_n(stddev=10, delta=20, alpha=0.05, power=0.80)
        assert 2 <= n <= 10

    def test_tighter_alpha_increases_n(self):
        n_05 = _required_n(stddev=10, delta=5, alpha=0.05, power=0.80)
        n_01 = _required_n(stddev=10, delta=5, alpha=0.01, power=0.80)
        assert n_01 > n_05

    def test_higher_power_increases_n(self):
        n_80 = _required_n(stddev=10, delta=5, alpha=0.05, power=0.80)
        n_95 = _required_n(stddev=10, delta=5, alpha=0.05, power=0.95)
        assert n_95 > n_80


class TestAnalyseStrategy:
    def test_resilience_strategy_analysed(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {
                "meanResilienceScore": 75.0,
                "stddevResilienceScore": 10.0,
            },
        }
        out = _analyse_strategy("colocate", sdata, "resilience", delta=5.0, alpha=0.05, power=0.80)
        assert out["currentN"] == 5
        assert out["currentStddev"] == 10.0
        assert out["requiredN"] > 0
        # δ=5 stddev=10 → need more than n=5.
        assert out["status"] == "insufficient"

    def test_strategy_with_no_data(self):
        sdata = {"iterations": [{}], "aggregated": {}}
        out = _analyse_strategy("colocate", sdata, "resilience", 5.0, 0.05, 0.80)
        assert out["status"] == "no-data"
        assert out["requiredN"] is None

    def test_strategy_with_zero_stddev(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {
                "meanResilienceScore": 75.0,
                "stddevResilienceScore": 0.0,
            },
        }
        out = _analyse_strategy("colocate", sdata, "resilience", 5.0, 0.05, 0.80)
        assert out["requiredN"] == 2
        assert "trivial" in out["status"]

    def test_recovery_metric(self):
        sdata = {
            "iterations": [{}] * 10,
            "aggregated": {
                "meanRecoveryTime_ms": 1200.0,
                "stddevRecoveryTime_ms": 200.0,
            },
        }
        out = _analyse_strategy("colocate", sdata, "recovery", delta=100.0, alpha=0.05, power=0.80)
        assert out["currentStddev"] == 200.0
        assert out["currentMean"] == 1200.0

    def test_achieved_status_when_current_n_sufficient(self):
        sdata = {
            "iterations": [{}] * 1000,
            "aggregated": {
                "meanResilienceScore": 75.0,
                "stddevResilienceScore": 10.0,
            },
        }
        out = _analyse_strategy("colocate", sdata, "resilience", delta=5.0, alpha=0.05, power=0.80)
        assert out["status"] == "achieved"


class TestPowerCommand:
    def test_text_output_basic(self, tmp_path):
        path = tmp_path / "summary.json"
        path.write_text(
            json.dumps(
                {
                    "strategies": {
                        "colocate": {
                            "iterations": [{}] * 5,
                            "aggregated": {
                                "meanResilienceScore": 75.0,
                                "stddevResilienceScore": 10.0,
                            },
                        }
                    }
                }
            )
        )
        runner = CliRunner()
        result = runner.invoke(power, ["-s", str(path)])
        assert result.exit_code == 0
        assert "Power analysis" in result.output
        assert "colocate" in result.output
        assert "approximate" in result.output

    def test_json_output(self, tmp_path):
        path = tmp_path / "summary.json"
        path.write_text(
            json.dumps(
                {
                    "strategies": {
                        "colocate": {
                            "iterations": [{}] * 5,
                            "aggregated": {
                                "meanResilienceScore": 75.0,
                                "stddevResilienceScore": 10.0,
                            },
                        }
                    }
                }
            )
        )
        runner = CliRunner()
        result = runner.invoke(power, ["-s", str(path), "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "perStrategy" in payload
        assert "colocate" in payload["perStrategy"]
        assert "note" in payload

    def test_custom_target_delta(self, tmp_path):
        path = tmp_path / "summary.json"
        path.write_text(
            json.dumps(
                {
                    "strategies": {
                        "a": {
                            "iterations": [{}] * 5,
                            "aggregated": {
                                "meanResilienceScore": 75.0,
                                "stddevResilienceScore": 10.0,
                            },
                        }
                    }
                }
            )
        )
        runner = CliRunner()
        result_small = runner.invoke(power, ["-s", str(path), "--target-delta", "2", "--json"])
        result_large = runner.invoke(power, ["-s", str(path), "--target-delta", "20", "--json"])
        assert result_small.exit_code == 0
        assert result_large.exit_code == 0
        small_n = json.loads(result_small.output)["perStrategy"]["a"]["requiredN"]
        large_n = json.loads(result_large.output)["perStrategy"]["a"]["requiredN"]
        assert small_n > large_n  # Tighter target → more iterations needed.

    def test_recovery_metric_flag(self, tmp_path):
        path = tmp_path / "summary.json"
        path.write_text(
            json.dumps(
                {
                    "strategies": {
                        "a": {
                            "iterations": [{}] * 5,
                            "aggregated": {
                                "meanRecoveryTime_ms": 1200.0,
                                "stddevRecoveryTime_ms": 200.0,
                            },
                        }
                    }
                }
            )
        )
        runner = CliRunner()
        result = runner.invoke(
            power, ["-s", str(path), "--metric", "recovery", "--target-delta", "100", "--json"]
        )
        assert result.exit_code == 0
        assert "meanRecovery_ms" in result.output or "recovery" in result.output
