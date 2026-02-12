"""ChaosEngine CRD generator for LitmusChaos experiments."""

import uuid
from typing import Any, Dict, List, Optional


# Default parameters for each experiment type
EXPERIMENT_DEFAULTS = {
    "pod-delete": {
        "TOTAL_CHAOS_DURATION": "30",
        "CHAOS_INTERVAL": "10",
        "FORCE": "false",
        "PODS_AFFECTED_PERC": "100",
    },
    "container-kill": {
        "TOTAL_CHAOS_DURATION": "30",
        "CHAOS_INTERVAL": "10",
        "TARGET_CONTAINER": "",
        "SIGNAL": "SIGKILL",
    },
    "pod-cpu-hog": {
        "TOTAL_CHAOS_DURATION": "60",
        "CPU_CORES": "1",
        "PODS_AFFECTED_PERC": "100",
    },
    "pod-memory-hog": {
        "TOTAL_CHAOS_DURATION": "60",
        "MEMORY_CONSUMPTION": "500",
        "PODS_AFFECTED_PERC": "100",
    },
    "pod-io-stress": {
        "TOTAL_CHAOS_DURATION": "60",
        "FILESYSTEM_UTILIZATION_PERCENTAGE": "10",
        "PODS_AFFECTED_PERC": "100",
    },
    "pod-network-loss": {
        "TOTAL_CHAOS_DURATION": "60",
        "NETWORK_INTERFACE": "eth0",
        "NETWORK_PACKET_LOSS_PERCENTAGE": "100",
        "CONTAINER_RUNTIME": "containerd",
    },
    "pod-network-latency": {
        "TOTAL_CHAOS_DURATION": "60",
        "NETWORK_INTERFACE": "eth0",
        "NETWORK_LATENCY": "300",
        "CONTAINER_RUNTIME": "containerd",
    },
    "pod-network-corruption": {
        "TOTAL_CHAOS_DURATION": "60",
        "NETWORK_INTERFACE": "eth0",
        "NETWORK_PACKET_CORRUPTION_PERCENTAGE": "100",
        "CONTAINER_RUNTIME": "containerd",
    },
    "pod-network-duplication": {
        "TOTAL_CHAOS_DURATION": "60",
        "NETWORK_INTERFACE": "eth0",
        "NETWORK_PACKET_DUPLICATION_PERCENTAGE": "100",
        "CONTAINER_RUNTIME": "containerd",
    },
    "pod-dns-error": {
        "TOTAL_CHAOS_DURATION": "60",
        "TARGET_HOSTNAMES": "",
        "CONTAINER_RUNTIME": "containerd",
    },
    "pod-dns-spoof": {
        "TOTAL_CHAOS_DURATION": "60",
        "SPOOF_MAP": "",
        "CONTAINER_RUNTIME": "containerd",
    },
    "node-cpu-hog": {
        "TOTAL_CHAOS_DURATION": "60",
        "NODE_CPU_CORE": "2",
    },
    "node-memory-hog": {
        "TOTAL_CHAOS_DURATION": "60",
        "MEMORY_PERCENTAGE": "50",
    },
    "node-io-stress": {
        "TOTAL_CHAOS_DURATION": "60",
        "FILESYSTEM_UTILIZATION_PERCENTAGE": "10",
    },
    "node-drain": {
        "TOTAL_CHAOS_DURATION": "60",
    },
    "node-taint": {
        "TOTAL_CHAOS_DURATION": "60",
        "TAINTS": "node.kubernetes.io/unreachable:NoSchedule",
    },
    "disk-fill": {
        "TOTAL_CHAOS_DURATION": "60",
        "FILL_PERCENTAGE": "80",
    },
    "disk-loss": {
        "TOTAL_CHAOS_DURATION": "60",
    },
    "kubelet-service-kill": {
        "TOTAL_CHAOS_DURATION": "60",
    },
    "docker-service-kill": {
        "TOTAL_CHAOS_DURATION": "60",
    },
}


