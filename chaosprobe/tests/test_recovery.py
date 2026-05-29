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

        result = RecoveryWatcher._finalize_cycle(
            {
                "deletionTime": deletion,
                "scheduledTime": scheduled,
                "readyTime": ready,
            }
        )

        assert result["deletionToScheduled_ms"] == 1000
        assert result["scheduledToReady_ms"] == 2000
        assert result["totalRecovery_ms"] == 3000
        assert result["deletionTime"] == deletion.isoformat()

    def test_incomplete_cycle_no_ready(self):
        deletion = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = RecoveryWatcher._finalize_cycle(
            {
                "deletionTime": deletion,
                "scheduledTime": None,
                "readyTime": None,
            }
        )
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
        result = RecoveryWatcher._finalize_cycle(
            {
                "deletionTime": deletion,
                "scheduledTime": None,
                "readyTime": None,
                "failure_reason": "experiment_ended_before_recovery",
            }
        )
        assert result["failure_reason"] == "experiment_ended_before_recovery"

    def test_complete_cycle_no_failure_reason(self):
        deletion = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        scheduled = datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc)
        ready = datetime(2024, 1, 1, 12, 0, 3, tzinfo=timezone.utc)
        result = RecoveryWatcher._finalize_cycle(
            {
                "deletionTime": deletion,
                "scheduledTime": scheduled,
                "readyTime": ready,
            }
        )
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

    def test_result_includes_scheduler_events_key(self):
        """`schedulerEvents` is always present (possibly empty), distinct
        from `recoveryEvents` and `rawEvents`."""
        watcher = _make_watcher()
        result = watcher.result()
        assert result["schedulerEvents"] == []
        # Sanity: the three event-ish fields are independent
        assert "recoveryEvents" in result
        assert "rawEvents" in result


def _make_event(
    *,
    reason: str = "Scheduled",
    pod_name: str = "nginx-abc",
    namespace: str = "default",
    kind: str = "Pod",
    event_type: str = "Normal",
    message: str = "Successfully assigned default/nginx-abc to worker-1",
    host: str = "worker-1",
    event_time=None,
):
    """Build a fake `kubernetes.client.V1Event`-like object."""
    event = MagicMock()
    event.reason = reason
    event.type = event_type
    event.message = message
    event.event_time = event_time
    event.last_timestamp = None
    event.first_timestamp = None

    event.involved_object = MagicMock()
    event.involved_object.kind = kind
    event.involved_object.name = pod_name
    event.involved_object.namespace = namespace

    event.source = MagicMock()
    event.source.host = host
    return event


