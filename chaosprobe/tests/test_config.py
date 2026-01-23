"""Tests for configuration loading and validation."""

import pytest
import tempfile
from pathlib import Path

from chaosprobe.config.loader import load_scenario, merge_configs
from chaosprobe.config.validator import validate_scenario, ValidationError


class TestConfigLoader:
    """Tests for scenario loading."""

    def test_load_valid_scenario(self):
        """Test loading a valid scenario file."""
        scenario_content = """
apiVersion: chaosprobe.io/v1alpha1
kind: ChaosScenario
metadata:
  name: test-scenario
  description: Test scenario
spec:
  infrastructure:
    namespace: test-ns
    resources:
      - name: test-deployment
        type: deployment
        spec:
          replicas: 1
          image: nginx:1.21
  experiments:
    - name: test-experiment
      type: pod-delete
      target:
        appLabel: "app=test"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(scenario_content)
            f.flush()

            scenario = load_scenario(f.name)

            assert scenario["metadata"]["name"] == "test-scenario"
            assert scenario["spec"]["infrastructure"]["namespace"] == "test-ns"
            assert len(scenario["spec"]["experiments"]) == 1

    def test_load_nonexistent_file(self):
        """Test loading a file that doesn't exist."""
        with pytest.raises(FileNotFoundError):
            load_scenario("/nonexistent/path/scenario.yaml")

    def test_merge_configs(self):
        """Test merging configuration dictionaries."""
        base = {
            "a": 1,
            "b": {"c": 2, "d": 3},
        }
        override = {
            "b": {"c": 10, "e": 5},
            "f": 6,
        }

        merged = merge_configs(base, override)

        assert merged["a"] == 1
        assert merged["b"]["c"] == 10
        assert merged["b"]["d"] == 3
        assert merged["b"]["e"] == 5
        assert merged["f"] == 6


class TestConfigValidator:
    """Tests for scenario validation."""

    def test_validate_minimal_valid_scenario(self):
        """Test validating a minimal valid scenario."""
        scenario = {
            "apiVersion": "chaosprobe.io/v1alpha1",
            "kind": "ChaosScenario",
            "metadata": {"name": "test"},
            "spec": {
                "infrastructure": {
                    "namespace": "test-ns",
                    "resources": [],
                },
                "experiments": [
                    {
                        "name": "test-exp",
                        "type": "pod-delete",
                    }
                ],
            },
        }

        assert validate_scenario(scenario) is True

    def test_validate_missing_required_field(self):
        """Test validation fails for missing required field."""
        scenario = {
            "apiVersion": "chaosprobe.io/v1alpha1",
            "kind": "ChaosScenario",
            # Missing metadata
            "spec": {
                "infrastructure": {
                    "namespace": "test-ns",
                    "resources": [],
                },
                "experiments": [{"name": "test", "type": "pod-delete"}],
            },
        }

        with pytest.raises(ValidationError):
            validate_scenario(scenario)

    def test_validate_invalid_experiment_type(self):
        """Test validation fails for invalid experiment type."""
        scenario = {
            "apiVersion": "chaosprobe.io/v1alpha1",
            "kind": "ChaosScenario",
            "metadata": {"name": "test"},
            "spec": {
                "infrastructure": {
                    "namespace": "test-ns",
                    "resources": [],
                },
                "experiments": [
                    {
                        "name": "test-exp",
                        "type": "invalid-experiment-type",
                    }
                ],
            },
        }

        with pytest.raises(ValidationError):
            validate_scenario(scenario)

    def test_validate_with_probes(self):
        """Test validating a scenario with probes."""
        scenario = {
            "apiVersion": "chaosprobe.io/v1alpha1",
            "kind": "ChaosScenario",
            "metadata": {"name": "test"},
            "spec": {
                "infrastructure": {
                    "namespace": "test-ns",
                    "resources": [],
                },
                "experiments": [
                    {
                        "name": "test-exp",
                        "type": "pod-delete",
                        "probes": [
                            {
                                "name": "http-probe",
                                "type": "httpProbe",
                                "mode": "Continuous",
                                "httpProbe": {
                                    "url": "http://test:80",
                                    "method": {"get": {"criteria": "==", "responseCode": "200"}},
                                },
                            }
                        ],
                    }
                ],
            },
        }

        assert validate_scenario(scenario) is True
