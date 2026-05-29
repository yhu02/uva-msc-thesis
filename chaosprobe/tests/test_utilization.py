"""Tests for per-pod utilization-vs-request derivation."""

from chaosprobe.metrics.utilization import (
    compute_per_pod_utilization,
    parse_cpu_quantity,
    parse_memory_quantity,
)


class TestParseCpuQuantity:
    def test_millicpu_suffix(self):
        assert parse_cpu_quantity("100m") == 0.1
        assert parse_cpu_quantity("500m") == 0.5
        assert parse_cpu_quantity("1500m") == 1.5

    def test_whole_cores(self):
        assert parse_cpu_quantity("2") == 2.0
        assert parse_cpu_quantity("1.5") == 1.5

    def test_none_and_empty(self):
        assert parse_cpu_quantity(None) is None
        assert parse_cpu_quantity("") is None
        assert parse_cpu_quantity("   ") is None

    def test_invalid(self):
        assert parse_cpu_quantity("abc") is None
        assert parse_cpu_quantity("100x") is None


class TestParseMemoryQuantity:
    def test_binary_suffixes(self):
        assert parse_memory_quantity("256Mi") == 256 * 1024**2
        assert parse_memory_quantity("1Gi") == 1024**3
        assert parse_memory_quantity("2Ki") == 2048

    def test_decimal_suffixes(self):
        assert parse_memory_quantity("500M") == 500 * 1000**2
        assert parse_memory_quantity("1G") == 1000**3
        # Lowercase k is the only allowed lowercase decimal suffix.
        assert parse_memory_quantity("4k") == 4000

    def test_mi_priority_over_m(self):
        """``"100Mi"`` must parse as 100 * 2^20, not 100 * 10^6 * something."""
        assert parse_memory_quantity("100Mi") == 100 * 1024**2

    def test_plain_bytes(self):
        assert parse_memory_quantity("512") == 512

    def test_none_and_empty(self):
        assert parse_memory_quantity(None) is None
        assert parse_memory_quantity("") is None

    def test_invalid(self):
        assert parse_memory_quantity("abcMi") is None
        assert parse_memory_quantity("xyz") is None


# ---------------------------------------------------------------------------
# compute_per_pod_utilization
# ---------------------------------------------------------------------------


def _make_pod_status(name="frontend-abc", cpu="200m", memory="256Mi"):
    spec = {"name": "main"}
    if cpu or memory:
        requests = {}
        if cpu:
            requests["cpu"] = cpu
        if memory:
            requests["memory"] = memory
        spec["requests"] = requests
    return {
        "pods": [
            {
                "name": name,
                "resourceSpecs": [spec],
            }
        ]
    }


def _make_prom_entry(phase, samples):
    metrics = {}
    for label, items in samples.items():
        metrics[label] = items
    return {"phase": phase, "metrics": metrics}


