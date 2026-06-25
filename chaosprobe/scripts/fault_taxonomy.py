#!/usr/bin/env python3
"""Single source of truth for classifying a scenario's fault into a fault class.

The thesis analysis scripts (``score_variance``, ``mechanism_metrics``,
``h3_mechanism_outcome``, ``distribution_charts``) restrict their H1/H2/H3
computations to *churn* runs — i.e. ``pod-delete`` only. They used to each carry
a private copy of this helper:

    def _is_churn(fault_name: str) -> bool:
        return "cpuhog" not in fault_name and fault_name != "pod-cpu-hog"

That definition is wrong two ways:

1. It does not exclude ``node-cpu-hog``. The substring guard checks for the
   un-hyphenated literal ``"cpuhog"``, but the actual scenario name is
   ``node-cpu-hog`` — so a directory scan that picks up a ``node-cpu-hog``
   summary silently folds it into the churn set.
2. It treats *every* non-cpu-hog fault as churn. ``node-memory-hog``,
   ``pod-network-loss``, ``pod-io-stress`` and friends all pass the guard, so
   the upcoming node-memory-hog contention campaign would be misclassified as
   churn and contaminate the very analyses the scripts exist to compute.

Every script's docstring says "churn (pod-delete) runs only", so the correct,
narrow definition is: a run is churn **iff** its fault is ``pod-delete``. This
module centralises that decision (and the broader taxonomy) so the four scripts
share one tested implementation.
"""

from __future__ import annotations

# Fault-class labels returned by ``fault_class``.
CHURN = "churn"
CPU_CONTENTION = "cpu-contention"
MEMORY_CONTENTION = "memory-contention"
NETWORK = "network"
IO = "io"
OTHER = "other"

# Substrings (matched against the normalised name) that map a fault to a class.
# CPU/memory are checked before the generic "hog" never appears alone, and the
# network/io substrings cover the Litmus experiment names installed by
# ``chaosprobe/chaosprobe/provisioner/setup.py`` (pod-network-{loss,latency,
# corruption,duplication}, pod-io-stress).
_NETWORK_MARKERS = (
    "network-latency",
    "network-loss",
    "network-corruption",
    "network-duplication",
)
_IO_MARKERS = ("io-stress", "disk")


def normalize_fault_name(name: str) -> str:
    """Lower-case, hyphenate underscores, and strip — so ``POD_DELETE `` and
    ``pod-delete`` compare equal."""
    return name.lower().replace("_", "-").strip()


def fault_class(name: str) -> str:
    """Classify a fault/scenario name into one fault class.

    ``pod-delete`` is the only churn fault; everything resource-pressure-shaped
    is a contention class (cpu/memory/network/io); anything unrecognised is
    ``other``. ``pod-delete`` is matched first so it can never be mistaken for a
    contention fault.
    """
    n = normalize_fault_name(name)
    if n == "pod-delete":
        return CHURN
    if "cpu-hog" in n:
        return CPU_CONTENTION
    if "memory-hog" in n:
        return MEMORY_CONTENTION
    if any(marker in n for marker in _NETWORK_MARKERS):
        return NETWORK
    if any(marker in n for marker in _IO_MARKERS):
        return IO
    return OTHER


def is_churn(name: str) -> bool:
    """True only for the churn fault (``pod-delete``).

    This is the guard the analysis scripts use to keep ``node-cpu-hog``,
    ``node-memory-hog`` and other contention faults out of the churn-only
    H1/H2/H3 statistics.
    """
    return fault_class(name) == CHURN
