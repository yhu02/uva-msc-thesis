"""Tests for prepull's fail-fast-on-unpullable-image behavior.

`prepull_probe_images` used to wait out the full timeout on an `Init:ImagePullBackOff`
pod (phase stays `Pending`) and then let the run proceed on missing images. It now
detects `ImagePullBackOff` and aborts with a `click.ClickException`, while still
cleaning up the prepull pods it created.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import click
import pytest
from kubernetes.client.rest import ApiException

from chaosprobe.probes.builder import _imagepull_failure, prepull_probe_images


def _cstatus(image, reason):
    waiting = SimpleNamespace(reason=reason) if reason else None
    return SimpleNamespace(image=image, state=SimpleNamespace(waiting=waiting))


def _pod(phase="Pending", init=None, main=None):
    return SimpleNamespace(
        status=SimpleNamespace(
            phase=phase,
            init_container_statuses=init,
            container_statuses=main,
        )
    )


# ── _imagepull_failure ────────────────────────────────────────────────────


def test_failure_detected_in_init_container():
    pod = _pod(init=[_cstatus("reg/img:tag", "ImagePullBackOff")])
    assert _imagepull_failure(pod) == ("reg/img:tag", "ImagePullBackOff")


def test_failure_detected_in_main_container():
    pod = _pod(main=[_cstatus("reg/img:tag", "ImagePullBackOff")])
    assert _imagepull_failure(pod) == ("reg/img:tag", "ImagePullBackOff")


def test_other_waiting_reason_is_not_a_failure():
    pod = _pod(init=[_cstatus("reg/img:tag", "PodInitializing")])
    assert _imagepull_failure(pod) is None


def test_running_container_is_not_a_failure():
    pod = _pod(init=[_cstatus("reg/img:tag", None)])
    assert _imagepull_failure(pod) is None


def test_no_statuses_is_not_a_failure():
    assert _imagepull_failure(_pod()) is None


# ── prepull_probe_images integration ──────────────────────────────────────


def _run_prepull(read_side_effect, timeout=300, api=None):
    """Drive prepull with a mocked CoreV1Api (time.sleep stubbed); return it."""
    if api is None:
        api = MagicMock()
    api.read_namespaced_pod_status.side_effect = read_side_effect
    with (
        patch("chaosprobe.k8s.ensure_k8s_config", lambda: None),
        patch("kubernetes.client.CoreV1Api", return_value=api),
        patch("time.sleep", lambda *a: None),
    ):
        result = prepull_probe_images("ns", ["reg/img:tag"], ["worker1"], timeout=timeout)
    return api, result


def test_aborts_and_cleans_up_on_imagepullbackoff():
    bad = _pod(init=[_cstatus("reg/img:tag", "ImagePullBackOff")])
    api = MagicMock()
    api.read_namespaced_pod_status.return_value = bad
    with (
        patch("chaosprobe.k8s.ensure_k8s_config", lambda: None),
        patch("kubernetes.client.CoreV1Api", return_value=api),
    ):
        with pytest.raises(click.ClickException) as exc:
            prepull_probe_images("ns", ["reg/img:tag"], ["worker1"])
    assert "cannot be pulled" in str(exc.value)
    # Cleanup must run even though we aborted (finally:).
    api.delete_namespaced_pod.assert_called()


def test_success_path_returns_count_and_cleans_up():
    api, result = _run_prepull(lambda *a, **k: _pod(phase="Succeeded"))
    assert result == 1  # 1 worker x 1 image
    api.delete_namespaced_pod.assert_called()


def test_failed_phase_warns_and_continues():
    api, result = _run_prepull(lambda *a, **k: _pod(phase="Failed"))
    assert result == 0
    api.delete_namespaced_pod.assert_called()


def test_timeout_warns_and_continues():
    # timeout=0 → loop body never runs, pending pods hit the timeout warning.
    api, _ = _run_prepull(lambda *a, **k: _pod(phase="Pending"), timeout=0)
    api.delete_namespaced_pod.assert_called()


def test_retries_after_transient_status_read_error():
    # A transient ApiException on read is skipped (continue), the pod is
    # re-polled on the next loop iteration (covers the sleep-between-polls path).
    api, result = _run_prepull([ApiException(status=500), _pod(phase="Succeeded")])
    assert result == 1


def test_noop_when_no_images():
    api = MagicMock()
    with (
        patch("chaosprobe.k8s.ensure_k8s_config", lambda: None),
        patch("kubernetes.client.CoreV1Api", return_value=api),
    ):
        assert prepull_probe_images("ns", [], ["worker1"]) == 0
    api.create_namespaced_pod.assert_not_called()


def test_pod_create_failure_is_warned_and_skipped():
    api = MagicMock()
    api.create_namespaced_pod.side_effect = ApiException(status=409)
    with (
        patch("chaosprobe.k8s.ensure_k8s_config", lambda: None),
        patch("kubernetes.client.CoreV1Api", return_value=api),
    ):
        # No pod was created, so there is nothing to poll and nothing to pull.
        assert prepull_probe_images("ns", ["reg/img:tag"], ["worker1"]) == 0
