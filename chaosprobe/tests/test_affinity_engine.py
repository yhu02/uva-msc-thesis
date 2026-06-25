"""Tests for chaosprobe.placement.affinity_engine (M1b).

Pure-Python per CONTRIBUTING: all Kubernetes API surfaces are MagicMocks;
no cluster is touched.  Covers patch-shape correctness for every supported
(r, mode) cell, the never-assume live verification (pass + each failure
mode), apply/restore, and the rollout wait.
"""

from unittest.mock import MagicMock

import pytest
from kubernetes.client.rest import ApiException

from chaosprobe.placement import affinity_engine as engine
from chaosprobe.placement.mutator import MANAGED_ANNOTATION, PLACEMENT_LABEL_KEY

WORKERS = ["w1", "w2", "w3", "w4"]


def _api():
    return engine.K8sApi(apps=MagicMock(), core=MagicMock())


def _affinity_pin(node, key=PLACEMENT_LABEL_KEY, operator="In", values=None):
    expr = MagicMock()
    expr.key = key
    expr.operator = operator
    expr.values = [node] if values is None else values
    term = MagicMock()
    term.match_expressions = [expr]
    required = MagicMock()
    required.node_selector_terms = [term]
    node_affinity = MagicMock()
    node_affinity.required_during_scheduling_ignored_during_execution = required
    affinity = MagicMock()
    affinity.node_affinity = node_affinity
    return affinity


def _dep(
    name,
    annotations=None,
    node_selector=None,
    match_labels=None,
    affinity=None,
    replicas=1,
    generation=1,
):
    dep = MagicMock()
    dep.metadata.name = name
    dep.metadata.annotations = annotations
    dep.metadata.generation = generation
    dep.spec.replicas = replicas
    dep.spec.template.spec.node_selector = node_selector
    dep.spec.template.spec.affinity = affinity
    if match_labels is None:
        dep.spec.selector = None
    else:
        dep.spec.selector.match_labels = match_labels
    return dep


def _pod(node="w1", phase="Running", ready=True, conditions="default", has_spec=True):
    pod = MagicMock()
    pod.status.phase = phase
    if conditions == "default":
        cond = MagicMock()
        cond.type = "Ready"
        cond.status = "True" if ready else "False"
        pod.status.conditions = [cond]
    else:
        pod.status.conditions = conditions
    if has_spec:
        pod.spec.node_name = node
    else:
        pod.spec = None
    return pod


def _pod_list(api, pods):
    api.core.list_namespaced_pod.return_value = MagicMock(items=pods)


# ── build_patch: (r, mode) → patch design table ───────────────────────


def _pin_values(patch):
    terms = patch["spec"]["template"]["spec"]["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"]
    return terms[0]["matchExpressions"][0]


@pytest.mark.parametrize("mode", [engine.MODE_PACKED, engine.MODE_ANTI_AFFINE])
def test_build_patch_r1_pins_to_node_either_mode(mode):
    patch = engine.build_patch("frontend", "w2", 1, mode, WORKERS)
    assert patch["spec"]["replicas"] == 1
    expr = _pin_values(patch)
    assert expr == {"key": PLACEMENT_LABEL_KEY, "operator": "In", "values": ["w2"]}
    assert patch["spec"]["template"]["spec"]["affinity"]["podAntiAffinity"] is None
    assert patch["metadata"]["annotations"][MANAGED_ANNOTATION] == f"affinity-r1-{mode}"


def test_build_patch_r3_packed_pins_all_replicas_to_one_node():
    patch = engine.build_patch("cartservice", "w3", 3, engine.MODE_PACKED, WORKERS)
    assert patch["spec"]["replicas"] == 3
    assert _pin_values(patch)["values"] == ["w3"]
    assert patch["metadata"]["annotations"][MANAGED_ANNOTATION] == "affinity-r3-packed"


def test_packed_round_robin_distributes_services_across_workers():
    # sorted service i → worker i mod W: services spread, not stacked.
    assignment = engine.packed_round_robin(["c", "a", "b", "d"], ["w1", "w2"])
    assert assignment == {"a": "w1", "b": "w2", "c": "w1", "d": "w2"}
    # per-node service count is balanced at ⌈S/W⌉ — never all on one node.
    per_node = {}
    for node in assignment.values():
        per_node[node] = per_node.get(node, 0) + 1
    assert max(per_node.values()) == 2


def test_packed_round_robin_rejects_no_workers():
    with pytest.raises(ValueError, match="non-empty list of worker"):
        engine.packed_round_robin(["a"], [])


