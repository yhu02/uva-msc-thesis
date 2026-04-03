"""Tests for anomaly label generation."""

from pathlib import Path

from chaosprobe.config.topology import parse_topology_from_directory
from chaosprobe.metrics.anomaly_labels import generate_anomaly_labels

# Discover routes once from the actual deploy manifests
_DEPLOY_DIR = str(Path(__file__).parent.parent / "scenarios" / "online-boutique" / "deploy")
_SERVICE_ROUTES = parse_topology_from_directory(_DEPLOY_DIR)


def _make_scenario(exp_name="pod-delete", target="productcatalogservice",
                   env_vars=None):
    """Build a minimal scenario dict for testing."""
    envs = env_vars or [
        {"name": "TOTAL_CHAOS_DURATION", "value": "120"},
        {"name": "CHAOS_INTERVAL", "value": "10"},
        {"name": "PODS_AFFECTED_PERC", "value": "100"},
    ]
    return {
        "path": "/tmp/test",
        "namespace": "online-boutique",
        "manifests": [],
        "experiments": [
            {
                "file": "experiment.yaml",
                "spec": {
                    "spec": {
                        "appinfo": {
                            "appns": "online-boutique",
                            "applabel": f"app={target}",
                            "appkind": "deployment",
                        },
                        "experiments": [
                            {
                                "name": exp_name,
                                "spec": {"components": {"env": envs}},
                            }
                        ],
                    }
                },
            }
        ],
    }


class TestGenerateAnomalyLabels:
    def test_basic_pod_delete(self):
        scenario = _make_scenario("pod-delete", "productcatalogservice")
        labels = generate_anomaly_labels(scenario)

        assert len(labels) == 1
        lbl = labels[0]
        assert lbl["faultType"] == "pod-delete"
        assert lbl["category"] == "availability"
        assert lbl["resource"] == "pod"
        assert lbl["severity"] == "critical"
        assert lbl["targetService"] == "productcatalogservice"
        assert lbl["targetNamespace"] == "online-boutique"
        assert lbl["parameters"]["duration_s"] == 120
        assert lbl["parameters"]["interval_s"] == 10
        assert lbl["parameters"]["podsAffectedPercent"] == 100

    def test_affected_services_for_productcatalog(self):
        scenario = _make_scenario("pod-delete", "productcatalogservice")
        labels = generate_anomaly_labels(scenario, service_routes=_SERVICE_ROUTES)

        affected = labels[0]["affectedServices"]
        # frontend, checkoutservice, recommendationservice all depend on productcatalog
        assert "frontend" in affected
        assert "checkoutservice" in affected
        assert "recommendationservice" in affected

    def test_affected_services_empty_without_routes(self):
        scenario = _make_scenario("pod-delete", "productcatalogservice")
        labels = generate_anomaly_labels(scenario)
        assert labels[0]["affectedServices"] == []

    def test_cpu_hog_parameters(self):
        scenario = _make_scenario("pod-cpu-hog", "currencyservice", env_vars=[
            {"name": "TOTAL_CHAOS_DURATION", "value": "60"},
            {"name": "CPU_CORES", "value": "1"},
            {"name": "CPU_LOAD", "value": "100"},
            {"name": "PODS_AFFECTED_PERC", "value": "100"},
        ])
        labels = generate_anomaly_labels(scenario)

        lbl = labels[0]
        assert lbl["faultType"] == "pod-cpu-hog"
        assert lbl["category"] == "saturation"
        assert lbl["resource"] == "cpu"
        assert lbl["parameters"]["cpuCores"] == 1
        assert lbl["parameters"]["cpuLoad"] == 100

    def test_memory_hog_parameters(self):
        scenario = _make_scenario("pod-memory-hog", "recommendationservice", env_vars=[
            {"name": "TOTAL_CHAOS_DURATION", "value": "60"},
            {"name": "MEMORY_CONSUMPTION", "value": "300"},
            {"name": "PODS_AFFECTED_PERC", "value": "100"},
        ])
        labels = generate_anomaly_labels(scenario)

        lbl = labels[0]
        assert lbl["faultType"] == "pod-memory-hog"
        assert lbl["parameters"]["memoryConsumption_mb"] == 300

    def test_network_loss_parameters(self):
        scenario = _make_scenario("pod-network-loss", "checkoutservice", env_vars=[
            {"name": "TOTAL_CHAOS_DURATION", "value": "60"},
            {"name": "NETWORK_PACKET_LOSS_PERCENTAGE", "value": "60"},
            {"name": "PODS_AFFECTED_PERC", "value": "100"},
        ])
        labels = generate_anomaly_labels(scenario)

        lbl = labels[0]
        assert lbl["parameters"]["packetLossPercent"] == 60

    def test_time_window_from_metrics(self):
        scenario = _make_scenario()
        metrics = {
            "timeWindow": {
                "start": "2026-04-02T01:35:00+00:00",
                "end": "2026-04-02T01:37:00+00:00",
            }
        }
        labels = generate_anomaly_labels(scenario, metrics=metrics)

        assert labels[0]["startTime"] == "2026-04-02T01:35:00+00:00"
        assert labels[0]["endTime"] == "2026-04-02T01:37:00+00:00"

    def test_explicit_times_override_metrics(self):
        scenario = _make_scenario()
        metrics = {
            "timeWindow": {
                "start": "2026-04-02T01:35:00+00:00",
                "end": "2026-04-02T01:37:00+00:00",
            }
        }
        labels = generate_anomaly_labels(
            scenario, metrics=metrics,
            experiment_start="2026-04-02T01:34:00+00:00",
            experiment_end="2026-04-02T01:38:00+00:00",
        )

        assert labels[0]["startTime"] == "2026-04-02T01:34:00+00:00"
        assert labels[0]["endTime"] == "2026-04-02T01:38:00+00:00"

    def test_placement_provides_target_node(self):
        scenario = _make_scenario("pod-delete", "productcatalogservice")
        placement = {
            "strategy": "colocate",
            "assignments": {"productcatalogservice": "worker1"},
        }
        labels = generate_anomaly_labels(scenario, placement=placement)

        assert labels[0]["targetNode"] == "worker1"

    def test_unknown_experiment_type(self):
        scenario = _make_scenario("custom-fault", "frontend")
        labels = generate_anomaly_labels(scenario)

        lbl = labels[0]
        assert lbl["faultType"] == "custom-fault"
        assert lbl["category"] == "unknown"

    def test_empty_scenario(self):
        scenario = {"experiments": [], "namespace": "default"}
        labels = generate_anomaly_labels(scenario)
        assert labels == []
