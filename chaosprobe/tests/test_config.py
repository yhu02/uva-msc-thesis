"""Tests for configuration loading and validation."""

import pytest

from chaosprobe.config.loader import load_scenario, merge_configs
from chaosprobe.config.validator import (
    validate_scenario,
    ValidationError,
    _validate_cluster_config,
    _validate_probe,
    _validate_http_probe,
    _validate_cmd_probe,
    _validate_k8s_probe,
    _validate_prom_probe,
    VALID_PROBE_TYPES,
    VALID_PROBE_MODES,
)


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


# ── Helpers for probe validation tests ──────────────────────

def _make_engine_scenario(probes):
    """Build a minimal valid scenario with given probes on a pod-delete experiment."""
    return {
        "path": "/tmp/test",
        "namespace": "default",
        "manifests": [],
        "experiments": [
            {
                "file": "experiment.yaml",
                "spec": {
                    "apiVersion": "litmuschaos.io/v1alpha1",
                    "kind": "ChaosEngine",
                    "metadata": {"name": "test-engine"},
                    "spec": {
                        "appinfo": {
                            "appns": "default",
                            "applabel": "app=test",
                            "appkind": "deployment",
                        },
                        "chaosServiceAccount": "litmus-admin",
                        "experiments": [
                            {
                                "name": "pod-delete",
                                "spec": {
                                    "probe": probes,
                                },
                            }
                        ],
                    },
                },
            }
        ],
    }


def _valid_http_probe_get():
    return {
        "name": "http-get-probe",
        "type": "httpProbe",
        "mode": "Continuous",
        "httpProbe/inputs": {
            "url": "http://svc.default.svc.cluster.local",
            "method": {"get": {"criteria": "==", "responseCode": "200"}},
        },
        "runProperties": {"probeTimeout": "5s", "interval": "2s", "retry": 2},
    }


def _valid_http_probe_post():
    return {
        "name": "http-post-probe",
        "type": "httpProbe",
        "mode": "Edge",
        "httpProbe/inputs": {
            "url": "http://svc.default.svc.cluster.local",
            "method": {
                "post": {
                    "contentType": "application/json",
                    "body": '{"key": "val"}',
                    "criteria": "==",
                    "responseCode": "200",
                }
            },
        },
        "runProperties": {"probeTimeout": "5s", "interval": "2s", "retry": 1},
    }


def _valid_cmd_probe_inline():
    return {
        "name": "cmd-inline-probe",
        "type": "cmdProbe",
        "mode": "Edge",
        "cmdProbe/inputs": {
            "command": "kubectl get pods -l app=test --no-headers | grep -c Running",
            "comparator": {"type": "int", "criteria": ">=", "value": "1"},
        },
        "runProperties": {"probeTimeout": "10s", "interval": "5s", "retry": 1},
    }


def _valid_cmd_probe_source():
    return {
        "name": "cmd-source-probe",
        "type": "cmdProbe",
        "mode": "OnChaos",
        "cmdProbe/inputs": {
            "command": "nslookup svc.default.svc.cluster.local",
            "comparator": {"type": "string", "criteria": "contains", "value": "Address"},
            "source": {"image": "busybox:1.36", "hostNetwork": False},
        },
        "runProperties": {"probeTimeout": "10s", "interval": "5s", "retry": 2},
    }


def _valid_k8s_probe_present():
    return {
        "name": "k8s-present-probe",
        "type": "k8sProbe",
        "mode": "EOT",
        "k8sProbe/inputs": {
            "group": "apps",
            "version": "v1",
            "resource": "deployments",
            "namespace": "default",
            "labelSelector": "app=test",
            "operation": "present",
        },
        "runProperties": {"probeTimeout": "5s", "interval": "5s", "retry": 1},
    }


def _valid_k8s_probe_absent():
    return {
        "name": "k8s-absent-probe",
        "type": "k8sProbe",
        "mode": "EOT",
        "k8sProbe/inputs": {
            "group": "",
            "version": "v1",
            "resource": "pods",
            "namespace": "default",
            "fieldSelector": "status.phase=Failed",
            "operation": "absent",
        },
        "runProperties": {"probeTimeout": "5s", "interval": "5s", "retry": 1},
    }


