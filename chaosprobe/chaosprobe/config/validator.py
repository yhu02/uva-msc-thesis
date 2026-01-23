"""Schema validation for ChaosProbe scenarios."""

from typing import Any, Dict, List

import jsonschema

# JSON Schema for scenario configuration
SCENARIO_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["apiVersion", "kind", "metadata", "spec"],
    "properties": {
        "apiVersion": {
            "type": "string",
            "pattern": "^chaosprobe\\.io/v\\d+.*$"
        },
        "kind": {
            "type": "string",
            "enum": ["ChaosScenario"]
        },
        "metadata": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "description": {"type": "string"}
            }
        },
        "spec": {
            "type": "object",
            "required": ["infrastructure", "experiments"],
            "properties": {
                "infrastructure": {
                    "type": "object",
                    "required": ["namespace", "resources"],
                    "properties": {
                        "namespace": {"type": "string", "minLength": 1},
                        "resources": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/resource"}
                        }
                    }
                },
                "experiments": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/experiment"},
                    "minItems": 1
                },
                "successCriteria": {"$ref": "#/$defs/successCriteria"},
                "comparison": {"$ref": "#/$defs/comparison"},
                "output": {"$ref": "#/$defs/outputConfig"}
            }
        }
    },
    "$defs": {
        "resource": {
            "type": "object",
            "required": ["name", "type"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "type": {
                    "type": "string",
                    "enum": ["deployment", "service", "configmap", "secret", "pdb", "networkpolicy", "pvc", "hpa", "ingress"]
                },
                "spec": {"type": "object"},
                "anomaly": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "type": {"type": "string"},
                        "description": {"type": "string"}
                    }
                }
            }
        },
        "experiment": {
            "type": "object",
            "required": ["name", "type"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "type": {
                    "type": "string",
                    "enum": [
                        "pod-delete", "container-kill", "pod-cpu-hog", "pod-memory-hog", "pod-io-stress",
                        "pod-network-loss", "pod-network-latency", "pod-network-corruption", "pod-network-duplication",
                        "pod-dns-error", "pod-dns-spoof",
                        "node-cpu-hog", "node-memory-hog", "node-io-stress", "node-drain", "node-taint",
                        "disk-fill", "disk-loss",
                        "kubelet-service-kill", "docker-service-kill"
                    ]
                },
                "target": {
                    "type": "object",
                    "properties": {
                        "appLabel": {"type": "string"},
                        "appKind": {"type": "string"},
                        "namespace": {"type": "string"},
                        "nodeName": {"type": "string"}
                    }
                },
                "parameters": {"type": "object"},
                "probes": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/probe"}
                }
            }
        },
        "probe": {
            "type": "object",
            "required": ["name", "type", "mode"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "type": {
                    "type": "string",
                    "enum": ["httpProbe", "cmdProbe", "k8sProbe", "promProbe"]
                },
                "mode": {
                    "type": "string",
                    "enum": ["SOT", "EOT", "Edge", "Continuous", "OnChaos"]
                },
                "httpProbe": {"type": "object"},
                "cmdProbe": {"type": "object"},
                "k8sProbe": {"type": "object"},
                "promProbe": {"type": "object"},
                "runProperties": {
                    "type": "object",
                    "properties": {
                        "probeTimeout": {"type": "string"},
                        "interval": {"type": "string"},
                        "retry": {"type": "integer"},
                        "probePollingInterval": {"type": "string"}
                    }
                }
            }
        },
        "successCriteria": {
            "type": "object",
            "properties": {
                "minResilienceScore": {"type": "number", "minimum": 0, "maximum": 100},
                "requireAllPass": {"type": "boolean"},
                "experimentCriteria": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "experimentName": {"type": "string"},
                            "minProbeSuccessPercentage": {"type": "number"},
                            "expectedVerdict": {"type": "string"}
                        }
                    }
                },
                "customChecks": {"type": "array"}
            }
        },
        "comparison": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "baseline": {"type": "object"},
                "verification": {"type": "object"},
                "improvementCriteria": {
                    "type": "object",
                    "properties": {
                        "resilienceScoreIncrease": {"type": "number"},
                        "probeSuccessIncrease": {"type": "number"}
                    }
                }
            }
        },
        "outputConfig": {
            "type": "object",
            "properties": {
                "format": {"type": "string", "enum": ["json", "yaml"]},
                "includeRawResults": {"type": "boolean"},
                "includeLogs": {"type": "boolean"},
                "timestampFormat": {"type": "string"}
            }
        }
    }
}


class ValidationError(Exception):
    """Exception raised when scenario validation fails."""

    def __init__(self, message: str, errors: List[str] = None):
        super().__init__(message)
        self.errors = errors or []


def validate_scenario(scenario: Dict[str, Any]) -> bool:
    """Validate a scenario configuration against the schema.

    Args:
        scenario: The scenario configuration dictionary.

    Returns:
        True if validation passes.

    Raises:
        ValidationError: If validation fails.
    """
    try:
        jsonschema.validate(instance=scenario, schema=SCENARIO_SCHEMA)
    except jsonschema.ValidationError as e:
        raise ValidationError(f"Schema validation failed: {e.message}", [str(e)])

    # Additional semantic validation
    errors = _semantic_validation(scenario)
    if errors:
        raise ValidationError("Semantic validation failed", errors)

    return True


def _semantic_validation(scenario: Dict[str, Any]) -> List[str]:
    """Perform semantic validation beyond schema validation.

    Args:
        scenario: The scenario configuration dictionary.

    Returns:
        List of validation error messages.
    """
    errors = []

    spec = scenario.get("spec", {})
    resources = spec.get("infrastructure", {}).get("resources", [])
    experiments = spec.get("experiments", [])

    # Check that experiment targets reference valid resources
    resource_names = {r["name"] for r in resources}
    resource_labels = set()
    for r in resources:
        if r.get("spec", {}).get("selector", {}).get("matchLabels"):
            for k, v in r["spec"]["selector"]["matchLabels"].items():
                resource_labels.add(f"{k}={v}")

    # Check experiment criteria reference valid experiments
    success_criteria = spec.get("successCriteria", {})
    experiment_criteria = success_criteria.get("experimentCriteria", [])
    experiment_names = {e["name"] for e in experiments}

    for criteria in experiment_criteria:
        if criteria.get("experimentName") not in experiment_names:
            errors.append(
                f"Experiment criteria references unknown experiment: {criteria.get('experimentName')}"
            )

    # Check probes have required configuration
    for exp in experiments:
        for probe in exp.get("probes", []):
            probe_type = probe.get("type")
            if probe_type and probe_type not in probe:
                errors.append(
                    f"Probe '{probe.get('name')}' in experiment '{exp['name']}' "
                    f"is missing configuration for type '{probe_type}'"
                )

    return errors
