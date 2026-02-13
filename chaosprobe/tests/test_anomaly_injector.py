"""Tests for anomaly injection."""

import pytest

from chaosprobe.provisioner.anomaly_injector import (
    AnomalyInjector,
    get_anomaly_definition,
    list_anomaly_types,
)


class TestAnomalyInjector:
    """Tests for the AnomalyInjector class."""

    def test_inject_missing_readiness_probe(self):
        """Test injecting missing-readiness-probe anomaly."""
        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "test-deployment"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "test",
                                "image": "nginx:1.21",
                                "readinessProbe": {
                                    "httpGet": {"path": "/", "port": 80}
                                },
                                "livenessProbe": {
                                    "httpGet": {"path": "/health", "port": 80}
                                },
                            }
                        ]
                    }
                }
            },
        }

        anomaly_config = {
            "enabled": True,
            "type": "missing-readiness-probe",
        }

        injector = AnomalyInjector()
        modified = injector.inject(manifest, anomaly_config)

        # Readiness probe should be removed
        container = modified["spec"]["template"]["spec"]["containers"][0]
        assert "readinessProbe" not in container
        # Liveness probe should still be present
        assert "livenessProbe" in container

    def test_inject_missing_liveness_probe(self):
        """Test injecting missing-liveness-probe anomaly."""
        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "test-deployment"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "test",
                                "image": "nginx:1.21",
                                "readinessProbe": {
                                    "httpGet": {"path": "/", "port": 80}
                                },
                                "livenessProbe": {
                                    "httpGet": {"path": "/health", "port": 80}
                                },
                            }
                        ]
                    }
                }
            },
        }

        anomaly_config = {
            "enabled": True,
            "type": "missing-liveness-probe",
        }

        injector = AnomalyInjector()
        modified = injector.inject(manifest, anomaly_config)

        container = modified["spec"]["template"]["spec"]["containers"][0]
        assert "livenessProbe" not in container
        assert "readinessProbe" in container

    def test_inject_insufficient_replicas(self):
        """Test injecting insufficient-replicas anomaly."""
        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "test-deployment"},
            "spec": {
                "replicas": 3,
                "template": {
                    "spec": {
                        "containers": [{"name": "test", "image": "nginx:1.21"}]
                    }
                },
            },
        }

        anomaly_config = {
            "enabled": True,
            "type": "insufficient-replicas",
        }

        injector = AnomalyInjector()
        modified = injector.inject(manifest, anomaly_config)

        assert modified["spec"]["replicas"] == 1

    def test_no_injection_when_no_config(self):
        """Test that no injection occurs when anomaly_config is None."""
        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "test-deployment"},
            "spec": {
                "replicas": 3,
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "test",
                                "image": "nginx:1.21",
                                "readinessProbe": {
                                    "httpGet": {"path": "/", "port": 80}
                                },
                            }
                        ]
                    }
                },
            },
        }

        injector = AnomalyInjector()
        modified = injector.inject(manifest, None)

        # Should be unchanged
        container = modified["spec"]["template"]["spec"]["containers"][0]
        assert "readinessProbe" in container

    def test_no_injection_when_anomaly_disabled(self):
        """Test that no injection occurs when anomaly.enabled=False."""
        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "test-deployment"},
            "spec": {
                "replicas": 3,
                "template": {
                    "spec": {
                        "containers": [{"name": "test", "image": "nginx:1.21"}]
                    }
                },
            },
        }

        anomaly_config = {
            "enabled": False,
            "type": "insufficient-replicas",
        }

        injector = AnomalyInjector()
        modified = injector.inject(manifest, anomaly_config)

        assert modified["spec"]["replicas"] == 3

    def test_track_injected_anomalies(self):
        """Test that injected anomalies are tracked."""
        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "test-deployment"},
            "spec": {
                "replicas": 3,
                "template": {
                    "spec": {
                        "containers": [{"name": "test", "image": "nginx:1.21"}]
                    }
                },
            },
        }

        anomaly_config = {
            "enabled": True,
            "type": "insufficient-replicas",
        }

        injector = AnomalyInjector()
        injector.inject(manifest, anomaly_config)

        anomalies = injector.get_injected_anomalies()
        assert len(anomalies) == 1
        assert anomalies[0]["type"] == "insufficient-replicas"
        assert anomalies[0]["resource"] == "test-deployment"


class TestAnomalyDefinitions:
    """Tests for anomaly definitions."""

    def test_get_anomaly_definition(self):
        """Test getting an anomaly definition."""
        definition = get_anomaly_definition("missing-readiness-probe")

        assert definition is not None
        assert "description" in definition
        assert "severity" in definition
        assert "effect" in definition

    def test_get_unknown_anomaly_definition(self):
        """Test getting an unknown anomaly definition."""
        definition = get_anomaly_definition("unknown-anomaly")
        assert definition is None

    def test_list_anomaly_types(self):
        """Test listing all anomaly types."""
        types = list_anomaly_types()

        assert len(types) > 0
        assert "missing-readiness-probe" in types
        assert "missing-liveness-probe" in types
        assert "insufficient-replicas" in types
