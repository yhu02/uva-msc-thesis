"""Tests for the protocol-labeled conntrack prober (v2 M1b collector).

Everything runs against a ``MagicMock`` CoreV1Api and an injected exec
callable (the prober's ``exec_fn`` seam wraps ``kubernetes.stream.stream``
in production), so no cluster is needed: canned ``conntrack`` outputs are
fed straight into the parsing/sampling paths.
"""

import json
import logging
import time
from unittest.mock import MagicMock, patch

from kubernetes.client.rest import ApiException

from chaosprobe.metrics import base as _base
from chaosprobe.metrics.collector import MetricsCollector
from chaosprobe.metrics.conntrack import (
    CONNTRACK_PACKAGE_PIN,
    MANAGED_LABEL_SELECTOR,
    MANAGED_LABELS,
    NODE_LABEL_KEY,
    SAMPLE_COMMAND,
    SAMPLER_IMAGE,
    SAMPLER_NAMESPACE,
    VERSION_COMMAND,
    ConntrackProtocolProber,
    build_sampler_pod_manifest,
    cleanup_sampler_pods,
    parse_conntrack_protocol_counts,
    parse_conntrack_version,
    sampler_pod_name,
)

_VERSION_BANNER = "conntrack v1.4.8 (conntrack-tools)"
_SAMPLE_OUTPUT = "   4197 tcp\n    910 udp\n      3 icmp\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_prober(exec_fn=None, **kwargs):
    """Create a prober without touching a real kubeconfig / API server."""
    with (
        patch("chaosprobe.metrics.conntrack.ensure_k8s_config"),
        patch("chaosprobe.metrics.conntrack.client") as mock_client,
    ):
        mock_core = MagicMock()
        mock_client.CoreV1Api.return_value = mock_core
        prober = ConntrackProtocolProber("test-ns", exec_fn=exec_fn, **kwargs)
    return prober, mock_core


def _make_node(name, labels=None):
    node = MagicMock()
    node.metadata = MagicMock()
    node.metadata.name = name
    node.metadata.labels = labels if labels is not None else {}
    return node


def _make_pod(name, node_label=None, phase="Running"):
    pod = MagicMock()
    pod.metadata = MagicMock()
    pod.metadata.name = name
    pod.metadata.labels = dict(MANAGED_LABELS)
    if node_label:
        pod.metadata.labels[NODE_LABEL_KEY] = node_label
    pod.status = MagicMock()
    pod.status.phase = phase
    return pod


def _pod_list(*pods):
    result = MagicMock()
    result.items = list(pods)
    return result


def _canned_exec(version=_VERSION_BANNER, sample=_SAMPLE_OUTPUT):
    """Exec callable answering the version and sample commands."""

    def exec_fn(core_api, namespace, pod, command):
        if command == VERSION_COMMAND:
            return version
        assert command == SAMPLE_COMMAND
        return sample

    return exec_fn


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class TestParseProtocolCounts:
    def test_parses_uniq_c_output(self):
        rows = parse_conntrack_protocol_counts(_SAMPLE_OUTPUT)
        assert rows == [
            {"proto": "tcp", "count": 4197},
            {"proto": "udp", "count": 910},
            {"proto": "icmp", "count": 3},
        ]

    def test_empty_table_yields_no_rows(self):
        assert parse_conntrack_protocol_counts("") == []

    def test_none_like_output_yields_no_rows(self):
        assert parse_conntrack_protocol_counts(None) == []

    def test_garbage_lines_are_skipped(self):
        out = (
            "conntrack v1.4.8 (conntrack-tools): 42 flow entries have been shown.\n"
            "   910 udp\n"
            "not a count line\n"
            "  abc tcp\n"
            "   12 tcp extra-field\n"
        )
        assert parse_conntrack_protocol_counts(out) == [{"proto": "udp", "count": 910}]

    def test_protocol_names_are_lowercased(self):
        assert parse_conntrack_protocol_counts("  5 TCP\n") == [{"proto": "tcp", "count": 5}]


class TestParseVersion:
    def test_extracts_version_from_banner(self):
        assert parse_conntrack_version(_VERSION_BANNER) == "1.4.8"

    def test_exec_error_returns_none(self):
        assert parse_conntrack_version("ERROR:handshake failed") is None

    def test_empty_output_returns_none(self):
        assert parse_conntrack_version("") is None

    def test_non_version_output_returns_none(self):
        # apk still installing — the binary doesn't exist yet.
        assert parse_conntrack_version("sh: conntrack: not found") is None


