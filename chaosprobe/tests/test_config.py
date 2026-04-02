"""Tests for configuration loading and validation."""

import pytest

from chaosprobe.config.loader import load_scenario, merge_configs
from chaosprobe.config.validator import validate_scenario, ValidationError, _validate_cluster_config


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


class TestClusterConfig:
    """Tests for cluster configuration validation and loading."""

    def test_validate_valid_cluster_config(self):
        """Test validating a valid cluster config."""
        cluster = {"workers": {"count": 3, "cpu": 2, "memory": 4096, "disk": 20}}
        errors = _validate_cluster_config(cluster)
        assert errors == []

    def test_validate_missing_workers(self):
        """Test validation fails when workers section is missing."""
        errors = _validate_cluster_config({})
        assert any("workers" in e for e in errors)

    def test_validate_invalid_count(self):
        """Test validation fails for non-positive count."""
        errors = _validate_cluster_config({"workers": {"count": 0}})
        assert any("count" in e for e in errors)

    def test_validate_invalid_cpu(self):
        """Test validation fails for non-positive cpu."""
        errors = _validate_cluster_config({"workers": {"cpu": -1}})
        assert any("cpu" in e for e in errors)

    def test_validate_invalid_memory(self):
        """Test validation fails for memory below minimum."""
        errors = _validate_cluster_config({"workers": {"memory": 100}})
        assert any("memory" in e for e in errors)

    def test_validate_invalid_provider(self):
        """Test validation fails for unknown provider."""
        errors = _validate_cluster_config(
            {"workers": {"count": 2}, "provider": "invalid"}
        )
        assert any("provider" in e for e in errors)

    def test_validate_valid_provider(self):
        """Test validation passes for valid providers."""
        for provider in ("vagrant", "kubespray"):
            errors = _validate_cluster_config(
                {"workers": {"count": 2}, "provider": provider}
            )
            assert errors == []

    def test_load_scenario_with_cluster_config(self, tmp_path):
        """Test loading a scenario with cluster.yaml."""
        # Create experiment.yaml
        experiment = tmp_path / "experiment.yaml"
        experiment.write_text("""
apiVersion: litmuschaos.io/v1alpha1
kind: ChaosEngine
metadata:
  name: test-engine
spec:
  engineState: active
  appinfo:
    appns: default
    applabel: app=test
    appkind: deployment
  chaosServiceAccount: litmus-admin
  experiments:
    - name: pod-delete
""")

        # Create cluster.yaml
        cluster_file = tmp_path / "cluster.yaml"
        cluster_file.write_text("""
cluster:
  provider: vagrant
  workers:
    count: 3
    cpu: 4
    memory: 8192
    disk: 40
""")

        scenario = load_scenario(str(tmp_path))
        assert "cluster" in scenario
        assert scenario["cluster"]["provider"] == "vagrant"
        assert scenario["cluster"]["workers"]["count"] == 3
        assert scenario["cluster"]["workers"]["cpu"] == 4
        assert scenario["cluster"]["workers"]["memory"] == 8192

    def test_load_scenario_without_cluster_config(self, tmp_path):
        """Test loading a scenario without cluster.yaml has no cluster key."""
        experiment = tmp_path / "experiment.yaml"
        experiment.write_text("""
apiVersion: litmuschaos.io/v1alpha1
kind: ChaosEngine
metadata:
  name: test-engine
spec:
  engineState: active
  appinfo:
    appns: default
    applabel: app=test
    appkind: deployment
  chaosServiceAccount: litmus-admin
  experiments:
    - name: pod-delete
""")

        scenario = load_scenario(str(tmp_path))
        assert "cluster" not in scenario

    def test_validate_scenario_with_valid_cluster(self, sample_scenario):
        """Test that validation passes with a valid cluster config."""
        sample_scenario["cluster"] = {
            "workers": {"count": 2, "cpu": 2, "memory": 2048}
        }
        assert validate_scenario(sample_scenario) is True

    def test_validate_scenario_with_invalid_cluster(self, sample_scenario):
        """Test that validation fails with an invalid cluster config."""
        sample_scenario["cluster"] = {"workers": {"count": -1}}
        with pytest.raises(ValidationError, match="count"):
            validate_scenario(sample_scenario)
