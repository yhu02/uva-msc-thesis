"""Scenario loader for ChaosProbe.

A scenario is a directory containing:
- One or more standard Kubernetes manifest files (Deployment, Service, etc.)
- One or more native LitmusChaos ChaosEngine YAML files

Files are auto-classified by their ``kind`` field.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

# Kinds that are treated as ChaosEngine experiment definitions
CHAOS_KINDS = {"ChaosEngine"}

# Filename for cluster configuration
CLUSTER_CONFIG_FILE = "cluster.yaml"


def load_scenario(scenario_path: str) -> Dict[str, Any]:
    """Load a scenario from a directory or single ChaosEngine file.

    Args:
        scenario_path: Path to a scenario directory or single YAML file.

    Returns:
        Scenario dictionary with keys:
            - path: Absolute path to the scenario directory
            - manifests: List of {file, spec} for K8s manifests
            - experiments: List of {file, spec} for ChaosEngine CRDs
            - namespace: Detected or default namespace

    Raises:
        FileNotFoundError: If the path does not exist.
        ValueError: If no ChaosEngine is found or YAML is invalid.
    """
    path = Path(scenario_path)

    if not path.exists():
        raise FileNotFoundError(f"Scenario path not found: {scenario_path}")

    if path.is_file():
        manifests, experiments = _load_yaml_file(path)
        scenario_dir = str(path.parent.resolve())
    elif path.is_dir():
        manifests, experiments = _load_yaml_directory(path)
        scenario_dir = str(path.resolve())
    else:
        raise ValueError(f"Invalid scenario path: {scenario_path}")

    if not experiments:
        raise ValueError(
            f"No ChaosEngine found in {scenario_path}. "
            "A scenario must contain at least one ChaosEngine YAML."
        )

    # Detect namespace from the first ChaosEngine's appinfo or metadata
    namespace = _detect_namespace(experiments)

    # Load cluster configuration if present
    cluster = _load_cluster_config(Path(scenario_dir))

    # Detect Rust cmdProbe sources
    rust_probes = _detect_rust_probes(Path(scenario_dir))

    result = {
        "path": scenario_dir,
        "manifests": manifests,
        "experiments": experiments,
        "namespace": namespace,
    }

    if cluster:
        result["cluster"] = cluster

    if rust_probes:
        result["probes"] = rust_probes

    return result


def _load_yaml_file(filepath: Path) -> Tuple[List[Dict], List[Dict]]:
    """Load and classify YAML documents from a single file."""
    manifests: List[Dict] = []
    experiments: List[Dict] = []

    text = filepath.read_text()
    for doc in yaml.safe_load_all(text):
        if doc is None:
            continue
        entry = {"file": str(filepath.resolve()), "spec": doc}
        if doc.get("kind") in CHAOS_KINDS:
            experiments.append(entry)
        else:
            manifests.append(entry)

    return manifests, experiments


def _load_yaml_directory(dirpath: Path) -> Tuple[List[Dict], List[Dict]]:
    """Load and classify all YAML files in a directory."""
    all_manifests: List[Dict] = []
    all_experiments: List[Dict] = []

    yaml_files = sorted(dirpath.glob("*.yaml")) + sorted(dirpath.glob("*.yml"))
    yaml_files = [f for f in yaml_files if f.name != CLUSTER_CONFIG_FILE]
    if not yaml_files:
        raise ValueError(f"No YAML files found in {dirpath}")

    for filepath in yaml_files:
        m, e = _load_yaml_file(filepath)
        all_manifests.extend(m)
        all_experiments.extend(e)

    return all_manifests, all_experiments


def _detect_namespace(experiments: List[Dict]) -> str:
    """Detect the target namespace from ChaosEngine specs.

    Falls back to 'default' if not specified.
    """
    for exp in experiments:
        spec = exp["spec"]
        # Check metadata.namespace
        ns = spec.get("metadata", {}).get("namespace")
        if ns:
            return ns
        # Check spec.appinfo.appns
        ns = spec.get("spec", {}).get("appinfo", {}).get("appns")
        if ns:
            return ns
    return "default"


def _load_cluster_config(scenario_dir: Path) -> Optional[Dict[str, Any]]:
    """Load cluster configuration from cluster.yaml in the scenario directory.

    Returns:
        Cluster configuration dict or None if not present.
    """
    cluster_file = scenario_dir / CLUSTER_CONFIG_FILE
    if not cluster_file.exists():
        return None

    text = cluster_file.read_text()
    data = yaml.safe_load(text)
    if not data or not isinstance(data, dict):
        return None

    return data.get("cluster", data)


def _detect_rust_probes(scenario_dir: Path) -> List[Dict[str, Any]]:
    """Detect Rust cmdProbe sources in a ``probes/`` subdirectory.

    Returns:
        List of probe descriptors with name, path, and kind
        (``single_file`` or ``cargo``).  Empty list if no probes found.
    """
    probes_dir = scenario_dir / "probes"
    if not probes_dir.is_dir():
        return []

    found: List[Dict[str, Any]] = []

    # Single .rs files
    for rs_file in sorted(probes_dir.glob("*.rs")):
        found.append(
            {
                "name": rs_file.stem,
                "path": str(rs_file.resolve()),
                "kind": "single_file",
            }
        )

    # Cargo project directories
    for child in sorted(probes_dir.iterdir()):
        if child.is_dir() and (child / "Cargo.toml").exists():
            found.append(
                {
                    "name": child.name,
                    "path": str(child.resolve()),
                    "kind": "cargo",
                }
            )

    return found


def merge_configs(*configs: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge multiple configuration dictionaries.

    Later configs override earlier ones for conflicting keys.
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
