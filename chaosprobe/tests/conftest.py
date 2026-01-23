"""Pytest configuration and fixtures."""

import pytest


@pytest.fixture
def sample_scenario():
    """Fixture providing a sample scenario configuration."""
    return {
        "apiVersion": "chaosprobe.io/v1alpha1",
        "kind": "ChaosScenario",
        "metadata": {
            "name": "sample-scenario",
            "description": "A sample scenario for testing",
        },
        "spec": {
            "infrastructure": {
                "namespace": "test-namespace",
                "resources": [
                    {
                        "name": "nginx-deployment",
                        "type": "deployment",
                        "spec": {
                            "replicas": 3,
                            "image": "nginx:1.21",
                            "labels": {"app": "nginx"},
                            "ports": [{"containerPort": 80}],
                        },
                        "anomaly": {
                            "enabled": True,
                            "type": "missing-readiness-probe",
                            "description": "Missing readiness probe",
                        },
                    },
                    {
                        "name": "nginx-service",
                        "type": "service",
                        "spec": {
                            "selector": {"app": "nginx"},
                            "ports": [{"port": 80, "targetPort": 80}],
                        },
                    },
                ],
            },
            "experiments": [
                {
                    "name": "pod-delete-test",
                    "type": "pod-delete",
                    "target": {
                        "appLabel": "app=nginx",
                        "appKind": "deployment",
                        "namespace": "test-namespace",
                    },
                    "parameters": {
                        "TOTAL_CHAOS_DURATION": "30",
                        "CHAOS_INTERVAL": "10",
                    },
                    "probes": [
                        {
                            "name": "http-probe",
                            "type": "httpProbe",
                            "mode": "Continuous",
                            "httpProbe": {
                                "url": "http://nginx-service:80",
                                "method": {
                                    "get": {
                                        "criteria": "==",
                                        "responseCode": "200",
                                    }
                                },
                            },
                            "runProperties": {
                                "probeTimeout": "5s",
                                "interval": "2s",
                                "retry": 3,
                            },
                        }
                    ],
                }
            ],
            "successCriteria": {
                "minResilienceScore": 80,
                "requireAllPass": True,
                "experimentCriteria": [
                    {
                        "experimentName": "pod-delete-test",
                        "minProbeSuccessPercentage": 90,
                        "expectedVerdict": "Pass",
                    }
                ],
            },
            "comparison": {
                "enabled": True,
                "improvementCriteria": {
                    "resilienceScoreIncrease": 10,
                    "probeSuccessIncrease": 15,
                },
            },
            "output": {
                "format": "json",
                "includeRawResults": True,
            },
        },
    }


@pytest.fixture
def sample_results():
    """Fixture providing sample experiment results."""
    return [
        {
            "name": "pod-delete-test",
            "type": "pod-delete",
            "engineName": "chaosprobe-pod-delete-test",
            "target": {
                "appLabel": "app=nginx",
                "appKind": "deployment",
                "namespace": "test-namespace",
            },
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
    """Fixture providing sample failed experiment results."""
    return [
        {
            "name": "pod-delete-test",
            "type": "pod-delete",
            "engineName": "chaosprobe-pod-delete-test",
            "target": {
                "appLabel": "app=nginx",
                "appKind": "deployment",
                "namespace": "test-namespace",
            },
            "verdict": "Fail",
            "probeSuccessPercentage": 65.0,
            "chaosResult": {
                "phase": "Completed",
                "verdict": "Fail",
                "probeSuccessPercentage": 65.0,
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
