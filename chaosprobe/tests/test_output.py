"""Tests for output generation and comparison."""

import pytest

from chaosprobe.output.generator import OutputGenerator
from chaosprobe.output.comparison import compare_runs


class TestOutputGenerator:
    """Tests for the OutputGenerator class."""

    def test_generate_output_structure(self, sample_scenario, failed_results):
        """Test that output has the correct top-level structure."""
        generator = OutputGenerator(sample_scenario, failed_results)
        output = generator.generate()

        assert output["schemaVersion"] == "2.0.0"
        assert "runId" in output
        assert "timestamp" in output
        assert "scenario" in output
        assert "infrastructure" in output
        assert "experiments" in output
        assert "summary" in output

    def test_generate_scenario_section(self, sample_scenario, sample_results):
        """Test scenario section includes file contents."""
        generator = OutputGenerator(sample_scenario, sample_results)
        output = generator.generate()

        assert output["scenario"]["directory"] == sample_scenario["path"]
        assert len(output["scenario"]["manifests"]) == 2
        assert len(output["scenario"]["experiments"]) == 1
        # Verify manifest content is included
        manifest = output["scenario"]["manifests"][0]
        assert "file" in manifest
        assert "content" in manifest
        assert manifest["content"]["kind"] == "Deployment"
        # Verify experiment content is included
        experiment = output["scenario"]["experiments"][0]
        assert experiment["content"]["kind"] == "ChaosEngine"

    def test_generate_infrastructure_section(self, sample_scenario, sample_results):
        """Test infrastructure section contains namespace."""
        generator = OutputGenerator(sample_scenario, sample_results)
        output = generator.generate()

        infra = output["infrastructure"]
        assert infra["namespace"] == "test-namespace"

    def test_generate_passing_summary(self, sample_scenario, sample_results):
        """Test summary for passing experiments."""
        generator = OutputGenerator(sample_scenario, sample_results)
        output = generator.generate()

        assert output["summary"]["overallVerdict"] == "PASS"
        assert output["summary"]["passed"] == 1
        assert output["summary"]["failed"] == 0
        assert output["summary"]["resilienceScore"] == 95.0

    def test_generate_failing_summary(self, sample_scenario, failed_results):
        """Test summary for failing experiments."""
        generator = OutputGenerator(sample_scenario, failed_results)
        output = generator.generate()

        assert output["summary"]["overallVerdict"] == "FAIL"
        assert output["summary"]["passed"] == 0
        assert output["summary"]["failed"] == 1

    def test_generate_minimal_output(self, sample_scenario, sample_results):
        """Test generating minimal output format."""
        generator = OutputGenerator(sample_scenario, sample_results)
        output = generator.generate_minimal()

        assert "runId" in output
        assert output["verdict"] == "PASS"
        assert output["resilienceScore"] == 95.0
        assert output["issueDetected"] is False

    def test_experiment_details(self, sample_scenario, failed_results):
        """Test that experiment section includes probe details."""
        generator = OutputGenerator(sample_scenario, failed_results)
        output = generator.generate()

        experiments = output["experiments"]
        assert len(experiments) == 1
        assert experiments[0]["name"] == "pod-delete"
        assert experiments[0]["result"]["verdict"] == "Fail"
        assert experiments[0]["result"]["probeSuccessPercentage"] == 0