def _valid_prom_probe():
    return {
        "name": "prom-probe",
        "type": "promProbe",
        "mode": "Continuous",
        "promProbe/inputs": {
            "endpoint": "http://prometheus:9090",
            "query": 'sum(rate(container_cpu_usage_seconds_total[1m]))',
            "comparator": {"type": "float", "criteria": "<=", "value": "0.8"},
        },
        "runProperties": {"probeTimeout": "5s", "interval": "10s", "retry": 1},
    }


class TestProbeValidation:
    """Tests for resilience probe validation across all four types."""

    # ── Valid probes pass ──────────────────────────────────

    def test_valid_http_get_probe(self):
        scenario = _make_engine_scenario([_valid_http_probe_get()])
        assert validate_scenario(scenario) is True

    def test_valid_http_post_probe(self):
        scenario = _make_engine_scenario([_valid_http_probe_post()])
        assert validate_scenario(scenario) is True

    def test_valid_cmd_probe_inline(self):
        scenario = _make_engine_scenario([_valid_cmd_probe_inline()])
        assert validate_scenario(scenario) is True

    def test_valid_cmd_probe_source(self):
        scenario = _make_engine_scenario([_valid_cmd_probe_source()])
        assert validate_scenario(scenario) is True

    def test_valid_k8s_probe_present(self):
        scenario = _make_engine_scenario([_valid_k8s_probe_present()])
        assert validate_scenario(scenario) is True

    def test_valid_k8s_probe_absent(self):
        scenario = _make_engine_scenario([_valid_k8s_probe_absent()])
        assert validate_scenario(scenario) is True

    def test_valid_prom_probe(self):
        scenario = _make_engine_scenario([_valid_prom_probe()])
        assert validate_scenario(scenario) is True

    def test_all_probes_together(self):
        probes = [
            _valid_http_probe_get(),
            _valid_http_probe_post(),
            _valid_cmd_probe_inline(),
            _valid_cmd_probe_source(),
            _valid_k8s_probe_present(),
            _valid_k8s_probe_absent(),
            _valid_prom_probe(),
        ]
        scenario = _make_engine_scenario(probes)
        assert validate_scenario(scenario) is True

    def test_no_probes_is_valid(self):
        """Experiments without probes are valid."""
        scenario = _make_engine_scenario([])
        assert validate_scenario(scenario) is True

    # ── Basic probe errors ────────────────────────────────

    def test_probe_missing_name(self):
        probe = _valid_http_probe_get()
        del probe["name"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="missing 'name'"):
            validate_scenario(scenario)

    def test_probe_missing_type(self):
        probe = _valid_http_probe_get()
        del probe["type"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="missing 'type'"):
            validate_scenario(scenario)

    def test_probe_invalid_type(self):
        probe = _valid_http_probe_get()
        probe["type"] = "invalidProbe"
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="invalid probe type"):
            validate_scenario(scenario)

    def test_probe_missing_mode(self):
        probe = _valid_http_probe_get()
        del probe["mode"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="missing 'mode'"):
            validate_scenario(scenario)

    def test_probe_invalid_mode(self):
        probe = _valid_http_probe_get()
        probe["mode"] = "InvalidMode"
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="invalid probe mode"):
            validate_scenario(scenario)

    def test_probe_missing_run_properties(self):
        probe = _valid_http_probe_get()
        del probe["runProperties"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="runProperties"):
            validate_scenario(scenario)

    def test_run_properties_missing_timeout(self):
        probe = _valid_http_probe_get()
        del probe["runProperties"]["probeTimeout"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="probeTimeout"):
            validate_scenario(scenario)

    def test_run_properties_missing_interval(self):
        probe = _valid_http_probe_get()
        del probe["runProperties"]["interval"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="interval"):
            validate_scenario(scenario)

    def test_run_properties_missing_retry(self):
        probe = _valid_http_probe_get()
        del probe["runProperties"]["retry"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="retry"):
            validate_scenario(scenario)

    # ── All five modes accepted ───────────────────────────

    @pytest.mark.parametrize("mode", ["SOT", "EOT", "Edge", "Continuous", "OnChaos"])
    def test_all_valid_modes(self, mode):
        probe = _valid_http_probe_get()
        probe["mode"] = mode
        scenario = _make_engine_scenario([probe])
        assert validate_scenario(scenario) is True

    # ── httpProbe validation ──────────────────────────────

    def test_http_probe_missing_inputs(self):
        probe = _valid_http_probe_get()
        del probe["httpProbe/inputs"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="httpProbe/inputs is required"):
            validate_scenario(scenario)

    def test_http_probe_missing_url(self):
        probe = _valid_http_probe_get()
        del probe["httpProbe/inputs"]["url"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="url is required"):
            validate_scenario(scenario)

    def test_http_probe_missing_method(self):
        probe = _valid_http_probe_get()
        del probe["httpProbe/inputs"]["method"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="method is required"):
            validate_scenario(scenario)

    def test_http_probe_invalid_get_criteria(self):
        probe = _valid_http_probe_get()
        probe["httpProbe/inputs"]["method"]["get"]["criteria"] = "invalid"
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="invalid get criteria"):
            validate_scenario(scenario)

    def test_http_probe_get_missing_response_code(self):
        probe = _valid_http_probe_get()
        del probe["httpProbe/inputs"]["method"]["get"]["responseCode"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="responseCode is required"):
            validate_scenario(scenario)

    def test_http_post_missing_content_type(self):
        probe = _valid_http_probe_post()
        del probe["httpProbe/inputs"]["method"]["post"]["contentType"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="contentType is required"):
            validate_scenario(scenario)

    def test_http_post_missing_body(self):
        probe = _valid_http_probe_post()
        del probe["httpProbe/inputs"]["method"]["post"]["body"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="requires 'body' or 'bodyPath'"):
            validate_scenario(scenario)

    def test_http_post_body_and_bodypath_exclusive(self):
        probe = _valid_http_probe_post()
        probe["httpProbe/inputs"]["method"]["post"]["bodyPath"] = "/tmp/body.json"
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="mutually exclusive"):
            validate_scenario(scenario)

    def test_http_probe_no_get_or_post(self):
        probe = _valid_http_probe_get()
        probe["httpProbe/inputs"]["method"] = {"put": {}}
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="must contain 'get' or 'post'"):
            validate_scenario(scenario)

    # ── cmdProbe validation ───────────────────────────────

    def test_cmd_probe_missing_inputs(self):
        probe = _valid_cmd_probe_inline()
        del probe["cmdProbe/inputs"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="cmdProbe/inputs is required"):
            validate_scenario(scenario)

    def test_cmd_probe_missing_command(self):
        probe = _valid_cmd_probe_inline()
        del probe["cmdProbe/inputs"]["command"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="command is required"):
            validate_scenario(scenario)

    def test_cmd_probe_missing_comparator(self):
        probe = _valid_cmd_probe_inline()
        del probe["cmdProbe/inputs"]["comparator"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="comparator is required"):
            validate_scenario(scenario)

    def test_cmd_probe_invalid_comparator_type(self):
        probe = _valid_cmd_probe_inline()
        probe["cmdProbe/inputs"]["comparator"]["type"] = "boolean"
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="comparator.type must be"):
            validate_scenario(scenario)

    def test_cmd_probe_invalid_int_criteria(self):
        probe = _valid_cmd_probe_inline()
        probe["cmdProbe/inputs"]["comparator"]["criteria"] = "contains"
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="invalid comparator criteria"):
            validate_scenario(scenario)

    def test_cmd_probe_invalid_string_criteria(self):
        probe = _valid_cmd_probe_source()
        probe["cmdProbe/inputs"]["comparator"]["criteria"] = ">="
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="invalid comparator criteria"):
            validate_scenario(scenario)

    def test_cmd_probe_source_missing_image(self):
        probe = _valid_cmd_probe_source()
        del probe["cmdProbe/inputs"]["source"]["image"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="source.image is required"):
            validate_scenario(scenario)

    # ── k8sProbe validation ───────────────────────────────

    def test_k8s_probe_missing_inputs(self):
        probe = _valid_k8s_probe_present()
        del probe["k8sProbe/inputs"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="k8sProbe/inputs is required"):
            validate_scenario(scenario)

    def test_k8s_probe_missing_version(self):
        probe = _valid_k8s_probe_present()
        del probe["k8sProbe/inputs"]["version"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="version is required"):
            validate_scenario(scenario)

    def test_k8s_probe_missing_resource(self):
        probe = _valid_k8s_probe_present()
        del probe["k8sProbe/inputs"]["resource"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="resource is required"):
            validate_scenario(scenario)

    def test_k8s_probe_missing_namespace(self):
        probe = _valid_k8s_probe_present()
        del probe["k8sProbe/inputs"]["namespace"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="namespace is required"):
            validate_scenario(scenario)

    def test_k8s_probe_missing_operation(self):
        probe = _valid_k8s_probe_present()
        del probe["k8sProbe/inputs"]["operation"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="operation is required"):
            validate_scenario(scenario)

    def test_k8s_probe_invalid_operation(self):
        probe = _valid_k8s_probe_present()
        probe["k8sProbe/inputs"]["operation"] = "update"
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="invalid k8sProbe operation"):
            validate_scenario(scenario)

    def test_k8s_probe_create_requires_data(self):
        probe = _valid_k8s_probe_present()
        probe["k8sProbe/inputs"]["operation"] = "create"
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="'data' field is required"):
            validate_scenario(scenario)

    def test_k8s_probe_empty_group_is_valid(self):
        """Core API resources have an empty group string."""
        probe = _valid_k8s_probe_absent()
        assert probe["k8sProbe/inputs"]["group"] == ""
        scenario = _make_engine_scenario([probe])
        assert validate_scenario(scenario) is True

    # ── promProbe validation ──────────────────────────────

    def test_prom_probe_missing_inputs(self):
        probe = _valid_prom_probe()
        del probe["promProbe/inputs"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="promProbe/inputs is required"):
            validate_scenario(scenario)

    def test_prom_probe_missing_endpoint(self):
        probe = _valid_prom_probe()
        del probe["promProbe/inputs"]["endpoint"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="endpoint is required"):
            validate_scenario(scenario)

    def test_prom_probe_missing_query(self):
        probe = _valid_prom_probe()
        del probe["promProbe/inputs"]["query"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="requires 'query' or 'queryPath'"):
            validate_scenario(scenario)

    def test_prom_probe_query_and_querypath_exclusive(self):
        probe = _valid_prom_probe()
        probe["promProbe/inputs"]["queryPath"] = "/tmp/query.promql"
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="mutually exclusive"):
            validate_scenario(scenario)

    def test_prom_probe_querypath_instead_of_query(self):
        probe = _valid_prom_probe()
        del probe["promProbe/inputs"]["query"]
        probe["promProbe/inputs"]["queryPath"] = "/tmp/query.promql"
        scenario = _make_engine_scenario([probe])
        assert validate_scenario(scenario) is True

    def test_prom_probe_missing_comparator(self):
        probe = _valid_prom_probe()
        del probe["promProbe/inputs"]["comparator"]
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="comparator is required"):
            validate_scenario(scenario)

    def test_prom_probe_invalid_float_criteria(self):
        probe = _valid_prom_probe()
        probe["promProbe/inputs"]["comparator"]["criteria"] = "contains"
        scenario = _make_engine_scenario([probe])
        with pytest.raises(ValidationError, match="invalid comparator criteria"):
            validate_scenario(scenario)

    # ── Scenario with probes on existing fixtures ─────────

    def test_sample_scenario_with_http_probe_passes(self, sample_scenario):
        """Adding a valid httpProbe to the sample scenario still passes."""
        sample_scenario["experiments"][0]["spec"]["spec"]["experiments"][0].setdefault("spec", {})["probe"] = [
            _valid_http_probe_get()
        ]
        assert validate_scenario(sample_scenario) is True

    def test_sample_scenario_with_all_probe_types(self, sample_scenario):
        """Adding all probe types to the sample scenario passes."""
        sample_scenario["experiments"][0]["spec"]["spec"]["experiments"][0].setdefault("spec", {})["probe"] = [
            _valid_http_probe_get(),
            _valid_cmd_probe_inline(),
            _valid_k8s_probe_present(),
            _valid_prom_probe(),
        ]
        assert validate_scenario(sample_scenario) is True
