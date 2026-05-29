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
