"""Pytest configuration and fixtures for the new ChaosCenter-native format."""

import pytest


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
    """Sample passing experiment results from ResultCollector."""
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
                        "status": {"phase": "Completed", "verdict": "Pass"},
                    }
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
                        "status": {"phase": "Completed", "verdict": "Fail"},
                    }
                ],
            },
        }
    ]