# ---------------------------------------------------------------------------
# Sampler pod spec
# ---------------------------------------------------------------------------


class TestSamplerPodManifest:
    def test_pod_name_is_deterministic_per_node(self):
        assert sampler_pod_name("worker1") == "chaosprobe-conntrack-sampler-worker1"

    def test_manifest_shape(self):
        m = build_sampler_pod_manifest("worker2")
        assert m["metadata"]["name"] == sampler_pod_name("worker2")
        assert m["metadata"]["namespace"] == SAMPLER_NAMESPACE
        # Managed labels (cleanup selector) + node binding label.
        for key, value in MANAGED_LABELS.items():
            assert m["metadata"]["labels"][key] == value
        assert m["metadata"]["labels"][NODE_LABEL_KEY] == "worker2"

        spec = m["spec"]
        assert spec["nodeName"] == "worker2"
        assert spec["hostNetwork"] is True
        assert spec["restartPolicy"] == "Never"
        # Tolerate every taint so cordoned/tainted workers keep reporting.
        assert spec["tolerations"] == [{"operator": "Exists"}]

        (container,) = spec["containers"]
        assert container["image"] == SAMPLER_IMAGE
        assert container["securityContext"] == {"privileged": True}
        # M1a I2: the conntrack-tools install must be version-pinned.
        assert CONNTRACK_PACKAGE_PIN in container["command"][2]
        assert "apk add --no-cache" in container["command"][2]

    def test_managed_selector_matches_manifest_labels(self):
        labels = build_sampler_pod_manifest("w")["metadata"]["labels"]
        for clause in MANAGED_LABEL_SELECTOR.split(","):
            key, value = clause.split("=")
            assert labels[key] == value


# ---------------------------------------------------------------------------
# Worker-node discovery
# ---------------------------------------------------------------------------


class TestListWorkerNodes:
    def test_excludes_control_plane_and_master_nodes(self):
        prober, core = _make_prober()
        core.list_node.return_value = _pod_list(
            _make_node("worker2"),
            _make_node("worker1"),
            _make_node("cp1", {"node-role.kubernetes.io/control-plane": ""}),
            _make_node("cp2", {"node-role.kubernetes.io/master": ""}),
        )
        assert prober._list_worker_nodes() == ["worker1", "worker2"]

    def test_node_without_metadata_is_skipped(self):
        prober, core = _make_prober()
        nameless = MagicMock()
        nameless.metadata = None
        core.list_node.return_value = _pod_list(nameless, _make_node("worker1"))
        assert prober._list_worker_nodes() == ["worker1"]

    def test_node_without_name_is_skipped(self):
        prober, core = _make_prober()
        core.list_node.return_value = _pod_list(_make_node(""), _make_node("worker1"))
        assert prober._list_worker_nodes() == ["worker1"]

    def test_api_failure_returns_empty(self, caplog):
        prober, core = _make_prober()
        core.list_node.side_effect = RuntimeError("api down")
        with caplog.at_level(logging.WARNING):
            assert prober._list_worker_nodes() == []
        assert "could not list nodes" in caplog.text


# ---------------------------------------------------------------------------
# ensure_samplers
# ---------------------------------------------------------------------------


