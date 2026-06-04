"""Shared Kubernetes client helpers.

Provides a single ``ensure_k8s_config()`` function that loads the
kubeconfig exactly once per process, eliminating the duplicated
try/except init blocks spread across 13+ modules.

It also enforces a fail-closed *context safety gate*: ChaosProbe injects
chaos into — and mutates — whatever the active kubeconfig context points at,
so a stale ``KUBECONFIG`` or wrong current-context could aim destructive
operations at a production/corporate cluster.  Before loading a kubeconfig we
refuse contexts whose name matches a denylist, and (when
``CHAOSPROBE_EXPECTED_CONTEXT`` is set) require an exact match.
"""

import os
from typing import Optional

from kubernetes import config

_configured = False

# Active-context name substrings that almost certainly mean a non-thesis
# cluster (e.g. a corporate Azure AKS kubeconfig with ``aie-*`` namespaces).
# Matching is case-insensitive.
_DENY_CONTEXT_SUBSTRINGS = ("aks", "aie")

# Env var to pin the exact allowed context name (strongest guard).
EXPECTED_CONTEXT_ENV = "CHAOSPROBE_EXPECTED_CONTEXT"
# Escape hatch for power users who know what they're doing.
ALLOW_ANY_CONTEXT_ENV = "CHAOSPROBE_ALLOW_ANY_CONTEXT"


class UnsafeKubeContextError(RuntimeError):
    """Raised when the active kube context fails the chaos safety gate."""

    def __init__(self, context_name: str, reason: str) -> None:
        self.context_name = context_name
        super().__init__(
            f"Refusing to use Kubernetes context {context_name!r}: {reason}.\n"
            "ChaosProbe injects chaos into and mutates the active context — verify "
            "it is your thesis cluster, not a production/corporate cluster.\n"
            f"Pin the allowed context with {EXPECTED_CONTEXT_ENV}=<name>, or set "
            f"{ALLOW_ANY_CONTEXT_ENV}=1 to bypass this check."
        )


def _active_context_name() -> Optional[str]:
    """Return the active kubeconfig context name, or ``None`` if undeterminable."""
    try:
        _contexts, active = config.list_kube_config_contexts()
    except config.ConfigException:
        # No usable kubeconfig to inspect; let load_kube_config() surface the
        # real error rather than masking it as a safety failure.
        return None
    if isinstance(active, dict):
        name = active.get("name")
        if isinstance(name, str):
            return name
    return None


def assert_safe_context() -> None:
    """Fail closed unless the active kube context is a safe chaos target.

    Skipped entirely when ``CHAOSPROBE_ALLOW_ANY_CONTEXT=1``.  When
    ``CHAOSPROBE_EXPECTED_CONTEXT`` is set the active context must match it
    exactly; otherwise the context name is rejected when it matches the
    denylist of production-cluster substrings.
    """
    if os.environ.get(ALLOW_ANY_CONTEXT_ENV) == "1":
        return
    name = _active_context_name()
    if name is None:
        return
    expected = os.environ.get(EXPECTED_CONTEXT_ENV)
    if expected:
        if name != expected:
            raise UnsafeKubeContextError(
                name, reason=f"does not match {EXPECTED_CONTEXT_ENV}={expected!r}"
            )
        return
    lowered = name.lower()
    for bad in _DENY_CONTEXT_SUBSTRINGS:
        if bad in lowered:
            raise UnsafeKubeContextError(name, reason=f"name contains denied substring {bad!r}")


def ensure_k8s_config() -> None:
    """Load Kubernetes configuration (in-cluster or kubeconfig).

    Safe to call multiple times — the actual load happens only once.  The
    context safety gate runs only on the kubeconfig path (in-cluster config has
    no context to mis-target and is inherently the cluster we're running in).
    """
    global _configured
    if _configured:
        return
    try:
        config.load_incluster_config()
    except config.ConfigException:
        assert_safe_context()
        config.load_kube_config()
    _configured = True