class ChaosEngineGenerator:
    """Generates ChaosEngine CRDs for LitmusChaos experiments."""

    def __init__(self, namespace: str, service_account: str = "litmus-admin"):
        """Initialize the generator.

        Args:
            namespace: Target namespace for chaos experiments.
            service_account: Service account for running experiments.
        """
        self.namespace = namespace
        self.service_account = service_account
        # Unique suffix per run so engines don't collide across concurrent/consecutive runs
        self._run_suffix = uuid.uuid4().hex[:6]

    def generate(self, experiment_config: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a ChaosEngine CRD from experiment configuration.

        Args:
            experiment_config: Experiment configuration from the scenario.

        Returns:
            ChaosEngine CRD manifest.
        """
        name = experiment_config["name"]
        exp_type = experiment_config["type"]
        target = experiment_config.get("target", {})
        parameters = experiment_config.get("parameters", {})
        probes = experiment_config.get("probes", [])

        # Merge default parameters with user-provided ones
        merged_params = {**EXPERIMENT_DEFAULTS.get(exp_type, {}), **parameters}

        # Build the experiment spec
        experiment_spec = {
            "name": exp_type,
            "spec": {
                "components": {
                    "env": [{"name": k, "value": str(v)} for k, v in merged_params.items()],
                },
            },
        }

        # Add probes if configured
        if probes:
            experiment_spec["spec"]["probe"] = [
                self._generate_probe(probe) for probe in probes
            ]

        # Build the ChaosEngine
        engine = {
            "apiVersion": "litmuschaos.io/v1alpha1",
            "kind": "ChaosEngine",
            "metadata": {
                "name": f"chaosprobe-{name}-{self._run_suffix}",
                "namespace": self.namespace,
                "labels": {
                    "managed-by": "chaosprobe",
                    "experiment": name,
                },
            },
            "spec": {
                "engineState": "active",
                "chaosServiceAccount": self.service_account,
                "experiments": [experiment_spec],
            },
        }

        # Add target specification based on experiment type
        if self._is_pod_experiment(exp_type):
            engine["spec"]["appinfo"] = {
                "appns": target.get("namespace", self.namespace),
                "applabel": target.get("appLabel", ""),
                "appkind": target.get("appKind", "deployment"),
            }
            engine["spec"]["annotationCheck"] = "false"
        elif self._is_node_experiment(exp_type):
            if target.get("nodeName"):
                experiment_spec["spec"]["components"]["nodeSelector"] = {
                    "kubernetes.io/hostname": target["nodeName"]
                }

        return engine

    def _is_pod_experiment(self, exp_type: str) -> bool:
        """Check if experiment type targets pods."""
        pod_experiments = {
            "pod-delete", "container-kill", "pod-cpu-hog", "pod-memory-hog",
            "pod-io-stress", "pod-network-loss", "pod-network-latency",
            "pod-network-corruption", "pod-network-duplication",
            "pod-dns-error", "pod-dns-spoof",
        }
        return exp_type in pod_experiments

    def _is_node_experiment(self, exp_type: str) -> bool:
        """Check if experiment type targets nodes."""
        node_experiments = {
            "node-cpu-hog", "node-memory-hog", "node-io-stress",
            "node-drain", "node-taint", "disk-fill", "disk-loss",
            "kubelet-service-kill", "docker-service-kill",
        }
        return exp_type in node_experiments

    def _generate_probe(self, probe_config: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a probe specification.

        Args:
            probe_config: Probe configuration from the scenario.

        Returns:
            Probe specification for ChaosEngine.
        """
        probe = {
            "name": probe_config["name"],
            "type": probe_config["type"],
            "mode": probe_config["mode"],
        }

        # Add run properties
        run_properties = probe_config.get("runProperties", {})
        probe["runProperties"] = {
            "probeTimeout": run_properties.get("probeTimeout", "5s"),
            "interval": run_properties.get("interval", "2s"),
            "retry": run_properties.get("retry", 3),
            "probePollingInterval": run_properties.get("probePollingInterval", "1s"),
        }

        # Add type-specific configuration
        probe_type = probe_config["type"]
        if probe_type == "httpProbe" and "httpProbe" in probe_config:
            probe["httpProbe/inputs"] = probe_config["httpProbe"]
        elif probe_type == "cmdProbe" and "cmdProbe" in probe_config:
            probe["cmdProbe/inputs"] = probe_config["cmdProbe"]
        elif probe_type == "k8sProbe" and "k8sProbe" in probe_config:
            probe["k8sProbe/inputs"] = probe_config["k8sProbe"]
        elif probe_type == "promProbe" and "promProbe" in probe_config:
            probe["promProbe/inputs"] = probe_config["promProbe"]

        return probe


def generate_chaos_experiment_crd(exp_type: str, namespace: str) -> Dict[str, Any]:
    """Generate a ChaosExperiment CRD for an experiment type.

    This is typically not needed if experiments are pre-installed from ChaosHub,
    but can be useful for custom experiments.

    Args:
        exp_type: The experiment type (e.g., "pod-delete").
        namespace: Target namespace.

    Returns:
        ChaosExperiment CRD manifest.
    """
    return {
        "apiVersion": "litmuschaos.io/v1alpha1",
        "kind": "ChaosExperiment",
        "metadata": {
            "name": exp_type,
            "namespace": namespace,
        },
        "spec": {
            "definition": {
                "scope": "Namespaced",
                "permissions": [],
                "image": f"litmuschaos/go-runner:latest",
                "args": ["-c", f"./experiments/{exp_type}"],
                "command": ["/bin/bash"],
                "env": [
                    {"name": "TOTAL_CHAOS_DURATION", "value": "30"},
                    {"name": "CHAOS_INTERVAL", "value": "10"},
                ],
                "labels": {
                    "name": exp_type,
                },
            },
        },
    }
