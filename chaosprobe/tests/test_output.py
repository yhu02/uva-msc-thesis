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
        """Test scenario section references files correctly."""
        generator = OutputGenerator(sample_scenario, sample_results)
        output = generator.generate()

        assert output["scenario"]["directory"] == sample_scenario["path"]
        assert len(output["scenario"]["manifestFiles"]) == 2
        assert len(output["scenario"]["experimentFiles"]) == 1

    def test_generate_infrastructure_section(self, sample_scenario, sample_results):
        """Test infrastructure section lists deployed resources."""
        generator = OutputGenerator(sample_scenario, sample_results)
        output = generator.generate()

        infra = output["infrastructure"]
        assert infra["namespace"] == "test-namespace"
        assert len(infra["resources"]) == 2

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
