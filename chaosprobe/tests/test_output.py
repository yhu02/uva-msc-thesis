"""Tests for output generation and comparison."""

from chaosprobe.output.comparison import compare_runs
from chaosprobe.output.generator import OutputGenerator, build_route_view


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
            "runId": "b",
            "timestamp": "T",
            "scenario": {},
            "experiments": [],
            "summary": {"resilienceScore": 50, "overallVerdict": "FAIL"},
            "metrics": {
                "recovery": {
                    "summary": {
                        "meanRecovery_ms": 3000.0,
                        "p95Recovery_ms": 4000.0,
                    }
                },
            },
        }
        after_fix = {
            "runId": "a",
            "timestamp": "T",
            "scenario": {},
            "experiments": [],
            "summary": {"resilienceScore": 90, "overallVerdict": "PASS"},
            "metrics": {
                "recovery": {
                    "summary": {
                        "meanRecovery_ms": 1500.0,
                        "p95Recovery_ms": 2000.0,
                    }
                },
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
            "runId": "b",
            "timestamp": "T",
            "scenario": {},
            "experiments": [],
            "summary": {"resilienceScore": 50, "overallVerdict": "FAIL"},
            "metrics": {
                "latency": {
                    "phases": {
                        "during-chaos": {
                            "routes": {
                                "frontend→cart": {"mean_ms": 50.0},
                            }
                        }
                    }
                },
            },
        }
        after_fix = {
            "runId": "a",
            "timestamp": "T",
            "scenario": {},
            "experiments": [],
            "summary": {"resilienceScore": 90, "overallVerdict": "PASS"},
            "metrics": {
                "latency": {
                    "phases": {
                        "during-chaos": {
                            "routes": {
                                "frontend→cart": {"mean_ms": 30.0},
                            }
                        }
                    }
                },
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
                "runId": "r",
                "timestamp": "T",
                "scenario": {},
                "experiments": [],
                "summary": {"resilienceScore": score, "overallVerdict": verdict},
                "metrics": {
                    "resources": {
                        "available": True,
                        "phases": {
                            "during-chaos": {
                                "node": {
                                    "meanCpu_percent": cpu,
                                    "meanMemory_percent": mem,
                                }
                            }
                        },
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
            "runId": "b",
            "timestamp": "T",
            "scenario": {},
            "experiments": [],
            "summary": {"resilienceScore": 50, "overallVerdict": "FAIL"},
        }
        after_fix = {
            "runId": "a",
            "timestamp": "T",
            "scenario": {},
            "experiments": [],
            "summary": {"resilienceScore": 90, "overallVerdict": "PASS"},
        }
        comparison = compare_runs(baseline, after_fix)
        assert comparison["comparison"]["metrics"] == {}


# ── build_route_view (Locust ↔ LatencyProber join) ─────────────


def _make_locust_stats(endpoints):
    return {
        "endpoints": endpoints,
        "totalRequests": sum(e.get("requests", 0) for e in endpoints),
    }


def _make_latency_phases(routes_by_phase):
    return {
        phase: {
            "sampleCount": sum(len(v) for v in routes.values()),
            "routes": routes,
        }
        for phase, routes in routes_by_phase.items()
    }


class TestBuildRouteView:
    """`build_route_view` joins outside-cluster (Locust) and in-pod
    (LatencyProber) per-route stats so the two perspectives can be
    cross-validated.  Disagreement is itself a thesis-grade finding
    (the in-pod kubectl-exec measurement has a measurable bias)."""

    def test_empty_when_both_inputs_missing(self):
        assert build_route_view(None, None) == []
        assert build_route_view({}, {}) == []

    def test_locust_only(self):
        locust = _make_locust_stats(
            [
                {
                    "name": "/",
                    "requests": 100,
                    "failures": 1,
                    "avgResponseTime_ms": 50.0,
                    "p95ResponseTime_ms": 120.0,
                }
            ]
        )
        view = build_route_view(locust, None)
        assert len(view) == 1
        entry = view[0]
        assert entry["route"] == "/"
        assert entry["latencyProber"] is None
        assert entry["locust"]["requests"] == 100
        assert entry["locust"]["p95ResponseTime_ms"] == 120.0

    def test_latency_only(self):
        latency = _make_latency_phases(
            {
                "pre-chaos": {"/": {"mean_ms": 50, "p95_ms": 100}},
                "during-chaos": {"/": {"mean_ms": 120, "p95_ms": 250}},
                "post-chaos": {"/": {"mean_ms": 70, "p95_ms": 130}},
            }
        )
        view = build_route_view(None, latency)
        assert len(view) == 1
        entry = view[0]
        assert entry["route"] == "/"
        assert entry["locust"] is None
        assert entry["latencyProber"]["during-chaos"]["p95_ms"] == 250

    def test_both_present_join_by_path(self):
        locust = _make_locust_stats(
            [
                {
                    "name": "/",
                    "requests": 100,
                    "failures": 1,
                    "avgResponseTime_ms": 50,
                    "p95ResponseTime_ms": 120,
                },
                {
                    "name": "/cart",
                    "requests": 30,
                    "failures": 0,
                    "avgResponseTime_ms": 35,
                    "p95ResponseTime_ms": 70,
                },
            ]
        )
        latency = _make_latency_phases(
            {
                "pre-chaos": {"/": {"mean_ms": 48}, "/cart": {"mean_ms": 32}},
                "during-chaos": {"/": {"mean_ms": 110}, "/cart": {"mean_ms": 65}},
                "post-chaos": {"/": {"mean_ms": 60}, "/cart": {"mean_ms": 38}},
            }
        )

        view = build_route_view(locust, latency)

        # Both routes present, both sources joined
        assert {e["route"] for e in view} == {"/", "/cart"}
        for entry in view:
            assert entry["locust"] is not None
            assert entry["latencyProber"] is not None
            assert entry["latencyProber"]["during-chaos"]["mean_ms"] in (110, 65)

    def test_route_in_latency_only_still_emitted(self):
        """A route the LatencyProber probes (e.g. east-west `checkout->currency`)
        that Locust doesn't hit must still appear in the view, with `locust=None`."""
        locust = _make_locust_stats([])  # no Locust routes
        latency = _make_latency_phases(
            {
                "pre-chaos": {"checkout->currency": {"mean_ms": 5}},
                "during-chaos": {"checkout->currency": {"mean_ms": 8}},
                "post-chaos": {"checkout->currency": {"mean_ms": 6}},
            }
        )
        view = build_route_view(locust, latency)
        assert len(view) == 1
        assert view[0]["route"] == "checkout->currency"
        assert view[0]["locust"] is None
        assert view[0]["latencyProber"]["pre-chaos"]["mean_ms"] == 5

    def test_route_missing_from_one_phase(self):
        """A route present in pre-chaos but missing during chaos (e.g. probe
        was added mid-experiment) maps to `None` for that phase so the
        consumer can distinguish 'no samples' from 'phase didn't run'."""
        latency = _make_latency_phases(
            {
                "pre-chaos": {"/": {"mean_ms": 50}},
                "during-chaos": {},  # / missing here
                "post-chaos": {"/": {"mean_ms": 55}},
            }
        )
        view = build_route_view(None, latency)
        entry = view[0]
        assert entry["latencyProber"]["pre-chaos"] is not None
        assert entry["latencyProber"]["during-chaos"] is None
        assert entry["latencyProber"]["post-chaos"] is not None

    def test_locust_endpoint_without_name_is_skipped(self):
        """A Locust endpoint missing the `name` field (defensive against
        malformed CSV) is silently skipped without raising."""
        locust = _make_locust_stats(
            [
                {
                    "name": "",
                    "requests": 5,
                    "failures": 0,
                    "avgResponseTime_ms": 0,
                    "p95ResponseTime_ms": 0,
                },
                {
                    "name": "/",
                    "requests": 100,
                    "failures": 0,
                    "avgResponseTime_ms": 50,
                    "p95ResponseTime_ms": 120,
                },
            ]
        )
        view = build_route_view(locust, None)
        assert [e["route"] for e in view] == ["/"]

    def test_locust_order_preserved_then_latency_sorted(self):
        """Stable ordering: Locust routes first in the order Locust reports
        them; LatencyProber-only routes sorted alphabetically after."""
        locust = _make_locust_stats(
            [
                {
                    "name": "/cart",
                    "requests": 1,
                    "failures": 0,
                    "avgResponseTime_ms": 1,
                    "p95ResponseTime_ms": 1,
                },
                {
                    "name": "/",
                    "requests": 1,
                    "failures": 0,
                    "avgResponseTime_ms": 1,
                    "p95ResponseTime_ms": 1,
                },
            ]
        )
        latency = _make_latency_phases(
            {
                "pre-chaos": {
                    "/": {"mean_ms": 1},
                    "/cart": {"mean_ms": 1},
                    "/payment": {"mean_ms": 1},
                    "/healthz": {"mean_ms": 1},
                },
                "during-chaos": {},
                "post-chaos": {},
            }
        )
        view = build_route_view(locust, latency)
        routes = [e["route"] for e in view]
        # Locust's order first (/cart, /), then alphabetical for the rest
        assert routes == ["/cart", "/", "/healthz", "/payment"]
