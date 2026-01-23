"""YAML configuration loader for ChaosProbe scenarios."""

from pathlib import Path
from typing import Any, Dict

import yaml


def load_scenario(path: str) -> Dict[str, Any]:
    """Load a scenario configuration from a YAML file.

    Args:
        path: Path to the scenario YAML file.

    Returns:
        Dictionary containing the parsed scenario configuration.

    Raises:
        FileNotFoundError: If the scenario file does not exist.
        yaml.YAMLError: If the file contains invalid YAML.
    """
    scenario_path = Path(path)

    if not scenario_path.exists():
        raise FileNotFoundError(f"Scenario file not found: {path}")

    with scenario_path.open("r") as f:
        scenario = yaml.safe_load(f)

    if scenario is None:
        raise ValueError(f"Empty scenario file: {path}")

    return scenario


def load_anomaly_definitions(path: str) -> Dict[str, Any]:
    """Load anomaly type definitions from a YAML file.

    Args:
        path: Path to the anomaly definitions YAML file.

    Returns:
        Dictionary containing anomaly definitions.
    """
    return load_scenario(path)


def merge_configs(*configs: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge multiple configuration dictionaries.

    Later configs override earlier ones for conflicting keys.

    Args:
        *configs: Configuration dictionaries to merge.

    Returns:
        Merged configuration dictionary.
    """
    result: Dict[str, Any] = {}

    for config in configs:
        result = _deep_merge(result, config)

    return result


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge two dictionaries."""
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value

    return result