class TestEnsureSamplers:
    def test_creates_pod_per_node_and_records_versions(self):
        prober, core = _make_prober(exec_fn=_canned_exec())
        core.list_namespaced_pod.return_value = _pod_list()

        samplers = prober.ensure_samplers(core, ["worker1", "worker2"])

        assert samplers == {
            "worker1": sampler_pod_name("worker1"),
            "worker2": sampler_pod_name("worker2"),
        }
        assert prober._tool_versions == {"worker1": "1.4.8", "worker2": "1.4.8"}
        assert core.create_namespaced_pod.call_count == 2
        ns_arg, manifest = core.create_namespaced_pod.call_args_list[0].args
        assert ns_arg == SAMPLER_NAMESPACE
        assert manifest["spec"]["nodeName"] == "worker1"

    def test_namespace_created_and_conflict_ignored(self):
        prober, core = _make_prober(exec_fn=_canned_exec())
        core.list_namespaced_pod.return_value = _pod_list()
        core.create_namespace.side_effect = ApiException(status=409, reason="Conflict")

        prober.ensure_samplers(core, ["worker1"])

        core.create_namespace.assert_called_once_with({"metadata": {"name": SAMPLER_NAMESPACE}})
        assert "worker1" in prober._samplers

    def test_namespace_creation_failure_warns_but_continues(self, caplog):
        prober, core = _make_prober(exec_fn=_canned_exec())
        core.list_namespaced_pod.return_value = _pod_list()
        core.create_namespace.side_effect = ApiException(status=403, reason="Forbidden")

        with caplog.at_level(logging.WARNING):
            prober.ensure_samplers(core, ["worker1"])
        assert "could not create namespace" in caplog.text

    def test_existing_sampler_pod_is_adopted_not_recreated(self):
        prober, core = _make_prober(exec_fn=_canned_exec())
        core.list_namespaced_pod.return_value = _pod_list(
            _make_pod("existing-sampler", node_label="worker1")
        )

        samplers = prober.ensure_samplers(core, ["worker1"])

        assert samplers == {"worker1": "existing-sampler"}
        core.create_namespaced_pod.assert_not_called()

    def test_dead_sampler_pod_is_deleted_and_recreated(self):
        prober, core = _make_prober(exec_fn=_canned_exec())
        core.list_namespaced_pod.return_value = _pod_list(
            _make_pod("dead-sampler", node_label="worker1", phase="Failed")
        )

        samplers = prober.ensure_samplers(core, ["worker1"])

        core.delete_namespaced_pod.assert_called_once_with("dead-sampler", SAMPLER_NAMESPACE)
        assert samplers == {"worker1": sampler_pod_name("worker1")}

    def test_dead_sampler_delete_failure_warns(self, caplog):
        prober, core = _make_prober(exec_fn=_canned_exec())
        core.list_namespaced_pod.return_value = _pod_list(
            _make_pod("dead-sampler", node_label="worker1", phase="Succeeded")
        )
        core.delete_namespaced_pod.side_effect = RuntimeError("nope")

        with caplog.at_level(logging.WARNING):
            prober.ensure_samplers(core, ["worker1"])
        assert "could not delete dead sampler" in caplog.text

    def test_existing_pod_without_node_label_is_ignored(self):
        prober, core = _make_prober(exec_fn=_canned_exec())
        stray = _make_pod("stray")
        stray.metadata.labels = dict(MANAGED_LABELS)  # no node label
        core.list_namespaced_pod.return_value = _pod_list(stray)

        samplers = prober.ensure_samplers(core, ["worker1"])
        assert samplers == {"worker1": sampler_pod_name("worker1")}

    def test_existing_pod_without_status_or_labels(self):
        prober, core = _make_prober(exec_fn=_canned_exec())
        bare = MagicMock()
        bare.metadata = MagicMock()
        bare.metadata.name = "bare"
        bare.metadata.labels = None
        bare.status = None
        core.list_namespaced_pod.return_value = _pod_list(bare)

        samplers = prober.ensure_samplers(core, ["worker1"])
        assert samplers == {"worker1": sampler_pod_name("worker1")}

    def test_existing_listing_failure_treated_as_no_existing(self, caplog):
        prober, core = _make_prober(exec_fn=_canned_exec())
        core.list_namespaced_pod.side_effect = RuntimeError("rbac")

        with caplog.at_level(logging.WARNING):
            samplers = prober.ensure_samplers(core, ["worker1"])
        assert "could not list existing samplers" in caplog.text
        assert samplers == {"worker1": sampler_pod_name("worker1")}

    def test_create_conflict_adopts_pod_by_name(self):
        prober, core = _make_prober(exec_fn=_canned_exec())
        core.list_namespaced_pod.return_value = _pod_list()
        core.create_namespaced_pod.side_effect = ApiException(status=409, reason="Conflict")

        samplers = prober.ensure_samplers(core, ["worker1"])
        assert samplers == {"worker1": sampler_pod_name("worker1")}

    def test_create_failure_skips_node_with_warning(self, caplog):
        prober, core = _make_prober(exec_fn=_canned_exec())
        core.list_namespaced_pod.return_value = _pod_list()
        core.create_namespaced_pod.side_effect = ApiException(status=403, reason="Forbidden")

        with caplog.at_level(logging.WARNING):
            samplers = prober.ensure_samplers(core, ["worker1"])
        assert samplers == {}
        assert "could not create sampler pod" in caplog.text

    def test_node_whose_sampler_never_readies_is_dropped(self, caplog):
        def exec_fn(core_api, namespace, pod, command):
            if pod == sampler_pod_name("worker2"):
                return "ERROR:container not running"
            return _canned_exec()(core_api, namespace, pod, command)

        prober, core = _make_prober(exec_fn=exec_fn, ready_timeout=0.0)
        core.list_namespaced_pod.return_value = _pod_list()

        with caplog.at_level(logging.WARNING):
            samplers = prober.ensure_samplers(core, ["worker1", "worker2"])

        assert samplers == {"worker1": sampler_pod_name("worker1")}
        assert "not ready within" in caplog.text

    def test_readiness_probe_exception_drops_node(self, caplog):
        def exec_fn(core_api, namespace, pod, command):
            raise ValueError("websocket handshake failed")

        prober, core = _make_prober(exec_fn=exec_fn, ready_timeout=0.0)
        core.list_namespaced_pod.return_value = _pod_list()

        with caplog.at_level(logging.WARNING):
            samplers = prober.ensure_samplers(core, ["worker1"])

        assert samplers == {}
        assert "readiness probe" in caplog.text
        assert "not ready within" in caplog.text