def test_build_patch_r3_anti_affine_spreads_with_no_pin():
    patch = engine.build_patch("cartservice", None, 3, engine.MODE_ANTI_AFFINE, WORKERS)
    spec = patch["spec"]
    assert spec["replicas"] == 3
    affinity = spec["template"]["spec"]["affinity"]
    assert affinity["nodeAffinity"] is None  # no pin: the scheduler chooses
    rule = affinity["podAntiAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][0]
    assert rule["topologyKey"] == PLACEMENT_LABEL_KEY
    assert rule["labelSelector"] == {"matchLabels": {"app": "cartservice"}}
    assert patch["metadata"]["annotations"][MANAGED_ANNOTATION] == "affinity-r3-anti-affine"


def test_build_patch_always_recreates_and_clears_stale_v1_pin():
    patch = engine.build_patch("a", "w1", 1, engine.MODE_PACKED, WORKERS)
    assert patch["spec"]["strategy"] == {"type": "Recreate", "rollingUpdate": None}
    assert patch["spec"]["template"]["spec"]["nodeSelector"] == {PLACEMENT_LABEL_KEY: None}


def test_build_patch_rejects_r2_as_deliberately_unsupported():
    with pytest.raises(ValueError, match="r=2 is deliberately unsupported"):
        engine.build_patch("a", "w1", 2, engine.MODE_PACKED, WORKERS)


def test_build_patch_rejects_unknown_mode():
    with pytest.raises(ValueError, match="unsupported mode"):
        engine.build_patch("a", "w1", 1, "zoned", WORKERS)


def test_build_patch_rejects_empty_node_names():
    with pytest.raises(ValueError, match="non-empty"):
        engine.build_patch("a", "w1", 1, engine.MODE_PACKED, [])


def test_build_patch_pinned_requires_node_name():
    with pytest.raises(ValueError, match="node_name is required"):
        engine.build_patch("a", None, 3, engine.MODE_PACKED, WORKERS)


def test_build_patch_pinned_rejects_unknown_node():
    with pytest.raises(ValueError, match="not in node_names"):
        engine.build_patch("a", "w9", 1, engine.MODE_PACKED, WORKERS)


def test_build_patch_anti_affine_rejects_a_node_pin():
    with pytest.raises(ValueError, match="no node pin"):
        engine.build_patch("a", "w1", 3, engine.MODE_ANTI_AFFINE, WORKERS)


def test_build_patch_anti_affine_needs_three_distinct_nodes():
    with pytest.raises(ValueError, match="needs >= 3 distinct nodes"):
        engine.build_patch("a", None, 3, engine.MODE_ANTI_AFFINE, ["w1", "w2", "w1"])


# ── live readers ──────────────────────────────────────────────────────


def test_ready_pod_nodes_uses_deployment_match_labels():
    api = _api()
    _pod_list(api, [_pod("w1"), _pod("w2")])
    dep = _dep("frontend", match_labels={"app": "fe", "tier": "web"})
    assert engine._ready_pod_nodes(api, "ns", dep) == ["w1", "w2"]
    api.core.list_namespaced_pod.assert_called_once_with("ns", label_selector="app=fe,tier=web")


def test_ready_pod_nodes_falls_back_to_app_label():
    api = _api()
    _pod_list(api, [_pod("w1")])
    assert engine._ready_pod_nodes(api, "ns", _dep("frontend")) == ["w1"]
    api.core.list_namespaced_pod.assert_called_once_with("ns", label_selector="app=frontend")


def test_ready_pod_nodes_empty_match_labels_falls_back():
    api = _api()
    _pod_list(api, [])
    assert engine._ready_pod_nodes(api, "ns", _dep("fe", match_labels={})) == []
    api.core.list_namespaced_pod.assert_called_once_with("ns", label_selector="app=fe")


def test_ready_pod_nodes_filters_non_ready_states():
    api = _api()
    pods = [
        _pod("w1"),  # counted
        _pod("w2", phase="Pending"),  # not Running
        _pod("w3", ready=False),  # Ready=False
        _pod("w4", conditions=None),  # no conditions at all
        _pod(None),  # no node assigned
        _pod("w5", has_spec=False),  # no pod spec
    ]
    pods[1].status.phase = "Pending"
    no_status = _pod("w6")
    no_status.status = None
    _pod_list(api, pods + [no_status])
    assert engine._ready_pod_nodes(api, "ns", _dep("fe")) == ["w1"]


def test_ready_pod_nodes_api_error_reads_as_no_pods():
    api = _api()
    api.core.list_namespaced_pod.side_effect = ApiException(status=500)
    assert engine._ready_pod_nodes(api, "ns", _dep("fe")) == []


def test_pinned_node_extracts_hostname_pin():
    assert engine._pinned_node(_dep("a", affinity=_affinity_pin("w3"))) == "w3"


@pytest.mark.parametrize(
    "affinity",
    [
        None,
        "no-node-affinity",
        "no-required",
        "no-terms",
        "no-expressions",
        "wrong-key",
        "wrong-operator",
        "no-values",
    ],
)
def test_pinned_node_absent_or_foreign_affinity_is_none(affinity):
    if affinity == "no-node-affinity":
        built = MagicMock()
        built.node_affinity = None
    elif affinity == "no-required":
        built = MagicMock()
        built.node_affinity.required_during_scheduling_ignored_during_execution = None
    elif affinity == "no-terms":
        built = _affinity_pin("w1")
        required = built.node_affinity.required_during_scheduling_ignored_during_execution
        required.node_selector_terms = None
    elif affinity == "no-expressions":
        built = _affinity_pin("w1")
        terms = built.node_affinity.required_during_scheduling_ignored_during_execution
        terms.node_selector_terms[0].match_expressions = None
    elif affinity == "wrong-key":
        built = _affinity_pin("w1", key="topology.kubernetes.io/zone")
    elif affinity == "wrong-operator":
        built = _affinity_pin("w1", operator="NotIn")
    elif affinity == "no-values":
        built = _affinity_pin("w1", values=[])
    else:
        built = affinity
    assert engine._pinned_node(_dep("a", affinity=built)) is None


def _ready_dep(name="fe", replicas=2, generation=3, observed=3, counts=2):
    dep = _dep(name, match_labels={"app": name}, replicas=replicas, generation=generation)
    dep.status.observed_generation = observed
    dep.status.ready_replicas = counts
    dep.status.updated_replicas = counts
    dep.status.available_replicas = counts
    return dep


def test_deployment_ready_all_green():
    api = _api()
    api.apps.read_namespaced_deployment.return_value = _ready_dep()
    _pod_list(api, [_pod("w1"), _pod("w2")])
    assert engine._deployment_ready(api, "ns", "fe") is True


def test_deployment_ready_stale_generation():
    api = _api()
    api.apps.read_namespaced_deployment.return_value = _ready_dep(generation=4, observed=3)
    assert engine._deployment_ready(api, "ns", "fe") is False


def test_deployment_ready_unavailable_replicas():
    api = _api()
    dep = _ready_dep()
    dep.status.available_replicas = 1
    api.apps.read_namespaced_deployment.return_value = dep
    assert engine._deployment_ready(api, "ns", "fe") is False


def test_deployment_ready_pod_level_guard():
    api = _api()
    api.apps.read_namespaced_deployment.return_value = _ready_dep()
    _pod_list(api, [_pod("w1")])  # status says 2 ready, pods say 1
    assert engine._deployment_ready(api, "ns", "fe") is False


def test_deployment_ready_defaults_when_status_missing():
    api = _api()
    dep = _dep("fe", replicas=None, generation=None)
    dep.status = None
    api.apps.read_namespaced_deployment.return_value = dep
    assert engine._deployment_ready(api, "ns", "fe") is False


def test_wait_for_rollouts_returns_empty_on_success():
    api = _api()
    api.apps.read_namespaced_deployment.return_value = _ready_dep(replicas=1, counts=1)
    _pod_list(api, [_pod("w1")])
    assert engine.wait_for_rollouts(api, "ns", ["fe"], timeout=10, poll_seconds=0) == []


def test_wait_for_rollouts_polls_until_ready():
    api = _api()
    not_ready = _ready_dep(replicas=1, generation=2, observed=1, counts=0)
    ready = _ready_dep(replicas=1, counts=1)
    api.apps.read_namespaced_deployment.side_effect = [not_ready, ready]
    _pod_list(api, [_pod("w1")])
    assert engine.wait_for_rollouts(api, "ns", ["fe"], timeout=10, poll_seconds=0) == []
    assert api.apps.read_namespaced_deployment.call_count == 2


def test_wait_for_rollouts_times_out_with_pending():
    api = _api()
    api.apps.read_namespaced_deployment.side_effect = ApiException(status=500)
    assert engine.wait_for_rollouts(api, "ns", ["b", "a"], timeout=0, poll_seconds=0) == [
        "a",
        "b",
    ]


def test_wait_for_rollouts_no_names_is_a_noop():
    api = _api()
    assert engine.wait_for_rollouts(api, "ns", [], timeout=10) == []
    api.apps.read_namespaced_deployment.assert_not_called()


def test_live_service_nodes_reads_distinct_ready_nodes():
    api = _api()
    api.apps.read_namespaced_deployment.return_value = _dep("fe", match_labels={"app": "fe"})
    _pod_list(api, [_pod("w2"), _pod("w2"), _pod("w1")])
    assert engine.live_service_nodes(api, "ns", ["fe"]) == {"fe": ["w1", "w2"]}


def test_live_service_nodes_missing_deployment_reads_empty():
    api = _api()
    api.apps.read_namespaced_deployment.side_effect = ApiException(status=404)
    assert engine.live_service_nodes(api, "ns", ["gone"]) == {"gone": []}


# ── apply_placement ───────────────────────────────────────────────────


def _deployment_list(api, deps):
    api.apps.list_namespaced_deployment.return_value = MagicMock(items=deps)


def test_apply_placement_pins_each_assigned_service():
    api = _api()
    result = engine.apply_placement(
        api, "ns", {"b": "w2", "a": "w1"}, 1, engine.MODE_PACKED, WORKERS, wait=False
    )
    assert result.applied == ["a", "b"]
    assert result.pending == []
    assert result.duration_seconds >= 0
    names = [c.args[0] for c in api.apps.patch_namespaced_deployment.call_args_list]
    assert names == ["a", "b"]
    patch_a = api.apps.patch_namespaced_deployment.call_args_list[0].args[2]
    assert _pin_values(patch_a)["values"] == ["w1"]


def test_apply_placement_anti_affine_discovers_app_deployments():
    api = _api()
    _deployment_list(api, [_dep("frontend"), _dep("loadgenerator"), _dep("chaos-operator-ce")])
    result = engine.apply_placement(
        api, "ns", None, 3, engine.MODE_ANTI_AFFINE, WORKERS, wait=False
    )
    assert result.applied == ["frontend"]  # infra + loadgenerator excluded
    patch = api.apps.patch_namespaced_deployment.call_args.args[2]
    assert patch["spec"]["template"]["spec"]["affinity"]["nodeAffinity"] is None


def test_apply_placement_anti_affine_explicit_services_skip_discovery():
    # The session driver passes its own service set; the engine must use
    # it verbatim instead of re-discovering (and resurrecting) deployments
    # the caller excluded.
    api = _api()
    result = engine.apply_placement(
        api, "ns", None, 3, engine.MODE_ANTI_AFFINE, WORKERS, wait=False, services=["b", "a"]
    )
    assert result.applied == ["a", "b"]
    api.apps.list_namespaced_deployment.assert_not_called()


def test_apply_placement_anti_affine_empty_explicit_services_raise():
    with pytest.raises(ValueError, match="no application deployments"):
        engine.apply_placement(_api(), "ns", None, 3, engine.MODE_ANTI_AFFINE, WORKERS, services=[])


def test_apply_placement_anti_affine_rejects_an_assignment():
    with pytest.raises(ValueError, match="takes no assignment"):
        engine.apply_placement(_api(), "ns", {"a": "w1"}, 3, engine.MODE_ANTI_AFFINE, WORKERS)


def test_apply_placement_anti_affine_requires_some_deployments():
    api = _api()
    _deployment_list(api, [])
    with pytest.raises(ValueError, match="no application deployments"):
        engine.apply_placement(api, "ns", None, 3, engine.MODE_ANTI_AFFINE, WORKERS)


def test_apply_placement_pinned_requires_assignment():
    with pytest.raises(ValueError, match="non-empty assignment"):
        engine.apply_placement(_api(), "ns", None, 1, engine.MODE_PACKED, WORKERS)


def test_apply_placement_rejects_unsupported_r():
    with pytest.raises(ValueError, match="unsupported replica count"):
        engine.apply_placement(_api(), "ns", {"a": "w1"}, 2, engine.MODE_PACKED, WORKERS)


def test_apply_placement_waits_and_reports_pending(caplog):
    api = _api()
    api.apps.read_namespaced_deployment.side_effect = ApiException(status=500)
    with caplog.at_level("WARNING"):
        result = engine.apply_placement(
            api, "ns", {"a": "w1"}, 1, engine.MODE_PACKED, WORKERS, timeout=0, poll_seconds=0
        )
    assert result.pending == ["a"]
    assert "not ready" in caplog.text


def test_apply_placement_waits_until_ready():
    api = _api()
    api.apps.read_namespaced_deployment.return_value = _ready_dep("a", replicas=1, counts=1)
    _pod_list(api, [_pod("w1")])
    result = engine.apply_placement(
        api, "ns", {"a": "w1"}, 1, engine.MODE_PACKED, WORKERS, timeout=5, poll_seconds=0
    )
    assert result.pending == []


# ── verify_placement: pass + every failure mode ───────────────────────


def _managed_dep(name, value, nodes, affinity=None):
    dep = _dep(
        name,
        annotations={MANAGED_ANNOTATION: value},
        match_labels={"app": name},
        affinity=affinity,
    )
    dep._nodes = nodes
    return dep


def _verify_api(deps):
    api = _api()
    _deployment_list(api, deps)

    def pods_for(namespace, label_selector):
        name = label_selector.split("=", 1)[1]
        dep = next(d for d in deps if d.metadata.name == name)
        return MagicMock(items=[_pod(node) for node in dep._nodes])

    api.core.list_namespaced_pod.side_effect = pods_for
    return api


def test_verify_anti_affine_passes_on_three_distinct_nodes():
    api = _verify_api(
        [
            _managed_dep("a", "affinity-r3-anti-affine", ["w1", "w2", "w3"]),
            _managed_dep("b", "affinity-r3-anti-affine", ["w2", "w3", "w4"]),
        ]
    )
    result = engine.verify_placement(api, "ns", 3, engine.MODE_ANTI_AFFINE)
    assert result.passed is True
    assert [c.ok for c in result.services] == [True, True]
    payload = result.to_dict()
    assert payload["passed"] is True
    assert payload["services"][0] == {
        "service": "a",
        "ok": True,
        "reason": "",
        "readyReplicas": 3,
        "nodes": ["w1", "w2", "w3"],
        "assignedNode": None,
    }


def test_verify_anti_affine_fails_when_replicas_share_a_node():
    api = _verify_api([_managed_dep("a", "affinity-r3-anti-affine", ["w1", "w1", "w2"])])
    result = engine.verify_placement(api, "ns", 3, engine.MODE_ANTI_AFFINE)
    assert result.passed is False
    assert "2 distinct node(s), expected 3" in result.services[0].reason


def test_verify_fails_on_wrong_ready_replica_count():
    api = _verify_api([_managed_dep("a", "affinity-r3-anti-affine", ["w1", "w2"])])
    result = engine.verify_placement(api, "ns", 3, engine.MODE_ANTI_AFFINE)
    assert "2 ready replica(s), expected 3" in result.services[0].reason


def test_verify_packed_passes_on_one_pinned_node():
    api = _verify_api(
        [_managed_dep("a", "affinity-r3-packed", ["w2", "w2", "w2"], affinity=_affinity_pin("w2"))]
    )
    result = engine.verify_placement(api, "ns", 3, engine.MODE_PACKED)
    assert result.passed is True
    assert result.services[0].assigned_node == "w2"


def test_verify_packed_fails_when_spread_over_nodes():
    api = _verify_api(
        [_managed_dep("a", "affinity-r3-packed", ["w1", "w2", "w2"], affinity=_affinity_pin("w2"))]
    )
    result = engine.verify_placement(api, "ns", 3, engine.MODE_PACKED)
    assert "expected exactly 1" in result.services[0].reason


def test_verify_r1_passes_on_the_assigned_node():
    api = _verify_api(
        [_managed_dep("a", "affinity-r1-packed", ["w1"], affinity=_affinity_pin("w1"))]
    )
    assert engine.verify_placement(api, "ns", 1, engine.MODE_PACKED).passed is True


def test_verify_r1_fails_on_the_wrong_node():
    api = _verify_api(
        [_managed_dep("a", "affinity-r1-packed", ["w2"], affinity=_affinity_pin("w1"))]
    )
    result = engine.verify_placement(api, "ns", 1, engine.MODE_PACKED)
    assert "replicas on 'w2', pinned to 'w1'" in result.services[0].reason


def test_verify_pinned_fails_without_a_pin():
    api = _verify_api([_managed_dep("a", "affinity-r1-packed", ["w1"], affinity=None)])
    result = engine.verify_placement(api, "ns", 1, engine.MODE_PACKED)
    assert "no node pin found" in result.services[0].reason


def test_verify_flags_stale_mode_annotation():
    api = _verify_api([_managed_dep("a", "affinity-r3-packed", ["w1", "w2", "w3"])])
    result = engine.verify_placement(api, "ns", 3, engine.MODE_ANTI_AFFINE)
    assert "managed annotation is 'affinity-r3-packed'" in result.services[0].reason


def test_verify_skips_unmanaged_and_v1_managed_deployments():
    api = _api()
    none_annotated = _dep("plain", annotations=None)
    legacy_managed = _dep("legacy", annotations={MANAGED_ANNOTATION: "colocate"})
    _deployment_list(api, [none_annotated, legacy_managed])
    result = engine.verify_placement(api, "ns", 1, engine.MODE_PACKED)
    assert result.services == []
    assert result.passed is False  # nothing managed = nothing verified = FAIL


def test_verify_rejects_invalid_combo():
    with pytest.raises(ValueError, match="unsupported replica count"):
        engine.verify_placement(_api(), "ns", 2, engine.MODE_PACKED)


# ── restore ───────────────────────────────────────────────────────────


def test_restore_resets_managed_deployments_to_single_replica():
    api = _api()
    managed = _dep("a", annotations={MANAGED_ANNOTATION: "affinity-r3-anti-affine"})
    stale_pin = _dep("b", node_selector={PLACEMENT_LABEL_KEY: "w1"})
    untouched = _dep("c")
    infra = _dep("chaos-operator-ce", annotations={MANAGED_ANNOTATION: "affinity-r1-packed"})
    _deployment_list(api, [managed, stale_pin, untouched, infra])

    cleared = engine.restore(api, "ns", wait=False)

    assert cleared == ["a", "b"]
    for args in api.apps.patch_namespaced_deployment.call_args_list:
        patch = args.args[2]
        assert patch["spec"]["replicas"] == 1
        assert patch["spec"]["template"]["spec"]["affinity"] is None
        assert patch["spec"]["template"]["spec"]["nodeSelector"] == {PLACEMENT_LABEL_KEY: None}
        assert patch["metadata"]["annotations"] == {MANAGED_ANNOTATION: None}
        assert patch["spec"]["strategy"]["type"] == "RollingUpdate"


def test_restore_waits_for_rollouts():
    api = _api()
    _deployment_list(api, [_dep("a", annotations={MANAGED_ANNOTATION: "affinity-r1-packed"})])
    api.apps.read_namespaced_deployment.return_value = _ready_dep("a", replicas=1, counts=1)
    _pod_list(api, [_pod("w1")])
    assert engine.restore(api, "ns", timeout=5, poll_seconds=0) == ["a"]


def test_restore_warns_on_pending_rollouts(caplog):
    api = _api()
    _deployment_list(api, [_dep("a", annotations={MANAGED_ANNOTATION: "affinity-r1-packed"})])
    api.apps.read_namespaced_deployment.side_effect = ApiException(status=500)
    with caplog.at_level("WARNING"):
        engine.restore(api, "ns", timeout=0, poll_seconds=0)
    assert "not ready" in caplog.text


def test_restore_nothing_managed_is_a_noop():
    api = _api()
    _deployment_list(api, [_dep("a"), _dep("b")])
    assert engine.restore(api, "ns") == []
    api.apps.patch_namespaced_deployment.assert_not_called()


# ── K8sApi ────────────────────────────────────────────────────────────


def test_k8sapi_from_cluster_builds_live_clients(monkeypatch):
    ensure = MagicMock()
    apps = MagicMock()
    core = MagicMock()
    monkeypatch.setattr(engine, "ensure_k8s_config", ensure)
    monkeypatch.setattr(engine.client, "AppsV1Api", MagicMock(return_value=apps))
    monkeypatch.setattr(engine.client, "CoreV1Api", MagicMock(return_value=core))
    api = engine.K8sApi.from_cluster()
    ensure.assert_called_once_with()
    assert api.apps is apps
    assert api.core is core


# ── module surface ────────────────────────────────────────────────────


def test_annotation_values_are_namespaced_per_cell():
    assert engine.annotation_value(1, engine.MODE_PACKED) == "affinity-r1-packed"
    assert engine.annotation_value(3, engine.MODE_ANTI_AFFINE) == "affinity-r3-anti-affine"


def test_supported_cells_exclude_r2():
    assert engine.SUPPORTED_REPLICAS == (1, 3)
    assert set(engine.MODES) == {engine.MODE_PACKED, engine.MODE_ANTI_AFFINE}
