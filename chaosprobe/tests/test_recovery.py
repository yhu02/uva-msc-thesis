"""Tests for the recovery watcher module."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from chaosprobe.metrics.recovery import RecoveryWatcher


def _make_watcher():
    """Create a RecoveryWatcher with mocked Kubernetes client."""
    with patch("chaosprobe.metrics.recovery.ensure_k8s_config"):
        with patch("chaosprobe.metrics.recovery.client") as mock_client:
            mock_client.CoreV1Api.return_value = MagicMock()
            watcher = RecoveryWatcher("default", "nginx")
    return watcher


class TestFinalizeCycle:
    def test_complete_cycle(self):
        deletion = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        scheduled = datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc)
        ready = datetime(2024, 1, 1, 12, 0, 3, tzinfo=timezone.utc)

        result = RecoveryWatcher._finalize_cycle({
            "deletionTime": deletion,
            "scheduledTime": scheduled,
            "readyTime": ready,
        })

        assert result["deletionToScheduled_ms"] == 1000
        assert result["scheduledToReady_ms"] == 2000
        assert result["totalRecovery_ms"] == 3000
        assert result["deletionTime"] == deletion.isoformat()

    def test_incomplete_cycle_no_ready(self):
        deletion = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = RecoveryWatcher._finalize_cycle({
            "deletionTime": deletion,
            "scheduledTime": None,
            "readyTime": None,
        })
        assert result["totalRecovery_ms"] is None
        assert result["deletionToScheduled_ms"] is None
        assert result["scheduledToReady_ms"] is None


class TestComputeSummary:
    def test_no_cycles(self):
        summary = RecoveryWatcher._compute_summary([])
        assert summary["count"] == 0
        assert summary["completedCycles"] == 0
        assert summary["meanRecovery_ms"] is None

    def test_all_incomplete(self):
        cycles = [{"totalRecovery_ms": None}]
        summary = RecoveryWatcher._compute_summary(cycles)
        assert summary["count"] == 1
        assert summary["completedCycles"] == 0
        assert summary["incompleteCycles"] == 1

    def test_multiple_cycles(self):
        cycles = [
            {"totalRecovery_ms": 1000},
            {"totalRecovery_ms": 2000},
            {"totalRecovery_ms": 3000},
        ]
        summary = RecoveryWatcher._compute_summary(cycles)
        assert summary["count"] == 3
        assert summary["completedCycles"] == 3
        assert summary["incompleteCycles"] == 0
        assert summary["meanRecovery_ms"] == 2000.0
        assert summary["minRecovery_ms"] == 1000
        assert summary["maxRecovery_ms"] == 3000

    def test_mixed_complete_incomplete(self):
        cycles = [
            {"totalRecovery_ms": 1500},
            {"totalRecovery_ms": None},
        ]
        summary = RecoveryWatcher._compute_summary(cycles)
        assert summary["completedCycles"] == 1
        assert summary["incompleteCycles"] == 1
        assert summary["meanRecovery_ms"] == 1500.0


class TestIsPodReady:
    def test_ready_pod(self):
        cond = SimpleNamespace(type="Ready", status="True")
        pod = SimpleNamespace(status=SimpleNamespace(conditions=[cond]))
        assert RecoveryWatcher._is_pod_ready(pod) is True

    def test_not_ready_pod(self):
        cond = SimpleNamespace(type="Ready", status="False")
        pod = SimpleNamespace(status=SimpleNamespace(conditions=[cond]))
        assert RecoveryWatcher._is_pod_ready(pod) is False

    def test_no_conditions(self):
        pod = SimpleNamespace(status=SimpleNamespace(conditions=None))
        assert RecoveryWatcher._is_pod_ready(pod) is False


class TestGetScheduledTime:
    def test_with_scheduled_condition(self):
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        cond = SimpleNamespace(type="PodScheduled", last_transition_time=ts)
        pod = SimpleNamespace(status=SimpleNamespace(conditions=[cond]))
        assert RecoveryWatcher._get_scheduled_time(pod) == ts

    def test_naive_timestamp_gets_utc(self):
        ts = datetime(2024, 1, 1, 12, 0, 0)  # naive
        cond = SimpleNamespace(type="PodScheduled", last_transition_time=ts)
        pod = SimpleNamespace(status=SimpleNamespace(conditions=[cond]))
        result = RecoveryWatcher._get_scheduled_time(pod)
        assert result.tzinfo == timezone.utc

    def test_no_conditions(self):
        pod = SimpleNamespace(status=SimpleNamespace(conditions=None))
        assert RecoveryWatcher._get_scheduled_time(pod) is None

    def test_no_scheduled_condition(self):
        cond = SimpleNamespace(type="Ready", status="True", last_transition_time=None)
        pod = SimpleNamespace(status=SimpleNamespace(conditions=[cond]))
        assert RecoveryWatcher._get_scheduled_time(pod) is None


class TestFailureReason:
    def test_incomplete_cycle_has_failure_reason(self):
        deletion = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = RecoveryWatcher._finalize_cycle({
            "deletionTime": deletion,
            "scheduledTime": None,
            "readyTime": None,
            "failure_reason": "experiment_ended_before_recovery",
        })
        assert result["failure_reason"] == "experiment_ended_before_recovery"

    def test_complete_cycle_no_failure_reason(self):
        deletion = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        scheduled = datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc)
        ready = datetime(2024, 1, 1, 12, 0, 3, tzinfo=timezone.utc)
        result = RecoveryWatcher._finalize_cycle({
            "deletionTime": deletion,
            "scheduledTime": scheduled,
            "readyTime": ready,
        })
        assert "failure_reason" not in result


class TestResult:
    def test_empty_result(self):
        watcher = _make_watcher()
        result = watcher.result()
        assert result["deploymentName"] == "nginx"
        assert result["recoveryEvents"] == []
        assert result["rawEvents"] == []
        assert "watchErrors" not in result

    def test_result_with_errors(self):
        watcher = _make_watcher()
        watcher._watch_errors.append("attempt 1: connection reset")
        result = watcher.result()
        assert result["watchErrors"] == ["attempt 1: connection reset"]
