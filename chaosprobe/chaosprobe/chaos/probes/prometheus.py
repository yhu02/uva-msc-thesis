"""Prometheus probe generator for LitmusChaos experiments."""

from typing import Any, Dict, Optional


class PromProbeGenerator:
    """Generates Prometheus probe configurations for LitmusChaos."""

    @staticmethod
    def generate(
        name: str,
        endpoint: str,
        query: str,
        comparator_type: str = "float",
        criteria: str = ">=",
        expected_value: str = "0",
        mode: str = "Edge",
        timeout: str = "10s",
        interval: str = "5s",
        retry: int = 3,
    ) -> Dict[str, Any]:
        """Generate a Prometheus probe configuration.

        Args:
            name: Probe name.
            endpoint: Prometheus server endpoint.
            query: PromQL query.
            comparator_type: Type of comparison (int, float, string).
            criteria: Comparison criteria (==, !=, <, >, <=, >=).
            expected_value: Expected value to compare against.
            mode: Probe mode (SOT, EOT, Edge, Continuous, OnChaos).
            timeout: Probe timeout.
            interval: Probe interval.
            retry: Number of retries.

        Returns:
            Probe configuration dictionary.
        """
        return {
            "name": name,
            "type": "promProbe",
            "mode": mode,
            "runProperties": {
                "probeTimeout": timeout,
                "interval": interval,
                "retry": retry,
                "probePollingInterval": "1s",
            },
            "promProbe": {
                "endpoint": endpoint,
                "query": query,
                "comparator": {
                    "type": comparator_type,
                    "criteria": criteria,
                    "value": expected_value,
                },
            },
        }

    @staticmethod
    def generate_container_cpu_check(
        name: str,
        prometheus_endpoint: str,
        namespace: str,
        pod_name: str,
        max_cpu_percent: float = 80.0,
        mode: str = "Continuous",
    ) -> Dict[str, Any]:
        """Generate a probe to check container CPU usage.

        Args:
            name: Probe name.
            prometheus_endpoint: Prometheus server endpoint.
            namespace: Target namespace.
            pod_name: Pod name (supports regex).
            max_cpu_percent: Maximum acceptable CPU percentage.
            mode: Probe mode.

        Returns:
            Probe configuration dictionary.
        """
        query = (
            f'sum(rate(container_cpu_usage_seconds_total{{'
            f'namespace="{namespace}",pod=~"{pod_name}.*"'
            f'}}[1m])) by (pod) * 100'
        )

        return PromProbeGenerator.generate(
            name=name,
            endpoint=prometheus_endpoint,
            query=query,
            comparator_type="float",
            criteria="<=",
            expected_value=str(max_cpu_percent),
            mode=mode,
        )

    @staticmethod
    def generate_container_memory_check(
        name: str,
        prometheus_endpoint: str,
        namespace: str,
        pod_name: str,
        max_memory_bytes: int,
        mode: str = "Continuous",
    ) -> Dict[str, Any]:
        """Generate a probe to check container memory usage.

        Args:
            name: Probe name.
            prometheus_endpoint: Prometheus server endpoint.
            namespace: Target namespace.
            pod_name: Pod name (supports regex).
            max_memory_bytes: Maximum acceptable memory in bytes.
            mode: Probe mode.

        Returns:
            Probe configuration dictionary.
        """
        query = (
            f'sum(container_memory_usage_bytes{{'
            f'namespace="{namespace}",pod=~"{pod_name}.*"'
            f'}}) by (pod)'
        )

        return PromProbeGenerator.generate(
            name=name,
            endpoint=prometheus_endpoint,
            query=query,
            comparator_type="float",
            criteria="<=",
            expected_value=str(max_memory_bytes),
            mode=mode,
        )

    @staticmethod
    def generate_http_request_rate_check(
        name: str,
        prometheus_endpoint: str,
        namespace: str,
        service_name: str,
        min_request_rate: float = 0,
        mode: str = "Continuous",
    ) -> Dict[str, Any]:
        """Generate a probe to check HTTP request rate.

        Args:
            name: Probe name.
            prometheus_endpoint: Prometheus server endpoint.
            namespace: Target namespace.
            service_name: Service name.
            min_request_rate: Minimum expected request rate (req/s).
            mode: Probe mode.

        Returns:
            Probe configuration dictionary.
        """
        query = (
            f'sum(rate(http_requests_total{{'
            f'namespace="{namespace}",service="{service_name}"'
            f'}}[1m]))'
        )

        return PromProbeGenerator.generate(
            name=name,
            endpoint=prometheus_endpoint,
            query=query,
            comparator_type="float",
            criteria=">=",
            expected_value=str(min_request_rate),
            mode=mode,
        )

    @staticmethod
    def generate_error_rate_check(
        name: str,
        prometheus_endpoint: str,
        namespace: str,
        service_name: str,
        max_error_rate: float = 0.01,
        mode: str = "Continuous",
    ) -> Dict[str, Any]:
        """Generate a probe to check HTTP error rate.

        Args:
            name: Probe name.
            prometheus_endpoint: Prometheus server endpoint.
            namespace: Target namespace.
            service_name: Service name.
            max_error_rate: Maximum acceptable error rate (0-1).
            mode: Probe mode.

        Returns:
            Probe configuration dictionary.
        """
        query = (
            f'sum(rate(http_requests_total{{'
            f'namespace="{namespace}",service="{service_name}",status=~"5.."'
            f'}}[1m])) / '
            f'sum(rate(http_requests_total{{'
            f'namespace="{namespace}",service="{service_name}"'
            f'}}[1m]))'
        )

        return PromProbeGenerator.generate(
            name=name,
            endpoint=prometheus_endpoint,
            query=query,
            comparator_type="float",
            criteria="<=",
            expected_value=str(max_error_rate),
            mode=mode,
        )

    @staticmethod
    def generate_pod_restart_count_check(
        name: str,
        prometheus_endpoint: str,
        namespace: str,
        pod_name: str,
        max_restarts: int = 0,
        mode: str = "Continuous",
    ) -> Dict[str, Any]:
        """Generate a probe to check pod restart count.

        Args:
            name: Probe name.
            prometheus_endpoint: Prometheus server endpoint.
            namespace: Target namespace.
            pod_name: Pod name (supports regex).
            max_restarts: Maximum acceptable restart count.
            mode: Probe mode.

        Returns:
            Probe configuration dictionary.
        """
        query = (
            f'sum(kube_pod_container_status_restarts_total{{'
            f'namespace="{namespace}",pod=~"{pod_name}.*"'
            f'}})'
        )

        return PromProbeGenerator.generate(
            name=name,
            endpoint=prometheus_endpoint,
            query=query,
            comparator_type="int",
            criteria="<=",
            expected_value=str(max_restarts),
            mode=mode,
        )
