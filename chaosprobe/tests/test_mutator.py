"""Tests for PlacementMutator kubernetes helpers."""

from unittest.mock import MagicMock

from chaosprobe.placement.mutator import PlacementMutator


def _mutator():
    m = PlacementMutator.__new__(PlacementMutator)
    m.namespace = "test-ns"
    m.core_api = MagicMock()
    return m


class TestGetPodNode:
    def test_returns_node_of_scheduled_pod(self):
        m = _mutator()
        pod = MagicMock()
        pod.spec.node_name = "node-a"
        m.core_api.list_namespaced_pod.return_value = MagicMock(items=[pod])
        assert m._get_pod_node("frontend") == "node-a"

    def test_returns_none_when_unscheduled(self):
        m = _mutator()
        pod = MagicMock()
        pod.spec.node_name = None
        m.core_api.list_namespaced_pod.return_value = MagicMock(items=[pod])
        assert m._get_pod_node("frontend") is None


def _mutator_with_deployments(dep_dicts):
    """Build a mutator whose ``apps_api`` returns the given deployment
    dicts (each a parsed Deployment spec with metadata + container env)."""
    m = PlacementMutator.__new__(PlacementMutator)
    m.namespace = "ns"
    m.apps_api = MagicMock()
    items = []
    for d in dep_dicts:
        obj = MagicMock()
        obj.metadata.name = d["metadata"]["name"]
        obj._dep_dict = d
        items.append(obj)
    m.apps_api.list_namespaced_deployment.return_value = MagicMock(items=items)
    m.apps_api.api_client.sanitize_for_serialization.side_effect = lambda o: o._dep_dict
    return m


def _deployment(name, env):
    return {
        "metadata": {"name": name},
        "spec": {"template": {"spec": {"containers": [{"env": env}]}}},
    }


class TestScaleDeployments:
    def _mutator(self, names):
        m = PlacementMutator.__new__(PlacementMutator)
        m.namespace = "ns"
        m.apps_api = MagicMock()
        items = []
        for n in names:
            obj = MagicMock()
            obj.metadata.name = n
            items.append(obj)
        m.apps_api.list_namespaced_deployment.return_value = MagicMock(items=items)
        return m

    def test_scales_app_deployments_skipping_infra_and_loadgen(self):
        from chaosprobe.orchestrator.preflight import LITMUS_INFRA_DEPLOYMENTS

        infra = sorted(LITMUS_INFRA_DEPLOYMENTS)[0]
        m = self._mutator(["frontend", "cartservice", "loadgenerator", infra])
        scaled = m.scale_deployments(3)
        assert scaled == ["frontend", "cartservice"]
        patched = {
            c.args[0]: c.args[2] for c in m.apps_api.patch_namespaced_deployment.call_args_list
        }
        assert patched == {
            "frontend": {"spec": {"replicas": 3}},
            "cartservice": {"spec": {"replicas": 3}},
        }


class TestServiceDependencyRoutes:
    """`get_service_dependency_routes` preserves the target ``host:port``
    and inferred protocol (``grpc``/``tcp``) that the 2-tuple
    `get_service_dependencies` discards, so gRPC backends are probed over
    their real port instead of a non-existent HTTP ``/healthz``."""

    def test_returns_protocol_and_port_per_edge(self):
        m = _mutator_with_deployments(
            [
                _deployment(
                    "checkoutservice",
                    [
                        {
                            "name": "PRODUCT_CATALOG_SERVICE_ADDR",
                            "value": "productcatalogservice:3550",
                        },
                        {"name": "CART_SERVICE_ADDR", "value": "redis-cart:6379"},
                    ],
                )
            ]
        )

        routes = m.get_service_dependency_routes()
        by_target = {r[1]: r for r in routes}

        assert by_target["productcatalogservice"][2] == "productcatalogservice:3550"
        assert by_target["productcatalogservice"][3] == "grpc"
        # Redis is inferred as a TCP target (probed via TCP connect).
        assert by_target["redis-cart"][2] == "redis-cart:6379"
        assert by_target["redis-cart"][3] == "tcp"
        # The source of every edge is the owning deployment.
        assert all(r[0] == "checkoutservice" for r in routes)

    def test_dedupes_repeated_source_target_edge(self):
        m = _mutator_with_deployments(
            [
                _deployment(
                    "frontend",
                    [
                        {
                            "name": "PRODUCT_CATALOG_SERVICE_ADDR",
                            "value": "productcatalogservice:3550",
                        },
                        {"name": "PRODUCTCATALOG_ADDR", "value": "productcatalogservice:3550"},
                    ],
                )
            ]
        )

        routes = m.get_service_dependency_routes()
        assert len([r for r in routes if r[1] == "productcatalogservice"]) == 1

    def test_skips_litmus_infra_deployments(self):
        from chaosprobe.orchestrator.preflight import LITMUS_INFRA_DEPLOYMENTS

        infra_name = next(iter(LITMUS_INFRA_DEPLOYMENTS))
        m = _mutator_with_deployments(
            [_deployment(infra_name, [{"name": "FOO_SERVICE_ADDR", "value": "foo:1234"}])]
        )
        assert m.get_service_dependency_routes() == []

    def test_serializer_exception_skips_deployment(self):
        m = _mutator_with_deployments(
            [
                _deployment(
                    "checkoutservice",
                    [{"name": "CURRENCY_SERVICE_ADDR", "value": "currencyservice:7000"}],
                )
            ]
        )
        m.apps_api.api_client.sanitize_for_serialization.side_effect = RuntimeError("boom")
        assert m.get_service_dependency_routes() == []

    def test_get_service_dependencies_projects_to_source_target_pairs(self):
        m = _mutator_with_deployments(
            [
                _deployment(
                    "checkoutservice",
                    [
                        {
                            "name": "PRODUCT_CATALOG_SERVICE_ADDR",
                            "value": "productcatalogservice:3550",
                        }
                    ],
                )
            ]
        )
        assert m.get_service_dependencies() == [("checkoutservice", "productcatalogservice")]
