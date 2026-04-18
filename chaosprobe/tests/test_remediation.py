"""Tests for remediation action log generation."""

from chaosprobe.metrics.remediation import generate_remediation_log


def _make_summary(strategies):
    return {"strategies": strategies}


def _make_strategy(verdict="PASS", score=100.0, mean_recovery=1200,
                   status="completed", placement=None):
    s = {
        "status": status,
        "experiment": {
            "overallVerdict": verdict,
            "resilienceScore": score,
        },
        "metrics": {
            "recovery": {
                "summary": {
                    "meanRecovery_ms": mean_recovery,
                    "p95Recovery_ms": mean_recovery * 1.2 if mean_recovery else None,
                    "maxRecovery_ms": mean_recovery * 1.5 if mean_recovery else None,
                }
            }
        } if mean_recovery is not None else {},
        "placement": placement or {},
    }
    return s


class TestGenerateRemediationLog:
    def test_empty_strategies(self):
        assert generate_remediation_log({"strategies": {}}) == []

    def test_no_default(self):
        summary = _make_summary({
            "colocate": _make_strategy(score=50),
        })
        assert generate_remediation_log(summary) == []

    def test_default_only(self):
        summary = _make_summary({
            "default": _make_strategy(score=100),
        })
        assert generate_remediation_log(summary) == []

    def test_single_strategy_vs_baseline(self):
        summary = _make_summary({
            "default": _make_strategy(score=100, mean_recovery=1200),
            "colocate": _make_strategy(score=50, mean_recovery=2000),
        })
        log = generate_remediation_log(summary)

        assert len(log) == 1
        entry = log[0]
        assert entry["baselineState"]["resilienceScore"] == 100
        assert entry["actionTaken"]["strategy"] == "colocate"
        assert entry["resultState"]["resilienceScore"] == 50
        assert entry["outcome"]["classification"] == "degraded"
        assert entry["outcome"]["resilienceImproved"] is False
        assert entry["outcome"]["recoveryImproved"] is False

    def test_improved_strategy(self):
        summary = _make_summary({
            "default": _make_strategy(score=50, mean_recovery=2000),
            "spread": _make_strategy(score=83, mean_recovery=1000),
        })
        log = generate_remediation_log(summary)

        assert len(log) == 1
        entry = log[0]
        assert entry["outcome"]["classification"] == "improved"
        assert entry["outcome"]["resilienceScoreDelta"] == 33.0
        assert entry["outcome"]["recoveryTimeDelta_ms"] == -1000.0
        assert entry["outcome"]["recoveryImproved"] is True

    def test_neutral_strategy(self):
        summary = _make_summary({
            "default": _make_strategy(score=83, mean_recovery=1200),
            "random": _make_strategy(score=83, mean_recovery=1300),
        })
        log = generate_remediation_log(summary)
        assert log[0]["outcome"]["classification"] == "neutral"

    def test_multiple_strategies(self):
        summary = _make_summary({
            "default": _make_strategy(score=100, mean_recovery=1200),
            "colocate": _make_strategy(score=50, mean_recovery=2000),
            "spread": _make_strategy(score=100, mean_recovery=900),
            "adversarial": _make_strategy(score=33, mean_recovery=3000),
        })
        log = generate_remediation_log(summary)
        assert len(log) == 3
        strategies = {e["actionTaken"]["strategy"] for e in log}
        assert strategies == {"colocate", "spread", "adversarial"}

    def test_skips_errored_strategy(self):
        summary = _make_summary({
            "default": _make_strategy(score=100),
            "colocate": _make_strategy(score=50, status="error"),
        })
        log = generate_remediation_log(summary)
        assert len(log) == 0

    def test_placement_included(self):
        placement = {
            "strategy": "colocate",
            "assignments": {"productcatalogservice": "worker1"},
        }
        summary = _make_summary({
            "default": _make_strategy(score=100),
            "colocate": _make_strategy(score=50, placement=placement),
        })
        log = generate_remediation_log(summary)
        assert log[0]["actionTaken"]["placement"] == placement
