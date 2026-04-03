"""Dynamic service topology extraction from Kubernetes manifests.

Parses deployment YAML files to discover service dependencies by inspecting
environment variables that reference other services (e.g. ``*_SERVICE_ADDR``,
``*_ADDR``).  This replaces hardcoded service dependency graphs with
automatically derived ones.

Usage::

    routes = parse_topology_from_directory("/path/to/deploy/")
    # Returns list of (source, target, host, protocol, description) tuples
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

logger = logging.getLogger(__name__)

# Type alias for a service dependency route:
# (source_service, target_service, target_host, protocol, description)
ServiceRoute = Tuple[str, str, str, str, str]

# Environment variable patterns that indicate a service dependency.
# Matches: PRODUCT_CATALOG_SERVICE_ADDR, REDIS_ADDR, CART_SERVICE_ADDR, etc.
_ADDR_ENV_RE = re.compile(r"^(.+?)_(?:SERVICE_)?ADDR$", re.IGNORECASE)

# Well-known non-dependency env vars to skip
_SKIP_ENV_NAMES = frozenset({
    "PORT",
    "LISTEN_ADDR",
    "BIND_ADDR",
    "JAEGER_SERVICE_ADDR",
    "COLLECTOR_ADDR",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "DISABLE_PROFILER",
    "ENABLE_PROFILER",
    "SHOPPING_ASSISTANT_SERVICE_ADDR",
})

# Known TCP-only services (not gRPC)
_TCP_SERVICES = frozenset({"redis-cart", "redis", "memcached"})


def _infer_protocol(target_service: str, port: str) -> str:
    """Infer the protocol based on service name and port."""
    if any(target_service.startswith(p) for p in ("redis", "memcached")):
        return "tcp"
    if port == "6379":
        return "tcp"
    return "grpc"


def _env_name_to_description(env_name: str) -> str:
    """Convert an env var name like PRODUCT_CATALOG_SERVICE_ADDR to a human description."""
    name = _ADDR_ENV_RE.sub(r"\1", env_name)
    return name.replace("_", " ").title()


def _extract_dependencies_from_deployment(
    deployment: Dict[str, Any],
) -> List[ServiceRoute]:
    """Extract service dependencies from a single Deployment spec.

    Parses container environment variables to find ``*_SERVICE_ADDR`` and
    ``*_ADDR`` entries that reference ``service:port`` targets.
    """
    routes: List[ServiceRoute] = []
    metadata = deployment.get("metadata", {})
    source_name = metadata.get("name", "unknown")

    template = deployment.get("spec", {}).get("template", {})
    containers = template.get("spec", {}).get("containers", [])

    for container in containers:
        for env in container.get("env", []):
            env_name = env.get("name", "")
            env_value = env.get("value", "")

            if not env_value or env_name in _SKIP_ENV_NAMES:
                continue

            if not _ADDR_ENV_RE.match(env_name):
                continue

            # Parse "service:port" or "service" format
            if ":" in env_value:
                target_host = env_value
                target_service, port = env_value.rsplit(":", 1)
            else:
                target_host = env_value
                target_service = env_value
                port = ""

            # Skip self-references
            if target_service == source_name:
                continue

            protocol = _infer_protocol(target_service, port)
            description = _env_name_to_description(env_name)

            routes.append((
                source_name,
                target_service,
                target_host,
                protocol,
                description,
            ))

    return routes


def parse_topology_from_manifests(
    manifests: List[Dict[str, Any]],
) -> List[ServiceRoute]:
    """Extract service dependency routes from a list of Kubernetes manifest specs.

    Parameters
    ----------
    manifests
        List of parsed YAML documents (Deployment, Service, etc.).

    Returns
    -------
    Deduplicated list of ``(source, target, host, protocol, description)`` tuples.
    """
    routes: List[ServiceRoute] = []
    seen = set()

    for doc in manifests:
        if doc.get("kind") != "Deployment":
            continue
        for route in _extract_dependencies_from_deployment(doc):
            key = (route[0], route[1])  # (source, target) pair
            if key not in seen:
                seen.add(key)
                routes.append(route)

    return routes


def parse_topology_from_directory(deploy_dir: str) -> List[ServiceRoute]:
    """Parse all YAML files in a directory to extract the service topology.

    Parameters
    ----------
    deploy_dir
        Path to a directory containing Kubernetes deployment YAML files.

    Returns
    -------
    List of ``(source, target, host, protocol, description)`` tuples.
    """
    dirpath = Path(deploy_dir)
    if not dirpath.is_dir():
        logger.warning("Deploy directory does not exist: %s", deploy_dir)
        return []

    manifests = []
    for filepath in sorted(dirpath.glob("*.yaml")) + sorted(dirpath.glob("*.yml")):
        try:
            text = filepath.read_text()
            for doc in yaml.safe_load_all(text):
                if doc and isinstance(doc, dict):
                    manifests.append(doc)
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", filepath, exc)

    return parse_topology_from_manifests(manifests)


def parse_topology_from_scenario(
    scenario: Dict[str, Any],
) -> List[ServiceRoute]:
    """Extract service topology from a loaded scenario.

    Looks for a ``deploy/`` subdirectory adjacent to the scenario path,
    and parses all Kubernetes manifests within it. Also parses any manifests
    already loaded in the scenario dict.

    Parameters
    ----------
    scenario
        Loaded scenario dict from ``load_scenario()``.

    Returns
    -------
    List of ``(source, target, host, protocol, description)`` tuples.
    """
    routes: List[ServiceRoute] = []
    seen = set()

    # 1. Try deploy/ subdirectory relative to scenario path
    scenario_path = scenario.get("path", "")
    if scenario_path:
        deploy_dir = Path(scenario_path) / "deploy"
        if not deploy_dir.is_dir():
            # Try parent's deploy/ (scenario file might be in a subdirectory)
            deploy_dir = Path(scenario_path).parent / "deploy"
        if deploy_dir.is_dir():
            for route in parse_topology_from_directory(str(deploy_dir)):
                key = (route[0], route[1])
                if key not in seen:
                    seen.add(key)
                    routes.append(route)

    # 2. Also check manifests already loaded in the scenario
    for manifest_entry in scenario.get("manifests", []):
        spec = manifest_entry.get("spec", {})
        if spec.get("kind") == "Deployment":
            for route in _extract_dependencies_from_deployment(spec):
                key = (route[0], route[1])
                if key not in seen:
                    seen.add(key)
                    routes.append(route)

    return routes
