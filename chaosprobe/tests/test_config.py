"""Tests for configuration loading and validation."""

import pytest
import tempfile
import os
from pathlib import Path

from chaosprobe.config.loader import load_scenario, merge_configs
from chaosprobe.config.validator import validate_scenario, ValidationError


class TestConfigLoader:
    """Tests for the new directory-based scenario loader."""

    def test_load_scenario_directory(self, tmp_path):
        """Test loading a scenario from a directory with manifests + ChaosEngine."""
        # Create deployment.yaml
        deployment = tmp_path / "deployment.yaml"
        deployment.write_text(
            """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nginx
spec:
  replicas: 1
  selector:
    matchLabels:
      app: nginx
  template:
    metadata:
      labels:
        app: nginx
    spec:
      containers:
        - name: nginx
          image: nginx:1.21
"""
        )

        # Create experiment.yaml (ChaosEngine)
        experiment = tmp_path / "experiment.yaml"
        experiment.write_text(
            """
apiVersion: litmuschaos.io/v1alpha1
kind: ChaosEngine
metadata:
  name: nginx-pod-delete
spec:
  engineState: active
  appinfo:
    appns: default
    applabel: app=nginx
    appkind: deployment
  chaosServiceAccount: litmus-admin
  experiments:
    - name: pod-delete
"""
        )

        scenario = load_scenario(str(tmp_path))

        assert scenario["path"] == str(tmp_path.resolve())
        assert len(scenario["manifests"]) == 1
        assert len(scenario["experiments"]) == 1
        assert scenario["manifests"][0]["spec"]["kind"] == "Deployment"
        assert scenario["experiments"][0]["spec"]["kind"] == "ChaosEngine"

    def test_load_scenario_single_file(self, tmp_path):
        """Test loading a single ChaosEngine file."""
        engine_file = tmp_path / "experiment.yaml"
        engine_file.write_text(
            """
apiVersion: litmuschaos.io/v1alpha1
kind: ChaosEngine
metadata:
  name: test-engine
spec:
  engineState: active
  appinfo:
    appns: test-ns
    applabel: app=test
    appkind: deployment
  chaosServiceAccount: litmus-admin
  experiments:
    - name: pod-delete
"""
        )

        scenario = load_scenario(str(engine_file))

        assert len(scenario["experiments"]) == 1
        assert len(scenario["manifests"]) == 0
        assert scenario["namespace"] == "test-ns"

    def test_load_scenario_detects_namespace(self, tmp_path):
        """Test that namespace is extracted from ChaosEngine appinfo."""
        experiment = tmp_path / "experiment.yaml"
        experiment.write_text(
            """
apiVersion: litmuschaos.io/v1alpha1
kind: ChaosEngine
metadata:
  name: test
spec:
  engineState: active
  appinfo:
    appns: my-namespace
    applabel: app=test
    appkind: deployment
  chaosServiceAccount: litmus-admin
  experiments:
    - name: pod-delete
"""
        )

        scenario = load_scenario(str(tmp_path))
        assert scenario["namespace"] == "my-namespace"

    def test_load_nonexistent_path(self):
        """Test loading from a path that doesn't exist."""
        with pytest.raises(FileNotFoundError):
            load_scenario("/nonexistent/path")

    def test_load_empty_directory(self, tmp_path):
        """Test loading from an empty directory raises ValueError."""
        with pytest.raises(ValueError, match="No YAML files found"):
            load_scenario(str(tmp_path))

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

    def test_validate_valid_scenario(self, sample_scenario):
        """Test validating a valid scenario."""
        assert validate_scenario(sample_scenario) is True

    def test_validate_missing_experiments(self):
        """Test validation fails when no experiments are present."""
        scenario = {
            "path": "/tmp/test",
            "namespace": "default",
            "manifests": [],
            "experiments": [],
        }
        with pytest.raises(ValidationError, match="at least one ChaosEngine"):
            validate_scenario(scenario)

    def test_validate_invalid_chaos_engine(self):
        """Test validation fails for invalid ChaosEngine spec."""
        scenario = {
            "path": "/tmp/test",
            "namespace": "default",
            "manifests": [],
            "experiments": [
                {
                    "file": "bad.yaml",
                    "spec": {
                        "apiVersion": "litmuschaos.io/v1alpha1",
                        "kind": "ChaosEngine",
                        "metadata": {"name": "test"},
                        "spec": {
                            # Missing appinfo and experiments
                        },
                    },
                }
            ],
        }
        with pytest.raises(ValidationError):
            validate_scenario(scenario)

    def test_validate_manifest_missing_name(self):
        """Test validation fails for manifest without metadata.name."""
        scenario = {
            "path": "/tmp/test",
            "namespace": "default",
            "manifests": [
                {
                    "file": "bad.yaml",
                    "spec": {
                        "apiVersion": "v1",
                        "kind": "Service",
                        "metadata": {},
                    },
                }
            ],
            "experiments": [
                {
                    "file": "experiment.yaml",
                    "spec": {
                        "apiVersion": "litmuschaos.io/v1alpha1",
                        "kind": "ChaosEngine",
                        "metadata": {"name": "test"},
                        "spec": {
                            "appinfo": {
                                "appns": "default",
                                "applabel": "app=test",
                                "appkind": "deployment",
                            },
                            "chaosServiceAccount": "litmus-admin",
                            "experiments": [{"name": "pod-delete"}],
                        },
                    },
                }
            ],
        }
        with pytest.raises(ValidationError, match="metadata.name"):
            validate_scenario(scenario)