class TestWaitForSamplerReady:
    def test_retries_until_version_appears(self):
        outputs = iter(["ERROR:starting", "sh: conntrack: not found", _VERSION_BANNER])

        def exec_fn(core_api, namespace, pod, command):
            return next(outputs)

        prober, core = _make_prober(exec_fn=exec_fn, ready_timeout=30.0, ready_poll_interval=0.0)
        assert prober._wait_for_sampler_ready(core, "pod") == "1.4.8"

    def test_times_out_and_returns_none(self):
        prober, core = _make_prober(exec_fn=lambda *a: "ERROR:never ready", ready_timeout=0.0)
        assert prober._wait_for_sampler_ready(core, "pod") is None


# ---------------------------------------------------------------------------
# start() — graceful degradation
# ---------------------------------------------------------------------------


class TestStart:
    def test_no_worker_nodes_disables_sampling_gracefully(self, caplog):
        prober, core = _make_prober()
        core.list_node.return_value = _pod_list()
        with caplog.at_level(logging.WARNING):
            prober.start()
        prober.stop()
        meta = prober.result()["meta"]
        assert meta["available"] is False
        assert meta["reason"] == "no worker nodes discovered"

    def test_setup_exception_disables_sampling_gracefully(self, caplog):
        prober, core = _make_prober()
        core.list_node.return_value = _pod_list(_make_node("worker1"))
        with patch.object(prober, "ensure_samplers", side_effect=RuntimeError("boom")):
            with caplog.at_level(logging.WARNING):
                prober.start()
        prober.stop()
        meta = prober.result()["meta"]
        assert meta["available"] is False
        assert "sampler setup failed: boom" in meta["reason"]
        assert "sampling disabled" in caplog.text

    def test_no_ready_sampler_records_reason(self):
        prober, core = _make_prober(exec_fn=lambda *a: "ERROR:nope", ready_timeout=0.0)
        core.list_node.return_value = _pod_list(_make_node("worker1"))
        core.list_namespaced_pod.return_value = _pod_list()
        prober.start()
        prober.stop()
        meta = prober.result()["meta"]
        assert meta["available"] is False
        assert "no sampler pod became ready" in meta["reason"]


# ---------------------------------------------------------------------------
# Sampling loop
# ---------------------------------------------------------------------------


