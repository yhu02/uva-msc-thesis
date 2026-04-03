"""Tests for the dynamic service topology parser."""

from pathlib import Path

from chaosprobe.config.topology import (
    _env_name_to_description,
    _extract_dependencies_from_deployment,
    _infer_protocol,
    parse_topology_from_directory,
    parse_topology_from_manifests,
    parse_topology_from_scenario,
)


class TestInferProtocol:
    def test_redis_service(self):
        assert _infer_protocol("redis-cart", "6379") == "tcp"

    def test_redis_prefix(self):
        assert _infer_protocol("redis", "6379") == "tcp"

    def test_memcached(self):
        assert _infer_protocol("memcached", "11211") == "tcp"

    def test_grpc_service(self):
        assert _infer_protocol("productcatalogservice", "3550") == "grpc"

    def test_port_6379_any_name(self):
        assert _infer_protocol("my-cache", "6379") == "tcp"


class TestEnvNameToDescription:
    def test_service_addr(self):
        assert _env_name_to_description("PRODUCT_CATALOG_SERVICE_ADDR") == "Product Catalog"

    def test_simple_addr(self):
        assert _env_name_to_description("REDIS_ADDR") == "Redis"

    def test_multi_word(self):
        assert _env_name_to_description("SHIPPING_SERVICE_ADDR") == "Shipping"


class TestExtractDependencies:
    def test_basic_deployment(self):
        deployment = {
            "metadata": {"name": "frontend"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "server",
                                "env": [
                                    {"name": "PRODUCT_CATALOG_SERVICE_ADDR", "value": "productcatalogservice:3550"},
                                    {"name": "CART_SERVICE_ADDR", "value": "cartservice:7070"},
                                    {"name": "PORT", "value": "8080"},
                                ],
                            }
                        ]
                    }
                }
            },
        }
        routes = _extract_dependencies_from_deployment(deployment)
        assert len(routes) == 2
        assert routes[0] == ("frontend", "productcatalogservice", "productcatalogservice:3550", "grpc", "Product Catalog")
        assert routes[1] == ("frontend", "cartservice", "cartservice:7070", "grpc", "Cart")

    def test_redis_addr(self):
        deployment = {
            "metadata": {"name": "cartservice"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "server",
                                "env": [
                                    {"name": "REDIS_ADDR", "value": "redis-cart:6379"},
                                ],
                            }
                        ]
                    }
                }
            },
        }
        routes = _extract_dependencies_from_deployment(deployment)
        assert len(routes) == 1
        assert routes[0] == ("cartservice", "redis-cart", "redis-cart:6379", "tcp", "Redis")

    def test_skip_non_addr_env(self):
        deployment = {
            "metadata": {"name": "frontend"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "server",
                                "env": [
                                    {"name": "PORT", "value": "8080"},
                                    {"name": "ENABLE_PROFILER", "value": "0"},
                                    {"name": "DISABLE_PROFILER", "value": "1"},
                                ],
                            }
                        ]
                    }
                }
            },
        }
        routes = _extract_dependencies_from_deployment(deployment)
        assert len(routes) == 0

    def test_skip_self_reference(self):
        deployment = {
            "metadata": {"name": "myservice"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "server",
                                "env": [
                                    {"name": "MY_SERVICE_ADDR", "value": "myservice:8080"},
                                ],
                            }
                        ]
                    }
                }
            },
        }
        routes = _extract_dependencies_from_deployment(deployment)
        assert len(routes) == 0

    def test_no_env(self):
        deployment = {
            "metadata": {"name": "redis"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": "redis"}]
                    }
                }
            },
        }
        routes = _extract_dependencies_from_deployment(deployment)
        assert len(routes) == 0


class TestParseTopologyFromManifests:
    def test_mixed_kinds(self):
        manifests = [
            {"kind": "Service", "metadata": {"name": "frontend"}},
            {
                "kind": "Deployment",
                "metadata": {"name": "frontend"},
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "server",
                                    "env": [
                                        {"name": "CART_SERVICE_ADDR", "value": "cartservice:7070"},
                                    ],
                                }
                            ]
                        }
                    }
                },
            },
        ]
        routes = parse_topology_from_manifests(manifests)
        assert len(routes) == 1
        assert routes[0][0] == "frontend"
        assert routes[0][1] == "cartservice"

    def test_deduplication(self):
        dep = {
            "kind": "Deployment",
            "metadata": {"name": "frontend"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "c1",
                                "env": [{"name": "CART_SERVICE_ADDR", "value": "cartservice:7070"}],
                            },
                            {
                                "name": "c2",
                                "env": [{"name": "CART_SERVICE_ADDR", "value": "cartservice:7070"}],
                            },
                        ]
                    }
                }
            },
        }
        routes = parse_topology_from_manifests([dep])
        # Same (source, target) pair from two containers — deduplicated
        assert len(routes) == 1

    def test_empty_list(self):
        assert parse_topology_from_manifests([]) == []


class TestParseTopologyFromDirectory:
    def test_real_deploy_dir(self):
        """Parse the actual Online Boutique deploy directory."""
        deploy_dir = str(Path(__file__).parent.parent / "scenarios" / "online-boutique" / "deploy")
        routes = parse_topology_from_directory(deploy_dir)

        # Should find all the known dependencies
        pairs = {(r[0], r[1]) for r in routes}
        assert ("frontend", "productcatalogservice") in pairs
        assert ("frontend", "cartservice") in pairs
        assert ("frontend", "checkoutservice") in pairs
        assert ("checkoutservice", "paymentservice") in pairs
        assert ("checkoutservice", "emailservice") in pairs
        assert ("cartservice", "redis-cart") in pairs
        assert ("recommendationservice", "productcatalogservice") in pairs

        # Should have at least 14 edges (the full OB graph)
        assert len(routes) >= 14

    def test_nonexistent_dir(self):
        routes = parse_topology_from_directory("/nonexistent/path")
        assert routes == []


class TestParseTopologyFromScenario:
    def test_with_deploy_subdir(self):
        """Parse topology from a scenario with a deploy/ sibling directory."""
        scenario = {
            "path": str(Path(__file__).parent.parent / "scenarios" / "online-boutique" / "contention-checkout-latency"),
        }
        routes = parse_topology_from_scenario(scenario)
        # deploy/ is a sibling, so it finds it via the parent
        pairs = {(r[0], r[1]) for r in routes}
        assert ("frontend", "productcatalogservice") in pairs

    def test_empty_scenario(self):
        routes = parse_topology_from_scenario({"path": "/tmp/nonexistent"})
        assert routes == []