class TestComparison:
    """Tests for run comparison."""

    def test_compare_runs_improvement(self):
        """Test comparing baseline (FAIL) with improved after-fix (PASS)."""
        baseline = {
            "runId": "baseline-123",
            "timestamp": "2025-01-18T10:00:00Z",
            "scenario": {"directory": "/tmp/test"},
            "experiments": [
                {
                    "name": "pod-delete",
                    "result": {
                        "verdict": "Fail",
                        "probeSuccessPercentage": 0,
                    },
                }
            ],
            "summary": {
                "resilienceScore": 0.0,
                "overallVerdict": "FAIL",
            },
        }

        after_fix = {
            "runId": "afterfix-456",
            "timestamp": "2025-01-18T11:00:00Z",
            "scenario": {"directory": "/tmp/test"},
            "experiments": [
                {
                    "name": "pod-delete",
                    "result": {
                        "verdict": "Pass",
                        "probeSuccessPercentage": 95,
                    },
                }
            ],
            "summary": {
                "resilienceScore": 95.0,
                "overallVerdict": "PASS",
            },
        }

        comparison = compare_runs(baseline, after_fix)

        assert comparison["schemaVersion"] == "2.0.0"
        assert comparison["comparison"]["resilienceScoreChange"] == 95.0
        assert comparison["comparison"]["verdictChanged"] is True
        assert comparison["conclusion"]["fixEffective"] is True
        assert comparison["conclusion"]["confidence"] > 0.7

    def test_compare_runs_no_improvement(self):
        """Test comparing runs with no meaningful improvement."""
        baseline = {
            "runId": "baseline-123",
            "timestamp": "2025-01-18T10:00:00Z",
            "scenario": {"directory": "/tmp/test"},
            "experiments": [
                {
                    "name": "pod-delete",
                    "result": {
                        "verdict": "Fail",
                        "probeSuccessPercentage": 0,
                    },
                }
            ],
            "summary": {
                "resilienceScore": 0.0,
                "overallVerdict": "FAIL",
            },
        }

        after_fix = {
            "runId": "afterfix-456",
            "timestamp": "2025-01-18T11:00:00Z",
            "scenario": {"directory": "/tmp/test"},
            "experiments": [
                {
                    "name": "pod-delete",
                    "result": {
                        "verdict": "Fail",
                        "probeSuccessPercentage": 5,
                    },
                }
            ],
            "summary": {
                "resilienceScore": 5.0,
                "overallVerdict": "FAIL",
            },
        }

        comparison = compare_runs(baseline, after_fix)

        assert comparison["comparison"]["resilienceScoreChange"] == 5.0
        assert comparison["comparison"]["verdictChanged"] is False
        assert comparison["conclusion"]["fixEffective"] is False

    def test_compare_runs_partial_fix(self):
        """Test comparing runs where verdict changed."""
        baseline = {
            "runId": "baseline",
            "timestamp": "2025-01-18T10:00:00Z",
            "scenario": {"directory": "/tmp/test"},
            "experiments": [
                {
                    "name": "pod-delete",
                    "result": {
                        "verdict": "Fail",
                        "probeSuccessPercentage": 0,
                    },
                }
            ],
            "summary": {
                "resilienceScore": 0.0,
                "overallVerdict": "FAIL",
            },
        }

        after_fix = {
            "runId": "afterfix",
            "timestamp": "2025-01-18T11:00:00Z",
            "scenario": {"directory": "/tmp/test"},
            "experiments": [
                {
                    "name": "pod-delete",
                    "result": {
                        "verdict": "Pass",
                        "probeSuccessPercentage": 85,
                    },
                }
            ],
            "summary": {
                "resilienceScore": 85.0,
                "overallVerdict": "PASS",
            },
        }

        comparison = compare_runs(baseline, after_fix)

        assert comparison["conclusion"]["fixEffective"] is True
        assert comparison["comparison"]["verdictChanged"] is True

    def test_compare_recovery_metrics(self):
        """Test that recovery time comparison is included when metrics present."""
        baseline = {
            "runId": "b", "timestamp": "T", "scenario": {},
            "experiments": [],
            "summary": {"resilienceScore": 50, "overallVerdict": "FAIL"},
            "metrics": {
                "recovery": {"summary": {
                    "meanRecovery_ms": 3000.0,
                    "p95Recovery_ms": 4000.0,
                }},
            },
        }
        after_fix = {
            "runId": "a", "timestamp": "T", "scenario": {},
            "experiments": [],
            "summary": {"resilienceScore": 90, "overallVerdict": "PASS"},
            "metrics": {
                "recovery": {"summary": {
                    "meanRecovery_ms": 1500.0,
                    "p95Recovery_ms": 2000.0,
                }},
            },
        }
        comparison = compare_runs(baseline, after_fix)
        rec = comparison["comparison"]["metrics"]["recovery"]
        assert rec["baseline"]["meanRecovery_ms"] == 3000.0
        assert rec["afterFix"]["meanRecovery_ms"] == 1500.0
        assert rec["meanChange_ms"] == -1500.0
        assert rec["improved"] is True

    def test_compare_latency_metrics(self):
        """Test that latency comparison is included for shared routes."""
        baseline = {
            "runId": "b", "timestamp": "T", "scenario": {},
            "experiments": [],
            "summary": {"resilienceScore": 50, "overallVerdict": "FAIL"},
            "metrics": {
                "latency": {"phases": {"during-chaos": {"routes": {
                    "frontend→cart": {"mean_ms": 50.0},
                }}}},
            },
        }
        after_fix = {
            "runId": "a", "timestamp": "T", "scenario": {},
            "experiments": [],
            "summary": {"resilienceScore": 90, "overallVerdict": "PASS"},
            "metrics": {
                "latency": {"phases": {"during-chaos": {"routes": {
                    "frontend→cart": {"mean_ms": 30.0},
                }}}},
            },
        }
        comparison = compare_runs(baseline, after_fix)
        lat = comparison["comparison"]["metrics"]["latency"]
        assert lat["allImproved"] is True
        assert lat["routes"][0]["change_ms"] == -20.0

    def test_compare_resource_metrics(self):
        """Test that resource utilization comparison is included."""
        def _make_run(score, verdict, cpu, mem):
            return {
                "runId": "r", "timestamp": "T", "scenario": {},
                "experiments": [],
                "summary": {"resilienceScore": score, "overallVerdict": verdict},
                "metrics": {
                    "resources": {
                        "available": True,
                        "phases": {"during-chaos": {"node": {
                            "meanCpu_percent": cpu,
                            "meanMemory_percent": mem,
                        }}},
                    },
                },
            }

        comparison = compare_runs(
            _make_run(50, "FAIL", 85.0, 70.0),
            _make_run(90, "PASS", 60.0, 55.0),
        )
        res = comparison["comparison"]["metrics"]["resources"]
        assert res["cpuChange_percent"] == -25.0
        assert res["memoryChange_percent"] == -15.0

    def test_compare_no_metrics_section_when_absent(self):
        """Test that metrics comparison is empty when no metrics data."""
        baseline = {
            "runId": "b", "timestamp": "T", "scenario": {},
            "experiments": [],
            "summary": {"resilienceScore": 50, "overallVerdict": "FAIL"},
        }
        after_fix = {
            "runId": "a", "timestamp": "T", "scenario": {},
            "experiments": [],
            "summary": {"resilienceScore": 90, "overallVerdict": "PASS"},
        }
        comparison = compare_runs(baseline, after_fix)
        assert comparison["comparison"]["metrics"] == {}