class TestSampling:
    def test_sample_once_records_per_node_protocol_rows(self):
        prober, _core = _make_prober(exec_fn=_canned_exec())
        prober._samplers = {"worker1": "pod-1", "worker2": "pod-2"}

        prober._sample_once()

        samples = prober.result()["samples"]
        assert len(samples) == 6  # 3 protocols x 2 nodes
        assert {s["node"] for s in samples} == {"worker1", "worker2"}
        first = samples[0]
        assert set(first) == {"ts", "node", "proto", "count", "phase"}
        assert first["phase"] == "pre-chaos"
        assert isinstance(first["count"], int)
        # Timestamps are ISO-8601 UTC so they align with the recorded
        # chaos windows (anomalyLabels) downstream.
        assert first["ts"].endswith("+00:00")

    def test_phase_tracks_chaos_markers(self):
        prober, _core = _make_prober(exec_fn=_canned_exec())
        prober._samplers = {"worker1": "pod-1"}
        prober._sample_once()
        prober.mark_chaos_start()
        prober._sample_once()
        prober.mark_chaos_end()
        prober._sample_once()
        phases = [s["phase"] for s in prober.result()["samples"]]
        assert phases[:3] == ["pre-chaos"] * 3
        assert phases[3:6] == ["during-chaos"] * 3
        assert phases[6:] == ["post-chaos"] * 3

    def test_exec_error_counts_probe_error_and_records_nothing(self, caplog):
        prober, _core = _make_prober(exec_fn=lambda *a: "ERROR:gone")
        prober._samplers = {"worker1": "pod-1"}
        with caplog.at_level(logging.WARNING):
            prober._sample_once()
        data = prober.result()
        assert data["samples"] == []
        assert data["meta"]["probeErrors"] == 1
        assert "exec failed" in caplog.text

    def test_empty_conntrack_table_records_nothing_without_error(self):
        prober, _core = _make_prober(exec_fn=_canned_exec(sample=""))
        prober._samplers = {"worker1": "pod-1"}
        prober._sample_once()
        data = prober.result()
        assert data["samples"] == []
        assert "probeErrors" not in data["meta"]

    def test_probe_loop_survives_sampling_exception(self, caplog):
        prober, _core = _make_prober()

        def boom():
            prober._stop_event.set()  # one tick, then exit the loop
            raise RuntimeError("tick failed")

        with patch.object(prober, "_sample_once", side_effect=boom):
            with caplog.at_level(logging.WARNING):
                prober._probe_loop()
        assert prober.result()["meta"]["probeErrors"] == 1
        assert "tick failed" in caplog.text

    def test_full_thread_lifecycle_collects_samples(self):
        prober, core = _make_prober(exec_fn=_canned_exec(), interval=0.01)
        core.list_node.return_value = _pod_list(
            _make_node("worker1"),
            _make_node("cp", {"node-role.kubernetes.io/control-plane": ""}),
        )
        core.list_namespaced_pod.return_value = _pod_list()

        prober.start()
        try:
            deadline = time.time() + 5.0
            while time.time() < deadline and not prober.result()["samples"]:
                time.sleep(0.01)
        finally:
            prober.stop()

        data = prober.result()
        assert data["samples"], "sampling thread produced no samples"
        assert data["meta"]["available"] is True
        assert data["meta"]["toolVersion"] == "1.4.8"
        assert data["meta"]["nodes"] == ["worker1"]


# ---------------------------------------------------------------------------
# result() metadata
# ---------------------------------------------------------------------------


