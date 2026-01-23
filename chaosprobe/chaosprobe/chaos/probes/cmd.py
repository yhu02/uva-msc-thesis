"""Command probe generator for LitmusChaos experiments."""

from typing import Any, Dict, Optional


class CmdProbeGenerator:
    """Generates command probe configurations for LitmusChaos."""

    @staticmethod
    def generate(
        name: str,
        command: str,
        comparator_type: str = "string",
        criteria: str = "contains",
        expected_value: str = "",
        mode: str = "Edge",
        timeout: str = "10s",
        interval: str = "5s",
        retry: int = 3,
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a command probe configuration.

        Args:
            name: Probe name.
            command: Command to execute.
            comparator_type: Type of comparison (string, int, float).
            criteria: Comparison criteria (contains, equal, notEqual, etc.).
            expected_value: Expected value to compare against.
            mode: Probe mode (SOT, EOT, Edge, Continuous, OnChaos).
            timeout: Probe timeout.
            interval: Probe interval.
            retry: Number of retries.
            source: Source image for running the command (optional).

        Returns:
            Probe configuration dictionary.
        """
        probe = {
            "name": name,
            "type": "cmdProbe",
            "mode": mode,
            "runProperties": {
                "probeTimeout": timeout,
                "interval": interval,
                "retry": retry,
                "probePollingInterval": "1s",
            },
            "cmdProbe": {
                "command": command,
                "comparator": {
                    "type": comparator_type,
                    "criteria": criteria,
                    "value": expected_value,
                },
            },
        }

        if source:
            probe["cmdProbe"]["source"] = source

        return probe

    @staticmethod
    def generate_exit_code_check(
        name: str,
        command: str,
        expected_exit_code: int = 0,
        mode: str = "Edge",
    ) -> Dict[str, Any]:
        """Generate a probe that checks command exit code.

        Args:
            name: Probe name.
            command: Command to execute.
            expected_exit_code: Expected exit code (default 0 for success).
            mode: Probe mode.

        Returns:
            Probe configuration dictionary.
        """
        # Wrap command to output exit code
        wrapped_command = f"{command}; echo $?"

        return CmdProbeGenerator.generate(
            name=name,
            command=wrapped_command,
            comparator_type="int",
            criteria="equal",
            expected_value=str(expected_exit_code),
            mode=mode,
        )

    @staticmethod
    def generate_output_contains(
        name: str,
        command: str,
        expected_substring: str,
        mode: str = "Edge",
    ) -> Dict[str, Any]:
        """Generate a probe that checks if command output contains a string.

        Args:
            name: Probe name.
            command: Command to execute.
            expected_substring: Substring that should be in output.
            mode: Probe mode.

        Returns:
            Probe configuration dictionary.
        """
        return CmdProbeGenerator.generate(
            name=name,
            command=command,
            comparator_type="string",
            criteria="contains",
            expected_value=expected_substring,
            mode=mode,
        )

    @staticmethod
    def generate_pod_count_check(
        name: str,
        label_selector: str,
        namespace: str,
        min_count: int = 1,
        mode: str = "Continuous",
    ) -> Dict[str, Any]:
        """Generate a probe that checks running pod count.

        Args:
            name: Probe name.
            label_selector: Kubernetes label selector (e.g., "app=nginx").
            namespace: Namespace to check.
            min_count: Minimum expected running pods.
            mode: Probe mode.

        Returns:
            Probe configuration dictionary.
        """
        command = (
            f"kubectl get pods -n {namespace} -l {label_selector} "
            f"--field-selector=status.phase=Running --no-headers | wc -l"
        )

        return CmdProbeGenerator.generate(
            name=name,
            command=command,
            comparator_type="int",
            criteria=">=",
            expected_value=str(min_count),
            mode=mode,
        )

    @staticmethod
    def generate_endpoint_count_check(
        name: str,
        service_name: str,
        namespace: str,
        min_count: int = 1,
        mode: str = "Continuous",
    ) -> Dict[str, Any]:
        """Generate a probe that checks service endpoint count.

        Args:
            name: Probe name.
            service_name: Name of the Kubernetes service.
            namespace: Namespace of the service.
            min_count: Minimum expected endpoints.
            mode: Probe mode.

        Returns:
            Probe configuration dictionary.
        """
        command = (
            f"kubectl get endpoints {service_name} -n {namespace} "
            f"-o jsonpath='{{.subsets[*].addresses | length}}'"
        )

        return CmdProbeGenerator.generate(
            name=name,
            command=command,
            comparator_type="int",
            criteria=">=",
            expected_value=str(min_count),
            mode=mode,
        )
