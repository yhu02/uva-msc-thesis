"""Pod placement chaos for ChaosProbe.

Manipulates Kubernetes pod scheduling to create deterministic contention
patterns for studying the effects of pod co-location on IO and execution.
"""

from chaosprobe.placement.strategy import PlacementStrategy, NodeAssignment
from chaosprobe.placement.mutator import PlacementMutator

__all__ = ["PlacementStrategy", "NodeAssignment", "PlacementMutator"]
