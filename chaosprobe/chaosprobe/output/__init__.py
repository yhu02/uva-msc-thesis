"""Output generation for ChaosProbe results."""

# Single source of truth for the output JSON schema version.
# Both ``generator.OutputGenerator`` (per-run output) and
# ``comparison.compare_runs`` (baseline-vs-fix output) emit this value,
# so bumping it here propagates to both consumers and avoids the
# previous drift where each file hardcoded its own copy.
SCHEMA_VERSION = "2.0.0"

from chaosprobe.output.comparison import compare_runs  # noqa: E402
from chaosprobe.output.generator import OutputGenerator  # noqa: E402

__all__ = ["SCHEMA_VERSION", "OutputGenerator", "compare_runs"]