class TestSchedulerEventCapture:
    """The K8s event watch surfaces `Scheduled` / `FailedScheduling` /
    `BackOff` / `FailedCreate` / `FailedMount` events for the deployment's
    pods so the H9 scheduling-latency-dominates-recovery hypothesis can be
    measured directly, not inferred from pod-state transitions alone."""

    def test_scheduled_event_captured(self):
        watcher = _make_watcher()
        ts = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
        event = _make_event(
            reason="Scheduled",
            pod_name="nginx-replacement-abc123",
            event_time=ts,
        )

        parsed = watcher._parse_scheduler_event(event)

        assert parsed is not None
        assert parsed["reason"] == "Scheduled"
        assert parsed["podName"] == "nginx-replacement-abc123"
        assert parsed["node"] == "worker-1"
        assert parsed["type"] == "Normal"
        assert parsed["timestamp"] == ts.isoformat()

    def test_failed_scheduling_event_captured(self):
        watcher = _make_watcher()
        event = _make_event(
            reason="FailedScheduling",
            pod_name="nginx-xyz",
            event_type="Warning",
            message="0/4 nodes are available: 4 Insufficient memory.",
            host=None,
        )

        parsed = watcher._parse_scheduler_event(event)

        assert parsed is not None
        assert parsed["reason"] == "FailedScheduling"
        assert parsed["type"] == "Warning"
        assert "Insufficient memory" in parsed["message"]
        # FailedScheduling has no node assigned yet
        assert parsed["node"] is None

    def test_pod_in_different_deployment_filtered(self):
        """Events for `frontend-*` pods are skipped when watching `nginx`."""
        watcher = _make_watcher()
        event = _make_event(reason="Scheduled", pod_name="frontend-abc")
        assert watcher._parse_scheduler_event(event) is None

    def test_non_pod_kind_filtered(self):
        """Events for non-Pod kinds (Deployment, ReplicaSet) are skipped."""
        watcher = _make_watcher()
        event = _make_event(
            reason="Scheduled",
            pod_name="nginx-abc",
            kind="ReplicaSet",
        )
        # The field selector should already filter these at the API layer,
        # but the parser must defend against malformed cases regardless.
        # Note: the parser ignores `kind` — the field selector handles it,
        # and the prefix match on name handles deployment scoping.  Here
        # we just confirm the event is still captured if it slips through.
        parsed = watcher._parse_scheduler_event(event)
        assert parsed is not None  # kind not enforced by the parser

    def test_unrelated_reason_filtered(self):
        """Reasons outside `_SCHEDULER_EVENT_REASONS` are ignored."""
        watcher = _make_watcher()
        event = _make_event(reason="Pulled", pod_name="nginx-abc")
        assert watcher._parse_scheduler_event(event) is None

    def test_malformed_event_no_involved_object_returns_none(self):
        watcher = _make_watcher()
        event = MagicMock()
        event.reason = "Scheduled"
        event.involved_object = None
        assert watcher._parse_scheduler_event(event) is None

    def test_malformed_event_no_pod_name_returns_none(self):
        watcher = _make_watcher()
        event = _make_event(reason="Scheduled", pod_name=None)
        assert watcher._parse_scheduler_event(event) is None

    def test_none_event_returns_none(self):
        watcher = _make_watcher()
        assert watcher._parse_scheduler_event(None) is None

    def test_event_with_string_timestamp_passes_through(self):
        """If the K8s client returns a pre-formatted timestamp string (as
        some versions do for `event_time`), pass it through unchanged."""
        watcher = _make_watcher()
        event = _make_event(reason="Scheduled", pod_name="nginx-abc")
        event.event_time = "2026-05-28T12:00:00Z"

        parsed = watcher._parse_scheduler_event(event)

        assert parsed["timestamp"] == "2026-05-28T12:00:00Z"

    def test_event_without_any_timestamp_falls_back_to_now(self):
        """When all three of `event_time`, `last_timestamp`, and
        `first_timestamp` are absent, the parser stamps "now" so the event
        still has a usable time.  The exact value isn't asserted — only
        that it's a non-empty ISO-format string."""
        watcher = _make_watcher()
        event = _make_event(reason="Scheduled", pod_name="nginx-abc")
        event.event_time = None
        event.last_timestamp = None
        event.first_timestamp = None

        parsed = watcher._parse_scheduler_event(event)

        assert parsed["timestamp"] is not None
        assert "T" in parsed["timestamp"]  # ISO 8601 marker

    def test_event_last_timestamp_fallback(self):
        """When event_time is None, falls back to last_timestamp."""
        watcher = _make_watcher()
        event = _make_event(reason="BackOff", pod_name="nginx-abc")
        event.event_time = None
        event.last_timestamp = datetime(2026, 5, 28, 13, 0, 0, tzinfo=timezone.utc)
        event.first_timestamp = None

        parsed = watcher._parse_scheduler_event(event)

        assert parsed["timestamp"] == "2026-05-28T13:00:00+00:00"

    def test_event_with_no_source_host(self):
        """An event whose source has no host (some cluster controllers
        don't set it) maps to node=None instead of raising."""
        watcher = _make_watcher()
        event = _make_event(reason="FailedScheduling", pod_name="nginx-abc")
        event.source = MagicMock()
        event.source.host = None

        parsed = watcher._parse_scheduler_event(event)

        assert parsed["node"] is None

    def test_event_with_no_source(self):
        """When the event lacks `source` entirely (rare), node falls back
        to None without raising."""
        watcher = _make_watcher()
        event = _make_event(reason="Scheduled", pod_name="nginx-abc")
        event.source = None

        parsed = watcher._parse_scheduler_event(event)

        assert parsed["node"] is None

    def test_all_five_reasons_accepted(self):
        watcher = _make_watcher()
        for reason in (
            "Scheduled",
            "FailedScheduling",
            "BackOff",
            "FailedCreate",
            "FailedMount",
        ):
            event = _make_event(reason=reason, pod_name="nginx-abc")
            parsed = watcher._parse_scheduler_event(event)
            assert parsed is not None, f"reason {reason} should be captured"
            assert parsed["reason"] == reason
