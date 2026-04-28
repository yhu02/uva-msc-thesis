"""Pytest configuration and fixtures for the new ChaosCenter-native format."""

import os

import pytest

# Vars loaded by `python-dotenv` when the CLI is exercised via CliRunner
# (chaosprobe.cli.main calls load_dotenv). Persisting these into os.environ
# leaks state between tests, so we strip them at session start and again
# before each test that does not opt in via the `with_env` fixture.
_LEAK_PRONE_VARS = (
    "CHAOSPROBE_REGISTRY",
    "CHAOSPROBE_REGISTRY_USER",
    "CHAOSPROBE_REGISTRY_PASSWORD",
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
    "ANSIBLE_BECOME_PASS",
)


@pytest.fixture(autouse=True)
def _isolate_dotenv_vars(monkeypatch):
    """Prevent .env values loaded by CliRunner from polluting other tests."""
    for name in _LEAK_PRONE_VARS:
        monkeypatch.delenv(name, raising=False)


def pytest_configure(config):
    # Also scrub once at session start in case a previous run or the parent
    # shell pre-populated these.
    for name in _LEAK_PRONE_VARS:
        os.environ.pop(name, None)


@pytest.fixture
def sample_scenario():
    """Scenario as returned by config.loader.load_scenario()."""
    return {
        "path": "/tmp/scenarios/nginx-pod-delete",
        "namespace": "test-namespace",
        "manifests": [
            {
                "file": "/tmp/scenarios/nginx-pod-delete/deployment.yaml",
                "spec": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {"name": "nginx", "labels": {"app": "nginx"}},
                    "spec": {
                        "replicas": 1,
                        "selector": {"matchLabels": {"app": "nginx"}},
                        "template": {
                            "metadata": {"labels": {"app": "nginx"}},
                            "spec": {
                                "containers": [
                                    {
                                        "name": "nginx",
                                        "image": "nginx:1.21",
                                        "ports": [{"containerPort": 80}],
                                    }
                                ]
                            },
                        },
                    },
                },
            },
            {
                "file": "/tmp/scenarios/nginx-pod-delete/service.yaml",
                "spec": {
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {"name": "nginx-service"},
                    "spec": {
                        "selector": {"app": "nginx"},
                        "ports": [{"port": 80, "targetPort": 80}],
                    },
                },
            },
        ],
        "experiments": [
            {
                "file": "/tmp/scenarios/nginx-pod-delete/experiment.yaml",
                "spec": {
                    "apiVersion": "litmuschaos.io/v1alpha1",
                    "kind": "ChaosEngine",
                    "metadata": {"name": "nginx-pod-delete"},
                    "spec": {
                        "engineState": "active",
                        "appinfo": {
                            "appns": "test-namespace",
                            "applabel": "app=nginx",
                            "appkind": "deployment",
                        },
                        "chaosServiceAccount": "litmus-admin",
                        "experiments": [
                            {
                                "name": "pod-delete",
                                "spec": {
                                    "components": {
                                        "env": [
                                            {
                                                "name": "TOTAL_CHAOS_DURATION",
                                                "value": "30",
                                            }
                                        ]
                                    }
                                },
                            }
                        ],
                    },
                },
            }
        ],
    }


@pytest.fixture
def sample_results():
    """Sample passing experiment results from ResultCollector with all probe types."""
    return [
        {
            "name": "pod-delete",
            "engineName": "nginx-pod-delete-abc123",
            "verdict": "Pass",
            "probeSuccessPercentage": 95.0,
            "chaosResult": {
                "phase": "Completed",
                "verdict": "Pass",
                "probeSuccessPercentage": 95.0,
                "failStep": "",
                "history": {
                    "passedRuns": 1,
                    "failedRuns": 0,
                    "totalRuns": 1,
                },
                "probes": [
                    {
                        "name": "http-probe",
                        "type": "httpProbe",
                        "mode": "Continuous",
                        "status": {"verdict": "Pass"},
                    },
                    {
                        "name": "cmd-probe",
                        "type": "cmdProbe",
                        "mode": "Edge",
                        "status": {"verdict": "Pass"},
                    },
                    {
                        "name": "k8s-probe",
                        "type": "k8sProbe",
                        "mode": "EOT",
                        "status": {"verdict": "Pass"},
                    },
                    {
                        "name": "prom-probe",
                        "type": "promProbe",
                        "mode": "Continuous",
                        "status": {"verdict": "Pass"},
                    },
                ],
            },
        }
    ]


@pytest.fixture
def failed_results():
    """Sample failing experiment results from ResultCollector."""
    return [
        {
            "name": "pod-delete",
            "engineName": "nginx-pod-delete-abc123",
            "verdict": "Fail",
            "probeSuccessPercentage": 0.0,
            "chaosResult": {
                "phase": "Completed",
                "verdict": "Fail",
                "probeSuccessPercentage": 0.0,
                "failStep": "ChaosInject",
                "history": {
                    "passedRuns": 0,
                    "failedRuns": 1,
                    "totalRuns": 1,
                },
                "probes": [
                    {
                        "name": "http-probe",
                        "type": "httpProbe",
                        "mode": "Continuous",
                        "status": {"verdict": "Fail", "description": "connection refused"},
                    }
                ],
            },
        }
    ]
