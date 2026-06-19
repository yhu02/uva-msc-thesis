"""Tests for orchestrator.preflight pure helpers."""

import pytest

from chaosprobe.orchestrator.preflight import extract_target_deployment


class TestExtractTargetDeployment:
    def test_extracts_app_label(self):
        scenario = {
            "experiments": [
                {"spec": {"spec": {"appinfo": {"applabel": "app=frontend"}}}},
            ]
        }
        assert extract_target_deployment(scenario) == "frontend"

    def test_skips_experiments_without_app_prefix(self):
        scenario = {
            "experiments": [
                {"spec": {"spec": {"appinfo": {"applabel": "tier=web"}}}},
                {"spec": {"spec": {"appinfo": {"applabel": "app=cart"}}}},
            ]
        }
        assert extract_target_deployment(scenario) == "cart"

    def test_raises_when_no_applabel(self):
        with pytest.raises(ValueError, match="Could not extract target deployment"):
            extract_target_deployment({"experiments": [{"spec": {"spec": {}}}]})


class TestIsStatefulInfra:
    """`is_stateful_infra` keeps datastores/discovery/tracing out of the
    clean-baseline rollout restart (restarting them loses state or breaks
    dependents — e.g. hotelReservation's non-durable Consul)."""

    @pytest.mark.parametrize(
        "name",
        [
            "consul",
            "mongodb-geo",
            "memcached-profile",
            "memcached-reserve",
            "jaeger",
            "redis-cart",  # online-boutique's datastore
            "mongodb",
            "postgres-main",
        ],
    )
    def test_stateful_backing_services_excluded(self, name):
        from chaosprobe.orchestrator.preflight import is_stateful_infra

        assert is_stateful_infra(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "frontend",
            "search",
            "geo",
            "profile",
            "rate",
            "recommendation",
            "reservation",
            "user",
            "productcatalogservice",
            "cartservice",
        ],
    )
    def test_app_services_not_excluded(self, name):
        from chaosprobe.orchestrator.preflight import is_stateful_infra

        # App services (the things we DO want a clean baseline for) must restart.
        assert is_stateful_infra(name) is False
