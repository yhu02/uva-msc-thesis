"""Lightweight statistical helpers for resilience score analysis.

Implements bootstrap confidence intervals and the Mann-Whitney U test
without taking a SciPy dependency.  All functions operate on plain
Python sequences of floats and return JSON-serialisable dictionaries.

The motivating critical-review finding (see references.md Tier 1.2 and
Dean & Barroso, *The Tail at Scale*, CACM 2013) is that n=3 iterations
with stddev around 25-30 leave the differences between mid-tier
placement strategies statistically inseparable.  These helpers make the
uncertainty explicit instead of hiding it behind point estimates.
"""

import math
import random
import statistics
from typing import Dict, List, Optional, Sequence


def _percentile(sorted_values: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile from a sorted sequence."""
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    k = (len(sorted_values) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_values[int(k)])
    return float(sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f))


def bootstrap_ci(
    values: Sequence[float],
    statistic: str = "mean",
    confidence: float = 0.95,
    n_resamples: int = 2000,
    seed: Optional[int] = 42,
) -> Dict[str, float]:
    """Compute a bootstrap confidence interval for a statistic.

    Args:
        values: Observed sample.
        statistic: ``"mean"``, ``"median"``, ``"min"``, or ``"p25"``.
        confidence: Two-sided confidence level (default 0.95).
        n_resamples: Number of bootstrap resamples.
        seed: RNG seed for reproducibility (None = nondeterministic).

    Returns:
        Dict with ``point``, ``ci_low``, ``ci_high``, ``confidence``,
        ``n``, and ``n_resamples``.  All NaNs collapse to ``None`` so the
        result is safely JSON-serialisable.
    """
    n = len(values)
    if n == 0:
        return {
            "point": None,
            "ci_low": None,
            "ci_high": None,
            "confidence": confidence,
            "n": 0,
            "n_resamples": 0,
            "statistic": statistic,
        }

    def _stat(xs: Sequence[float]) -> float:
        if statistic == "mean":
            return statistics.mean(xs)
        if statistic == "median":
            return statistics.median(xs)
        if statistic == "min":
            return min(xs)
        if statistic == "p25":
            return _percentile(sorted(xs), 0.25)
        raise ValueError(f"unknown statistic: {statistic}")

    point = _stat(values)

    if n == 1:
        return {
            "point": round(point, 2),
            "ci_low": round(point, 2),
            "ci_high": round(point, 2),
            "confidence": confidence,
            "n": 1,
            "n_resamples": 0,
            "statistic": statistic,
        }

    rng = random.Random(seed)
    resamples: List[float] = []
    for _ in range(n_resamples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        resamples.append(_stat(sample))
    resamples.sort()

    alpha = (1.0 - confidence) / 2.0
    lo = _percentile(resamples, alpha)
    hi = _percentile(resamples, 1.0 - alpha)

    return {
        "point": round(point, 2),
        "ci_low": round(lo, 2),
        "ci_high": round(hi, 2),
        "confidence": confidence,
        "n": n,
        "n_resamples": n_resamples,
        "statistic": statistic,
    }


def _rank_with_ties(combined: Sequence[float]) -> List[float]:
    """Average-rank assignment for the Mann-Whitney rank-sum statistic.

    Ties get the average of the ranks they span (standard Mann-Whitney
    convention).
    """
    indexed = sorted(enumerate(combined), key=lambda iv: iv[1])
    ranks = [0.0] * len(combined)
    i = 0
    n = len(combined)
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        # ranks i..j (inclusive) all get the average rank
        avg_rank = (i + j) / 2.0 + 1.0  # +1 because ranks are 1-indexed
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def _standard_normal_sf(z: float) -> float:
    """Survival function (1 - CDF) of the standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2))