class TestComputePerPodUtilization:
    def test_pod_with_cpu_and_memory_request_and_samples(self):
        pod_status = _make_pod_status(cpu="200m", memory="256Mi")
        prometheus_data = {
            "available": True,
            "timeSeries": [
                _make_prom_entry(
                    "pre-chaos",
                    {
                        "cpu_usage": [
                            {"metric": {"pod": "frontend-abc"}, "value": [1, "0.05"]},
                        ],
                        "memory_usage": [
                            {"metric": {"pod": "frontend-abc"}, "value": [1, str(128 * 1024**2)]},
                        ],
                    },
                ),
                _make_prom_entry(
                    "during-chaos",
                    {
                        "cpu_usage": [
                            {"metric": {"pod": "frontend-abc"}, "value": [2, "0.15"]},
                            {"metric": {"pod": "frontend-abc"}, "value": [3, "0.25"]},
                        ],
                    },
                ),
            ],
        }

        out = compute_per_pod_utilization(pod_status, prometheus_data)
        pod = out["pods"]["frontend-abc"]

        assert pod["cpuRequestCores"] == 0.2
        assert pod["memoryRequestBytes"] == 256 * 1024**2

        pre = pod["phases"]["pre-chaos"]
        assert pre["cpuUsageCores"] == 0.05
        assert pre["cpuFraction"] == round(0.05 / 0.2, 4)
        assert pre["memoryUsageBytes"] == 128 * 1024**2
        assert pre["memoryFraction"] == round((128 * 1024**2) / (256 * 1024**2), 4)

        during = pod["phases"]["during-chaos"]
        assert during["cpuUsageCores"] == 0.2
        assert during["cpuFraction"] == 1.0
        # No memory samples during-chaos — keys absent.
        assert "memoryUsageBytes" not in during
        assert "memoryFraction" not in during

    def test_pod_without_requests_returns_fractions_omitted(self):
        pod_status = {"pods": [{"name": "x", "resourceSpecs": []}]}
        prometheus_data = {
            "available": True,
            "timeSeries": [
                _make_prom_entry(
                    "pre-chaos",
                    {
                        "cpu_usage": [{"metric": {"pod": "x"}, "value": [1, "0.5"]}],
                    },
                ),
            ],
        }
        out = compute_per_pod_utilization(pod_status, prometheus_data)
        pod = out["pods"]["x"]
        assert pod["cpuRequestCores"] is None
        assert pod["phases"]["pre-chaos"]["cpuUsageCores"] == 0.5
        assert "cpuFraction" not in pod["phases"]["pre-chaos"]

    def test_unavailable_prometheus_returns_empty(self):
        assert compute_per_pod_utilization(
            _make_pod_status(),
            {"available": False, "reason": "no data"},
        ) == {"pods": {}}

    def test_none_inputs_return_empty(self):
        assert compute_per_pod_utilization(None, None) == {"pods": {}}
        assert compute_per_pod_utilization(None, {"available": True}) == {"pods": {}}
        assert compute_per_pod_utilization(_make_pod_status(), None) == {"pods": {}}

    def test_multi_container_request_sums(self):
        pod_status = {
            "pods": [
                {
                    "name": "multi",
                    "resourceSpecs": [
                        {"name": "main", "requests": {"cpu": "100m", "memory": "100Mi"}},
                        {"name": "sidecar", "requests": {"cpu": "50m", "memory": "50Mi"}},
                    ],
                }
            ]
        }
        prometheus_data = {
            "available": True,
            "timeSeries": [
                _make_prom_entry(
                    "pre-chaos",
                    {
                        "cpu_usage": [{"metric": {"pod": "multi"}, "value": [1, "0.075"]}],
                    },
                ),
            ],
        }
        out = compute_per_pod_utilization(pod_status, prometheus_data)
        pod = out["pods"]["multi"]
        assert pod["cpuRequestCores"] == 0.15
        assert pod["memoryRequestBytes"] == 150 * 1024**2
        assert pod["phases"]["pre-chaos"]["cpuFraction"] == round(0.075 / 0.15, 4)

    def test_phase_with_no_samples_omitted(self):
        pod_status = _make_pod_status(cpu="100m", memory="100Mi")
        prometheus_data = {
            "available": True,
            "timeSeries": [
                _make_prom_entry("pre-chaos", {}),
            ],
        }
        out = compute_per_pod_utilization(pod_status, prometheus_data)
        pod = out["pods"]["frontend-abc"]
        assert pod["phases"] == {}

    def test_unparseable_sample_value_skipped(self):
        pod_status = _make_pod_status(cpu="100m", memory="100Mi")
        prometheus_data = {
            "available": True,
            "timeSeries": [
                _make_prom_entry(
                    "pre-chaos",
                    {
                        "cpu_usage": [
                            {"metric": {"pod": "frontend-abc"}, "value": [1, "not-a-number"]},
                            {"metric": {"pod": "frontend-abc"}, "value": [2, "0.1"]},
                        ],
                    },
                ),
            ],
        }
        out = compute_per_pod_utilization(pod_status, prometheus_data)
        pod = out["pods"]["frontend-abc"]
        # Only the valid 0.1 sample contributes.
        assert pod["phases"]["pre-chaos"]["cpuUsageCores"] == 0.1

    def test_sample_without_pod_label_skipped(self):
        pod_status = _make_pod_status()
        prometheus_data = {
            "available": True,
            "timeSeries": [
                _make_prom_entry(
                    "pre-chaos",
                    {
                        "cpu_usage": [
                            {"metric": {}, "value": [1, "0.5"]},
                        ],
                    },
                ),
            ],
        }
        out = compute_per_pod_utilization(pod_status, prometheus_data)
        pod = out["pods"]["frontend-abc"]
        assert pod["phases"] == {}

    def test_entry_without_phase_skipped(self):
        pod_status = _make_pod_status()
        prometheus_data = {
            "available": True,
            "timeSeries": [
                {
                    "metrics": {
                        "cpu_usage": [{"metric": {"pod": "frontend-abc"}, "value": [1, "0.5"]}]
                    }
                },
            ],
        }
        out = compute_per_pod_utilization(pod_status, prometheus_data)
        pod = out["pods"]["frontend-abc"]
        assert pod["phases"] == {}

    def test_malformed_value_field_skipped(self):
        pod_status = _make_pod_status()
        prometheus_data = {
            "available": True,
            "timeSeries": [
                _make_prom_entry(
                    "pre-chaos",
                    {
                        "cpu_usage": [
                            {"metric": {"pod": "frontend-abc"}, "value": None},
                            {"metric": {"pod": "frontend-abc"}, "value": [1]},
                            {"metric": {"pod": "frontend-abc"}, "value": [1, "0.2"]},
                        ],
                    },
                ),
            ],
        }
        out = compute_per_pod_utilization(pod_status, prometheus_data)
        pod = out["pods"]["frontend-abc"]
        assert pod["phases"]["pre-chaos"]["cpuUsageCores"] == 0.2
