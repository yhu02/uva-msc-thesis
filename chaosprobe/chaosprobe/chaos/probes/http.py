"""HTTP probe generator for LitmusChaos experiments."""

from typing import Any, Dict, Optional


class HttpProbeGenerator:
    """Generates HTTP probe configurations for LitmusChaos."""

    @staticmethod
    def generate(
        name: str,
        url: str,
        method: str = "GET",
        expected_code: int = 200,
        mode: str = "Continuous",
        timeout: str = "5s",
        interval: str = "2s",
        retry: int = 3,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[str] = None,
        criteria: str = "==",
    ) -> Dict[str, Any]:
        """Generate an HTTP probe configuration.

        Args:
            name: Probe name.
            url: URL to probe.
            method: HTTP method (GET, POST, etc.).
            expected_code: Expected HTTP response code.
            mode: Probe mode (SOT, EOT, Edge, Continuous, OnChaos).
            timeout: Probe timeout.
            interval: Probe interval.
            retry: Number of retries.
            headers: Optional HTTP headers.
            body: Optional request body for POST/PUT.
            criteria: Comparison criteria (==, !=, <, >, etc.).

        Returns:
            Probe configuration dictionary.
        """
        probe = {
            "name": name,
            "type": "httpProbe",
            "mode": mode,
            "runProperties": {
                "probeTimeout": timeout,
                "interval": interval,
                "retry": retry,
                "probePollingInterval": "1s",
            },
            "httpProbe": {
                "url": url,
                "method": {
                    method.lower(): {
                        "criteria": criteria,
                        "responseCode": str(expected_code),
                    }
                },
            },
        }

        if headers:
            probe["httpProbe"]["method"][method.lower()]["headers"] = headers

        if body and method.upper() in ["POST", "PUT", "PATCH"]:
            probe["httpProbe"]["method"][method.lower()]["body"] = body

        return probe

    @staticmethod
    def generate_health_check(
        name: str,
        service_name: str,
        namespace: str,
        port: int = 80,
        path: str = "/",
        mode: str = "Continuous",
    ) -> Dict[str, Any]:
        """Generate a health check probe for a Kubernetes service.

        Args:
            name: Probe name.
            service_name: Name of the Kubernetes service.
            namespace: Namespace of the service.
            port: Service port.
            path: Health check path.
            mode: Probe mode.

        Returns:
            Probe configuration dictionary.
        """
        url = f"http://{service_name}.{namespace}.svc.cluster.local:{port}{path}"
        return HttpProbeGenerator.generate(
            name=name,
            url=url,
            method="GET",
            expected_code=200,
            mode=mode,
        )

    @staticmethod
    def generate_response_time_check(
        name: str,
        url: str,
        max_response_time_ms: int = 1000,
        mode: str = "Continuous",
    ) -> Dict[str, Any]:
        """Generate a probe that checks response time.

        Note: This uses cmdProbe internally as httpProbe doesn't support
        response time validation directly.

        Args:
            name: Probe name.
            url: URL to check.
            max_response_time_ms: Maximum acceptable response time in ms.
            mode: Probe mode.

        Returns:
            Probe configuration dictionary (uses cmdProbe).
        """
        # Convert ms to seconds for curl
        max_time_s = max_response_time_ms / 1000.0

        return {
            "name": name,
            "type": "cmdProbe",
            "mode": mode,
            "runProperties": {
                "probeTimeout": "10s",
                "interval": "2s",
                "retry": 3,
                "probePollingInterval": "1s",
            },
            "cmdProbe": {
                "command": f"curl -w '%{{time_total}}' -o /dev/null -s {url}",
                "comparator": {
                    "type": "float",
                    "criteria": "<=",
                    "value": str(max_time_s),
                },
            },
        }