def mann_whitney_u(
    a: Sequence[float],
    b: Sequence[float],
) -> Dict[str, float]:
    """Two-sided Mann-Whitney U test with normal-approximation p-value.

    Returns ``u_statistic``, ``z``, ``p_two_sided``, ``n_a``, ``n_b``.
    Both samples must have at least one observation; otherwise the result
    is degenerate (p=1.0).  For small samples (n < 8 either side) the
    normal approximation is unreliable — the same is true of SciPy's
    fallback, so callers should interpret with caution at very small n.
    """
    n_a = len(a)
    n_b = len(b)
    if n_a == 0 or n_b == 0:
        return {
            "u_statistic": None,
            "z": None,
            "p_two_sided": 1.0,
            "n_a": n_a,
            "n_b": n_b,
            "method": "mann_whitney_u",
        }

    combined = list(a) + list(b)
    ranks = _rank_with_ties(combined)
    rank_sum_a = sum(ranks[:n_a])
    u_a = rank_sum_a - n_a * (n_a + 1) / 2.0
    u_b = n_a * n_b - u_a
    u = min(u_a, u_b)

    mean_u = n_a * n_b / 2.0
    # Tie correction
    ties_correction = 0.0
    rank_counts: Dict[float, int] = {}
    for r in ranks:
        rank_counts[r] = rank_counts.get(r, 0) + 1
    n = n_a + n_b
    for cnt in rank_counts.values():
        if cnt > 1:
            ties_correction += (cnt ** 3 - cnt) / (n * (n - 1))
    var_u = n_a * n_b / 12.0 * ((n + 1) - ties_correction)

    if var_u <= 0:
        z = 0.0
        p = 1.0
    else:
        # Continuity correction
        z = (u - mean_u + 0.5 * (1 if u > mean_u else -1)) / math.sqrt(var_u)
        p = 2.0 * _standard_normal_sf(abs(z))

    return {
        "u_statistic": round(u, 2),
        "z": round(z, 3),
        "p_two_sided": round(p, 4),
        "n_a": n_a,
        "n_b": n_b,
        "method": "mann_whitney_u",
    }


def pairwise_comparisons(
    samples_by_label: Dict[str, Sequence[float]],
    holm_bonferroni: bool = True,
) -> List[Dict[str, object]]:
    """Run pairwise Mann-Whitney U across every label pair.

    Args:
        samples_by_label: ``{strategy_name: [scores...]}``.
        holm_bonferroni: If True, also report Holm-Bonferroni adjusted
            p-values so the family-wise error rate is controlled when
            making many pairwise comparisons (recommended for an
            8-strategy x 1-fault matrix = 28 comparisons).

    Returns:
        List of dicts, one per (label_a, label_b) pair, sorted by raw p.
        Each dict has keys: ``a``, ``b``, ``mean_a``, ``mean_b``,
        ``u_statistic``, ``z``, ``p_raw``, ``p_holm`` (if requested),
        ``significant_05`` (bool, against the adjusted p when available).
    """
    labels = list(samples_by_label.keys())
    out: List[Dict[str, object]] = []
    for i, la in enumerate(labels):
        for lb in labels[i + 1:]:
            sa = list(samples_by_label[la])
            sb = list(samples_by_label[lb])
            t = mann_whitney_u(sa, sb)
            row: Dict[str, object] = {
                "a": la,
                "b": lb,
                "mean_a": round(statistics.mean(sa), 2) if sa else None,
                "mean_b": round(statistics.mean(sb), 2) if sb else None,
                "u_statistic": t["u_statistic"],
                "z": t["z"],
                "p_raw": t["p_two_sided"],
            }
            out.append(row)

    if holm_bonferroni:
        # Holm-Bonferroni step-down correction
        sorted_rows = sorted(out, key=lambda r: r["p_raw"])
        m = len(sorted_rows)
        prev_adj = 0.0
        for i, row in enumerate(sorted_rows):
            adj = min(1.0, (m - i) * float(row["p_raw"]))
            adj = max(adj, prev_adj)  # enforce monotonicity
            prev_adj = adj
            row["p_holm"] = round(adj, 4)
            row["significant_05"] = adj < 0.05
        out = sorted(out, key=lambda r: r["p_holm"])  # type: ignore[arg-type]
    else:
        for row in out:
            row["significant_05"] = float(row["p_raw"]) < 0.05
        out = sorted(out, key=lambda r: r["p_raw"])  # type: ignore[arg-type]

    return out
