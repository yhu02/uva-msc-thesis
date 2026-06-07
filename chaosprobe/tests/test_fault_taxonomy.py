"""Tests for scripts/fault_taxonomy.py — the shared fault-class taxonomy.

Guards the regression that motivated the module: ``node-cpu-hog`` (and every
other contention fault) must NOT be classified as churn. The old per-script
helper matched the un-hyphenated literal ``"cpuhog"`` and so leaked
``node-cpu-hog`` into the churn set, and treated every non-cpu-hog fault as
churn.
"""

import importlib.util
from pathlib import Path

import pytest

# scripts/ is not a package; load the module by path (mirrors test_archive_run.py).
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "fault_taxonomy.py"
_spec = importlib.util.spec_from_file_location("fault_taxonomy", _SCRIPT)
assert _spec is not None and _spec.loader is not None
fault_taxonomy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fault_taxonomy)


@pytest.mark.parametrize(
    "name, expected_class, expected_churn",
    [
        # The only churn fault.
        ("pod-delete", "churn", True),
        # CPU contention — node-cpu-hog is THE regression the old helper missed.
        ("pod-cpu-hog", "cpu-contention", False),
        ("node-cpu-hog", "cpu-contention", False),
        # Memory contention — the upcoming core contention campaign.
        ("pod-memory-hog", "memory-contention", False),
        ("node-memory-hog", "memory-contention", False),
        # Network faults.
        ("pod-network-loss", "network", False),
        ("pod-network-latency", "network", False),
        ("pod-network-corruption", "network", False),
        ("pod-network-duplication", "network", False),
        # IO faults.
        ("pod-io-stress", "io", False),
        ("disk-fill", "io", False),
        # Anything unrecognised.
        ("container-kill", "other", False),
        ("node-drain", "other", False),
    ],
)
def test_fault_class_and_is_churn(name: str, expected_class: str, expected_churn: bool) -> None:
    assert fault_taxonomy.fault_class(name) == expected_class
    assert fault_taxonomy.is_churn(name) is expected_churn


@pytest.mark.parametrize(
    "raw, normalized",
    [
        ("pod-delete", "pod-delete"),
        ("POD_DELETE ", "pod-delete"),
        ("  Pod_Cpu_Hog", "pod-cpu-hog"),
        ("NODE-MEMORY-HOG", "node-memory-hog"),
    ],
)
def test_normalize_fault_name(raw: str, normalized: str) -> None:
    assert fault_taxonomy.normalize_fault_name(raw) == normalized


def test_normalized_names_classify_like_canonical() -> None:
    """A messy underscored/upper-cased name resolves to the same class/churn verdict."""
    assert fault_taxonomy.is_churn("POD_DELETE ") is True
    assert fault_taxonomy.fault_class("Node_Cpu_Hog") == "cpu-contention"
    assert fault_taxonomy.is_churn("Node_Cpu_Hog") is False
