"""Tests for output generation."""

import pytest

from chaosprobe.output.generator import OutputGenerator
from chaosprobe.output.comparison import compare_runs


class TestOutputGenerator:
    """Tests for the OutputGenerator class."""

    def test_generate_output(self):
        """Test generating complete output."""
        scenario = {
            "metadata": {
                "name": "test-scenario",
                "description": "Test description",
            },
            "spec": {
                "infrastructure": {
                    "namespace": "test-ns",
                    "resources": [
                        {
                            "name": "test-deployment",
                            "type": "deployment",
                            "anomaly": {
                                "enabled": True,
                                "type": "missing-readiness-probe",
                            },
                        }
                    ],
                },
                "experiments": [
                    {
                        "name": "test-experiment",
                        "type": "pod-delete",
                    }
                ],
                "successCriteria": {
                    "minResilienceScore": 80,
                    "requireAllPass": True,
                },
            },
        }

        results = [
            {
                "name": "test-experiment",
                "type": "pod-delete",
                "verdict": "Fail",
                "probeSuccessPercentage": 65.0,
                "chaosResult": {
                    "phase": "Completed",
                    "verdict": "Fail",
                    "failStep": "ChaosInject",
                },
            }
        ]

        generator = OutputGenerator(scenario, results, with_anomaly=True)
        output = generator.generate()

        assert "schemaVersion" in output
        assert "runId" in output
        assert "timestamp" in output
        assert output["scenario"]["name"] == "test-scenario"
        assert output["summary"]["overallVerdict"] == "FAIL"
        assert output["summary"]["resilienceScore"] == 65.0

    def test_generate_minimal_output(self):
        """Test generating minimal output format."""
        scenario = {
            "metadata": {"name": "test"},
            "spec": {
                "infrastructure": {
                    "namespace": "test-ns",
                    "resources": [
                        {
                            "name": "test-deployment",
                            "type": "deployment",
                            "anomaly": {
                                "enabled": True,
                                "type": "missing-readiness-probe",
                            },
                        }
                    ],
                },
                "experiments": [{"name": "test-exp", "type": "pod-delete"}],
                "successCriteria": {},
            },
        }

        results = [
            {
                "name": "test-exp",
                "verdict": "Pass",
                "probeSuccessPercentage": 95.0,
            }
        ]

        generator = OutputGenerator(scenario, results, with_anomaly=True)
        output = generator.generate_minimal()

        assert "runId" in output
        assert output["verdict"] == "PASS"
        assert output["resilienceScore"] == 95.0
        assert output["anomaly"]["type"] == "missing-readiness-probe"

    def test_ai_analysis_hints(self):
        """Test that AI analysis hints are generated."""
        scenario = {
            "metadata": {"name": "test"},
            "spec": {
                "infrastructure": {
                    "namespace": "test-ns",
                    "resources": [
                        {
                            "name": "test-deployment",
                            "type": "deployment",
                            "anomaly": {
                                "enabled": True,
                                "type": "missing-readiness-probe",
                            },
                        }
                    ],
                },
                "experiments": [{"name": "test-exp", "type": "pod-delete"}],
                "successCriteria": {},
            },
        }

        results = [
            {
                "name": "test-exp",
                "type": "pod-delete",
                "verdict": "Fail",
                "probeSuccessPercentage": 65.0,
                "chaosResult": {"failStep": "ChaosInject"},
            }
        ]

        generator = OutputGenerator(scenario, results, with_anomaly=True)
        output = generator.generate()

        hints = output["aiAnalysisHints"]
        assert "primaryIssue" in hints
        assert hints["anomalyCorrelation"]["anomalyType"] == "missing-readiness-probe"
        assert hints["anomalyCorrelation"]["likelyContributed"] is True


class TestComparison:
    """Tests for run comparison."""

    def test_compare_runs_improvement(self):
        """Test comparing baseline with improved after-fix run."""
        baseline = {
            "runId": "baseline-123",
            "timestamp": "2025-01-18T10:00:00Z",
            "scenario": {"name": "test"},
            "infrastructure": {
                "anomalyInjected": True,
                "resources": [
                    {
                        "name": "test-deployment",
                        "anomaly": {
                            "type": "missing-readiness-probe",
                        },
                    }
                ],
            },
            "experiments": [
                {
                    "name": "test-exp",
                    "result": {
                        "verdict": "Fail",
                        "probeSuccessPercentage": 65,
                    },
                }
            ],
            "summary": {
                "resilienceScore": 65.0,
                "overallVerdict": "FAIL",
            },
        }

        after_fix = {
            "runId": "afterfix-456",
            "timestamp": "2025-01-18T11:00:00Z",
            "scenario": {"name": "test"},
            "infrastructure": {
                "anomalyInjected": False,
                "resources": [
                    {
                        "name": "test-deployment",
                        "anomaly": None,
                    }
                ],
            },
            "experiments": [
                {
                    "name": "test-exp",
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

        assert comparison["comparison"]["resilienceScoreChange"] == 30.0
        assert comparison["comparison"]["verdictChanged"] is True
        assert comparison["conclusion"]["fixEffective"] is True
        assert comparison["conclusion"]["confidence"] > 0.7

    def test_compare_runs_no_improvement(self):
        """Test comparing runs with no improvement."""
        baseline = {
            "runId": "baseline-123",
            "timestamp": "2025-01-18T10:00:00Z",
            "scenario": {"name": "test"},
            "infrastructure": {
                "anomalyInjected": True,
                "resources": [],
            },
            "experiments": [
                {
                    "name": "test-exp",
                    "result": {
                        "verdict": "Fail",
                        "probeSuccessPercentage": 65,
                    },
                }
            ],
            "summary": {
                "resilienceScore": 65.0,
                "overallVerdict": "FAIL",
            },
        }

        after_fix = {
            "runId": "afterfix-456",
            "timestamp": "2025-01-18T11:00:00Z",
            "scenario": {"name": "test"},
            "infrastructure": {
                "anomalyInjected": False,
                "resources": [],
            },
            "experiments": [
                {
                    "name": "test-exp",
                    "result": {
                        "verdict": "Fail",
                        "probeSuccessPercentage": 68,
                    },
                }
            ],
            "summary": {
                "resilienceScore": 68.0,
                "overallVerdict": "FAIL",
            },
        }

        comparison = compare_runs(baseline, after_fix)

        assert comparison["comparison"]["resilienceScoreChange"] == 3.0
        assert comparison["comparison"]["verdictChanged"] is False
        assert comparison["conclusion"]["fixEffective"] is False