class TestResultMeta:
    def test_meta_records_pin_image_interval_and_versions(self):
        prober, _core = _make_prober(exec_fn=_canned_exec())
        prober._samplers = {"worker1": "pod-1"}
        prober._tool_versions = {"worker1": "1.4.8"}
        meta = prober.result()["meta"]
        assert meta["available"] is True
        assert meta["toolVersion"] == "1.4.8"
        assert meta["toolVersionsByNode"] == {"worker1": "1.4.8"}
        assert meta["intervalSeconds"] == 5.0
        assert meta["samplerImage"] == SAMPLER_IMAGE
        assert meta["packagePin"] == CONNTRACK_PACKAGE_PIN
        assert meta["samplerNamespace"] == SAMPLER_NAMESPACE
        assert meta["nodes"] == ["worker1"]

    def test_tool_version_none_when_nodes_disagree(self):
        prober, _core = _make_prober()
        prober._samplers = {"w1": "p1", "w2": "p2"}
        prober._tool_versions = {"w1": "1.4.8", "w2": "1.4.7"}
        meta = prober.result()["meta"]
        assert meta["toolVersion"] is None
        assert meta["toolVersionsByNode"] == {"w1": "1.4.8", "w2": "1.4.7"}

    def test_tool_version_none_when_nothing_resolved(self):
        prober, _core = _make_prober()
        assert prober.result()["meta"]["toolVersion"] is None

    def test_default_exec_fn_is_the_k8s_stream_wrapper(self):
        prober, _core = _make_prober()
        assert prober._exec is _base.exec_in_pod


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_deletes_all_managed_sampler_pods(self):
        core = MagicMock()
        core.list_namespaced_pod.return_value = _pod_list(
            _make_pod("s1", node_label="w1"), _make_pod("s2", node_label="w2")
        )
        assert cleanup_sampler_pods(core) == 2
        core.list_namespaced_pod.assert_called_once_with(
            SAMPLER_NAMESPACE, label_selector=MANAGED_LABEL_SELECTOR
        )
        deleted = {c.args[0] for c in core.delete_namespaced_pod.call_args_list}
        assert deleted == {"s1", "s2"}

    def test_list_failure_degrades_to_warning(self, caplog):
        core = MagicMock()
        core.list_namespaced_pod.side_effect = RuntimeError("api down")
        with caplog.at_level(logging.WARNING):
            assert cleanup_sampler_pods(core) == 0
        assert "could not list sampler pods" in caplog.text

    def test_delete_failure_warns_and_continues(self, caplog):
        core = MagicMock()
        core.list_namespaced_pod.return_value = _pod_list(
            _make_pod("s1", node_label="w1"), _make_pod("s2", node_label="w2")
        )
        core.delete_namespaced_pod.side_effect = [ApiException(status=404), None]
        with caplog.at_level(logging.WARNING):
            assert cleanup_sampler_pods(core) == 1
        assert "could not delete pod s1" in caplog.text

    def test_prober_cleanup_uses_own_core_api_by_default(self):
        prober, core = _make_prober()
        core.list_namespaced_pod.return_value = _pod_list(_make_pod("s1", node_label="w1"))
        assert prober.cleanup() == 1
        core.delete_namespaced_pod.assert_called_once_with("s1", SAMPLER_NAMESPACE)

    def test_prober_cleanup_accepts_explicit_core_api(self):
        prober, own_core = _make_prober()
        other = MagicMock()
        other.list_namespaced_pod.return_value = _pod_list()
        assert prober.cleanup(other) == 0
        other.list_namespaced_pod.assert_called_once()
        own_core.list_namespaced_pod.assert_not_called()


# ---------------------------------------------------------------------------
# Collector integration — how samples land in summary.json
# ---------------------------------------------------------------------------


def _make_collector():
    with (
        patch("chaosprobe.metrics.collector.ensure_k8s_config"),
        patch("chaosprobe.metrics.collector.client") as mock_client,
    ):
        mock_core = MagicMock()
        mock_client.CoreV1Api.return_value = mock_core
        collector = MetricsCollector("test-ns")
    mock_core.list_namespaced_pod.return_value = _pod_list()
    collector.discovery_api = MagicMock()
    collector.discovery_api.list_namespaced_endpoint_slice.return_value = MagicMock(
        data=json.dumps({"items": []})
    )
    return collector


class TestCollectorIntegration:
    def test_conntrack_data_surfaces_as_summary_keys(self):
        collector = _make_collector()
        samples = [{"ts": "t", "node": "w1", "proto": "udp", "count": 910, "phase": "pre-chaos"}]
        meta = {"available": True, "toolVersion": "1.4.8"}

        result = collector.collect(
            deployment_name="frontend",
            since_time=0.0,
            until_time=10.0,
            conntrack_data={"samples": samples, "meta": meta},
        )

        assert result["conntrackProtocolSamples"] == samples
        assert result["conntrackProtocolMeta"] == meta

    def test_missing_pieces_default_to_empty_containers(self):
        collector = _make_collector()
        result = collector.collect(
            deployment_name="frontend",
            since_time=0.0,
            until_time=10.0,
            conntrack_data={},
        )
        assert result["conntrackProtocolSamples"] == []
        assert result["conntrackProtocolMeta"] == {}

    def test_omitted_when_not_collected(self):
        collector = _make_collector()
        result = collector.collect(
            deployment_name="frontend",
            since_time=0.0,
            until_time=10.0,
        )
        assert "conntrackProtocolSamples" not in result
        assert "conntrackProtocolMeta" not in result
