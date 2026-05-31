"""Probe-timeout and chaos-duration arithmetic.

Pure helpers used by the iteration loop to compute how long to wait for
a ChaosCenter experiment to complete.  Extracted from ``strategy_runner``
so the timing logic can be tested without dragging in the rest of the
orchestrator.
"""

from __future__ import annotations

from typing import Any, Dict


def parse_probe_timeout(s: str) -> int:
    """Parse a Go-style duration string (e.g. ``'15s'``, ``'1.5s'``) to integer seconds."""
    s = s.strip()
    if not s:
        return 5
    try:
        if s.endswith("ms"):
            return max(1, int(float(s[:-2]) // 1000))
        if s.endswith("s"):
            return max(1, int(float(s[:-1])))
        if s.endswith("m"):
            return max(1, int(float(s[:-1]) * 60))
        return max(1, int(float(s)))
    except (ValueError, OverflowError):
        return 5


def extract_chaos_duration(scenario: Dict[str, Any]) -> int:
    """Extract the total chaos duration (seconds) from the scenario."""
    chaos_duration = 60  # fallback
    for exp_entry in scenario.get("experiments", []):
        spec = exp_entry.get("spec", {})
        for exp in spec.get("spec", {}).get("experiments", []):
            for env in exp.get("spec", {}).get("components", {}).get("env", []):
                if env.get("name") == "TOTAL_CHAOS_DURATION":
                    try:
                        chaos_duration = max(chaos_duration, int(env["value"]))
                    except (ValueError, KeyError):
                        # Non-numeric / missing TOTAL_CHAOS_DURATION → keep the running max.
                        pass
    return chaos_duration


def compute_effective_timeout(scenario: Dict[str, Any], user_timeout: int) -> int:
    """Compute a polling timeout that accounts for chaos duration + probe overhead.

    The go-runner evaluates probes **before** and **after** the chaos
    window.  At PreChaos and PostChaos, probes are evaluated
    **sequentially** (not in goroutines).  When probes can't reach
    their targets, each one exhausts its full ``(retry + 1) ×
    probeTimeout`` budget (``retry`` is the count of *additional*
    retries after the initial attempt).

    Returns the larger of *user_timeout* and the computed minimum.
    """
    chaos_duration = extract_chaos_duration(scenario)
    total_probe_budget = 0

    for exp_entry in scenario.get("experiments", []):
        spec = exp_entry.get("spec", {})
        for exp in spec.get("spec", {}).get("experiments", []):
            for probe in exp.get("spec", {}).get("probe", []):
                run_props = probe.get("runProperties", {})
                t = parse_probe_timeout(run_props.get("probeTimeout", "5s"))
                try:
                    r = int(run_props.get("retry", 0))
                except (ValueError, TypeError):
                    r = 0
                total_probe_budget += t * (r + 1)

    # pre-chaos probes + chaos + post-chaos probes + workflow overhead
    min_timeout = chaos_duration + 2 * total_probe_budget + 120
    return max(user_timeout, min_timeout)
