"""Shared Kubernetes client helpers.

Provides a single ``ensure_k8s_config()`` function that loads the
kubeconfig exactly once per process, eliminating the duplicated
try/except init blocks spread across 13+ modules.
"""

from kubernetes import config

_configured = False


def ensure_k8s_config() -> None:
    """Load Kubernetes configuration (in-cluster or kubeconfig).

    Safe to call multiple times — the actual load happens only once.
    """
    global _configured
    if _configured:
        return
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    _configured = True
