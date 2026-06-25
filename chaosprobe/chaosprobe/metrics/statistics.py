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
from collections import defaultdict
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


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
) -> Dict[str, object]:
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
) -> Dict[str, object]:
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
            ties_correction += (cnt**3 - cnt) / (n * (n - 1))
    var_u = n_a * n_b / 12.0 * ((n + 1) - ties_correction)

    if var_u <= 0:
        z = 0.0
        p = 1.0
    else:
        # Continuity correction: shrink the deviation |U - mean_u| toward the
        # mean by 0.5 (clamped at 0) so the normal approximation stays
        # conservative.  U = min(u_a, u_b) is always <= mean_u, so subtracting
        # 0.5 from the deviation's magnitude is the standard correction and
        # matches SciPy's mannwhitneyu(use_continuity=True).  z is therefore
        # non-negative — it is the magnitude of the (corrected) deviation.
        z = max(0.0, abs(u - mean_u) - 0.5) / math.sqrt(var_u)
        p = 2.0 * _standard_normal_sf(z)

    return {
        "u_statistic": round(u, 2),
        "z": round(z, 3),
        "p_two_sided": round(p, 4),
        "n_a": n_a,
        "n_b": n_b,
        "method": "mann_whitney_u",
    }


# z values for common two-sided confidence levels.
_Z_FOR_CONFIDENCE = {0.80: 1.282, 0.90: 1.645, 0.95: 1.96, 0.99: 2.576}


def wilson_ci(
    successes: int,
    total: int,
    confidence: float = 0.95,
) -> Dict[str, Optional[float]]:
    """Wilson score interval for a Bernoulli proportion.

    The textbook normal-approximation interval (``p̂ ± z·SE``) collapses
    at boundary cases (p̂=0 or p̂=1 give zero-width intervals) and at
    small n.  Wilson stays well-defined in both regimes — the right
    default for thesis-level probe-success-rate reporting where n is in
    the single digits per probe per strategy.

    Returns ``{successes, total, point, ci_low, ci_high, confidence}``;
    ``point`` / ``ci_low`` / ``ci_high`` are ``None`` when ``total == 0``.
    """
    z = _Z_FOR_CONFIDENCE.get(confidence)
    if z is None:
        # Fall back to 95% if the caller asks for a confidence we don't
        # have a tabulated z for, rather than silently producing wrong
        # numbers.
        z = 1.96
        confidence = 0.95

    if total <= 0:
        return {
            "successes": successes,
            "total": total,
            "point": None,
            "ci_low": None,
            "ci_high": None,
            "confidence": confidence,
        }

    p_hat = successes / total
    denom = 1.0 + z * z / total
    center = (p_hat + z * z / (2.0 * total)) / denom
    half = (z * math.sqrt(p_hat * (1.0 - p_hat) / total + z * z / (4.0 * total * total))) / denom
    return {
        "successes": successes,
        "total": total,
        "point": round(p_hat, 4),
        "ci_low": round(max(0.0, center - half), 4),
        "ci_high": round(min(1.0, center + half), 4),
        "confidence": confidence,
    }


def cliffs_delta(
    a: Sequence[float],
    b: Sequence[float],
) -> Dict[str, object]:
    """Cliff's delta — non-parametric effect size for two independent samples.

    delta = (#pairs where a_i > b_j  -  #pairs where a_i < b_j)  /  (n_a * n_b)

    The Mann-Whitney p-value says *whether* the two distributions differ;
    Cliff's delta says *how much*.  Both are needed for the defence —
    "statistically significant but practically negligible" is a common
    reviewer objection.

    Returns ``{delta, magnitude, n_a, n_b}``.  ``magnitude`` uses
    Romano et al. (2006) thresholds on ``|delta|``:

    * < 0.147   → "negligible"
    * < 0.33    → "small"
    * < 0.474   → "medium"
    * otherwise → "large"

    Boundary case ``n_a == 0`` or ``n_b == 0`` returns
    ``{delta: None, magnitude: None}``.
    """
    n_a = len(a)
    n_b = len(b)
    if n_a == 0 or n_b == 0:
        return {"delta": None, "magnitude": None, "n_a": n_a, "n_b": n_b}

    greater = 0
    less = 0
    for ai in a:
        for bj in b:
            if ai > bj:
                greater += 1
            elif ai < bj:
                less += 1
    delta = (greater - less) / (n_a * n_b)

    abs_delta = abs(delta)
    if abs_delta < 0.147:
        magnitude = "negligible"
    elif abs_delta < 0.33:
        magnitude = "small"
    elif abs_delta < 0.474:
        magnitude = "medium"
    else:
        magnitude = "large"

    return {
        "delta": round(delta, 4),
        "magnitude": magnitude,
        "n_a": n_a,
        "n_b": n_b,
    }


def _as_float(value: object) -> float:
    """Narrow a heterogeneous-row value known to be numeric to ``float``.

    Pairwise rows are typed ``Dict[str, object]`` (mixed str / float / bool
    values); the p-value fields used for sorting are always numbers, so this
    asserts that at the boundary and coerces.
    """
    assert isinstance(value, (int, float)), f"expected numeric p-value, got {value!r}"
    return float(value)


def pairwise_comparisons(
    samples_by_label: Mapping[str, Sequence[float]],
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
        ``cliffs_delta``, ``effect_size_magnitude``,
        ``significant_05`` (bool, against the adjusted p when available).
    """
    labels = list(samples_by_label.keys())
    out: List[Dict[str, object]] = []
    for i, la in enumerate(labels):
        for lb in labels[i + 1 :]:
            sa = list(samples_by_label[la])
            sb = list(samples_by_label[lb])
            t = mann_whitney_u(sa, sb)
            d = cliffs_delta(sa, sb)
            row: Dict[str, object] = {
                "a": la,
                "b": lb,
                "mean_a": round(statistics.mean(sa), 2) if sa else None,
                "mean_b": round(statistics.mean(sb), 2) if sb else None,
                "u_statistic": t["u_statistic"],
                "z": t["z"],
                "p_raw": t["p_two_sided"],
                "cliffs_delta": d["delta"],
                "effect_size_magnitude": d["magnitude"],
            }
            out.append(row)

    if holm_bonferroni:
        # Holm-Bonferroni step-down correction
        sorted_rows = sorted(out, key=lambda r: _as_float(r["p_raw"]))
        m = len(sorted_rows)
        prev_adj = 0.0
        for i, row in enumerate(sorted_rows):
            adj = min(1.0, (m - i) * _as_float(row["p_raw"]))
            adj = max(adj, prev_adj)  # enforce monotonicity
            prev_adj = adj
            row["p_holm"] = round(adj, 4)
            row["significant_05"] = adj < 0.05
        out = sorted(out, key=lambda r: _as_float(r["p_holm"]))
    else:
        for row in out:
            row["significant_05"] = _as_float(row["p_raw"]) < 0.05
        out = sorted(out, key=lambda r: _as_float(r["p_raw"]))

    return out


def _round_or_none(value: float, digits: int) -> Optional[float]:
    """Round a float, collapsing NaN/inf to ``None`` for JSON-safe output."""
    if not math.isfinite(value):
        return None
    return round(value, digits)


def tost_equivalence_correlation(
    rho: float,
    n: int,
    sesoi: float = 0.3,
    alpha: float = 0.05,
) -> Dict[str, object]:
    """Two one-sided tests (TOST) for *equivalence* of a correlation to zero.

    This is the instrument the thesis's H3 "decoupling" claim needs.  An
    ordinary correlation test that fails to reject (p > 0.05) only shows
    *absence of evidence*; TOST turns it into *evidence of absence* by
    testing whether the correlation lies inside an equivalence band
    (-sesoi, +sesoi) — a smallest-effect-size-of-interest the analyst
    declares in advance (default |rho| = 0.3, a conventional "small"
    boundary).  Equivalence is concluded only when **both** one-sided
    tests reject, i.e. ``p_tost = max(p_lower, p_upper) < alpha``.

    Uses the Fisher z-transform: ``z = atanh(rho)`` with standard error
    ``1/sqrt(n - 3)``.  The bounds are transformed the same way.

    Args:
        rho: Observed correlation (Spearman or Pearson), in [-1, 1].
        n: Number of paired observations the correlation was computed on.
        sesoi: Smallest effect size of interest (equivalence half-width on
            the correlation scale); must be in (0, 1).
        alpha: One-sided significance level for each test.

    Returns:
        Dict with ``rho``, ``n``, ``sesoi``, ``alpha``, ``se``, ``z``,
        ``p_lower``, ``p_upper``, ``p_tost``, ``equivalent`` (bool), and
        ``bounds`` (the ``[-sesoi, sesoi]`` band).  When ``n <= 3`` the SE
        is undefined: the p-values are ``None`` and ``equivalent`` is
        ``False``.
    """
    bounds = [-abs(sesoi), abs(sesoi)]
    if n <= 3 or not 0.0 < abs(sesoi) < 1.0:
        return {
            "rho": rho,
            "n": n,
            "sesoi": abs(sesoi),
            "alpha": alpha,
            "se": None,
            "z": None,
            "p_lower": None,
            "p_upper": None,
            "p_tost": None,
            "equivalent": False,
            "bounds": bounds,
        }

    # Clamp away from ±1 so atanh stays finite at the boundary.
    clamped = max(-0.999999999, min(0.999999999, rho))
    z_obs = math.atanh(clamped)
    se = 1.0 / math.sqrt(n - 3)
    z_low = math.atanh(-abs(sesoi))
    z_high = math.atanh(abs(sesoi))

    # H0_lower: rho <= -sesoi.  Reject (rho is above the lower bound) when
    # (z_obs - z_low)/se is large and positive -> upper-tail probability.
    stat_lower = (z_obs - z_low) / se
    p_lower = _standard_normal_sf(stat_lower)
    # H0_upper: rho >= +sesoi.  Reject (rho is below the upper bound) when
    # (z_obs - z_high)/se is large and negative -> lower-tail probability.
    stat_upper = (z_obs - z_high) / se
    p_upper = _standard_normal_sf(-stat_upper)

    p_tost = max(p_lower, p_upper)
    return {
        "rho": round(rho, 4),
        "n": n,
        "sesoi": abs(sesoi),
        "alpha": alpha,
        "se": round(se, 4),
        "z": round(z_obs, 4),
        "p_lower": round(p_lower, 4),
        "p_upper": round(p_upper, 4),
        "p_tost": round(p_tost, 4),
        "equivalent": p_tost < alpha,
        "bounds": bounds,
    }


def sign_test(a: Sequence[float], b: Sequence[float]) -> Dict[str, object]:
    """Exact two-sided binomial sign test on paired observations.

    Counts pairs where ``a > b`` vs ``a < b`` (ties dropped) and asks how
    surprising the split is under a fair coin.  For the H2 mechanism
    claim, this is the cleanest statement of "spread flushes more
    conntrack than colocate in k/k sessions": with k/k in one direction
    the exact p is ``2 * 0.5**k``.

    Args:
        a, b: Equal-length paired samples.

    Returns:
        Dict with ``n_pos``, ``n_neg``, ``n`` (non-tied pairs), and
        ``p_two_sided``.

    Raises:
        ValueError: if the two samples differ in length.
    """
    if len(a) != len(b):
        raise ValueError("sign_test requires equal-length paired samples")
    n_pos = sum(1 for x, y in zip(a, b) if x > y)
    n_neg = sum(1 for x, y in zip(a, b) if x < y)
    n = n_pos + n_neg
    if n == 0:
        p = 1.0
    else:
        k = min(n_pos, n_neg)
        tail = sum(math.comb(n, i) for i in range(0, k + 1)) * (0.5**n)
        p = min(1.0, 2.0 * tail)
    return {
        "n_pos": n_pos,
        "n_neg": n_neg,
        "n": n,
        "p_two_sided": round(p, 4),
    }


def wilcoxon_signed_rank(a: Sequence[float], b: Sequence[float]) -> Dict[str, object]:
    """Paired Wilcoxon signed-rank test with normal-approximation p-value.

    The paired counterpart to Mann-Whitney, for session-blocked designs
    where each session yields one value per arm (e.g. spread vs colocate
    measured under the same cluster state).  Pairing removes between-
    session variance, so this is both more powerful and more honest than
    an unpaired test here.  Zero differences are dropped (standard
    Wilcoxon convention); ties in the absolute differences get average
    ranks with the usual variance correction.  A continuity correction
    keeps the approximation conservative, matching ``mann_whitney_u``.

    The exact binomial ``sign_test`` is computed alongside and returned
    under ``sign_test`` — report both.

    Args:
        a, b: Equal-length paired samples.

    Returns:
        Dict with ``w_statistic`` (= ``min(w_plus, w_minus)``),
        ``w_plus``, ``w_minus``, ``z``, ``p_two_sided``, ``n_nonzero``,
        ``n_pairs``, and a nested ``sign_test`` dict.

    Raises:
        ValueError: if the two samples differ in length.
    """
    if len(a) != len(b):
        raise ValueError("wilcoxon_signed_rank requires equal-length paired samples")
    sgn = sign_test(a, b)
    diffs = [x - y for x, y in zip(a, b)]
    nonzero = [d for d in diffs if d != 0]
    n_r = len(nonzero)
    if n_r == 0:
        return {
            "w_statistic": None,
            "w_plus": 0.0,
            "w_minus": 0.0,
            "z": 0.0,
            "p_two_sided": 1.0,
            "n_nonzero": 0,
            "n_pairs": len(a),
            "sign_test": sgn,
        }

    abs_d = [abs(d) for d in nonzero]
    ranks = _rank_with_ties(abs_d)
    w_plus = sum(r for d, r in zip(nonzero, ranks) if d > 0)
    w_minus = sum(r for d, r in zip(nonzero, ranks) if d < 0)
    w = min(w_plus, w_minus)

    mean_w = n_r * (n_r + 1) / 4.0
    tie_term = 0.0
    abs_counts: Dict[float, int] = {}
    for d in abs_d:
        abs_counts[d] = abs_counts.get(d, 0) + 1
    for cnt in abs_counts.values():
        if cnt > 1:
            tie_term += cnt**3 - cnt
    var_w = n_r * (n_r + 1) * (2 * n_r + 1) / 24.0 - tie_term / 48.0

    if var_w <= 0:  # pragma: no cover - all-tied diffs collapse to n_r==0 above, so var_w>0 here
        z = 0.0
        p = 1.0
    else:
        z = max(0.0, abs(w - mean_w) - 0.5) / math.sqrt(var_w)
        p = min(1.0, 2.0 * _standard_normal_sf(z))

    return {
        "w_statistic": round(w, 2),
        "w_plus": round(w_plus, 2),
        "w_minus": round(w_minus, 2),
        "z": round(z, 3),
        "p_two_sided": round(p, 4),
        "n_nonzero": n_r,
        "n_pairs": len(a),
        "sign_test": sgn,
    }


def _icc_point(cells: Mapping[Tuple[object, object], Sequence[float]]) -> Dict[str, float]:
    """Variance partition + ICC_strategy for a ``{(strategy, run): scores}`` map.

    Mirrors ``scripts/score_variance.py:decompose`` exactly so the CLI
    number and this helper's bootstrap reconcile: ``sig2_iter`` is the
    mean within-cell population variance, ``sig2_run`` the mean (over
    strategies) population variance of that strategy's cell means, and
    ``sig2_strat`` the population variance of the strategy grand means.
    Returns NaN components when there is no data to partition.
    """
    strategies = sorted({s for s, _ in cells}, key=repr)
    if not strategies:
        return {
            "sig2_strat": float("nan"),
            "sig2_run": float("nan"),
            "sig2_iter": float("nan"),
            "total": float("nan"),
            "icc": float("nan"),
        }

    within = [statistics.pvariance(v) for v in cells.values() if len(v) >= 2]
    sig2_iter = statistics.mean(within) if within else 0.0

    strat_means: List[float] = []
    run_vars: List[float] = []
    for strat in strategies:
        cell_means = [statistics.mean(v) for (s, _), v in cells.items() if s == strat and v]
        if not cell_means:
            continue
        strat_means.append(statistics.mean(cell_means))
        if len(cell_means) >= 2:
            run_vars.append(statistics.pvariance(cell_means))
    sig2_run = statistics.mean(run_vars) if run_vars else 0.0
    sig2_strat = statistics.pvariance(strat_means) if strat_means else float("nan")

    total = sig2_strat + sig2_run + sig2_iter
    icc = sig2_strat / total if total else float("nan")
    return {
        "sig2_strat": sig2_strat,
        "sig2_run": sig2_run,
        "sig2_iter": sig2_iter,
        "total": total,
        "icc": icc,
    }


def icc_bootstrap(
    cells: Mapping[Tuple[object, object], Sequence[float]],
    confidence: float = 0.95,
    n_resamples: int = 2000,
    seed: Optional[int] = 42,
) -> Dict[str, object]:
    """ICC_strategy with a cluster-bootstrap confidence interval.

    ``ICC_strategy`` is the share of resilience-score variance attributable
    to the placement strategy — the thesis's H1 instrument.  A point
    estimate alone invites "0.046 ± what?"; this adds a percentile CI by
    resampling the nested design at the levels that carry the dependence:
    strategies with replacement, then runs within each resampled strategy
    with replacement (iterations ride along inside their run).  Resampled
    units are relabelled so a strategy or run drawn twice counts as two
    distinct cells.

    Args:
        cells: ``{(strategy, run): [per-iteration scores]}`` — the shape
            ``score_variance.py:collect`` produces.
        confidence: Two-sided confidence level for the interval.
        n_resamples: Number of bootstrap resamples.
        seed: RNG seed for reproducibility (None = nondeterministic).

    Returns:
        Dict with ``icc`` (point estimate), ``ci_low``, ``ci_high``,
        ``confidence``, ``n_resamples``, ``n_strategies``, ``n_obs``, and
        the ``sig2_strat`` / ``sig2_run`` / ``sig2_iter`` components.
        NaN/empty results collapse to ``None``.
    """
    point = _icc_point(cells)
    n_obs = sum(len(v) for v in cells.values())

    strat_to_runs: Dict[object, List[Tuple[object, List[float]]]] = defaultdict(list)
    for (s, run), v in cells.items():
        strat_to_runs[s].append((run, list(v)))
    strategies = sorted(strat_to_runs, key=repr)

    boot: List[float] = []
    if strategies:
        rng = random.Random(seed)
        n_strat = len(strategies)
        for _ in range(n_resamples):
            resampled: Dict[Tuple[object, object], Sequence[float]] = {}
            for si in range(n_strat):
                strat = strategies[rng.randrange(n_strat)]
                runs = strat_to_runs[strat]
                synth_strat = (strat, si)
                for rj in range(len(runs)):
                    run, values = runs[rng.randrange(len(runs))]
                    resampled[(synth_strat, (run, rj))] = values
            icc = _icc_point(resampled)["icc"]
            if math.isfinite(icc):
                boot.append(icc)

    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    if boot:
        boot.sort()
        alpha = (1.0 - confidence) / 2.0
        ci_low = _round_or_none(_percentile(boot, alpha), 4)
        ci_high = _round_or_none(_percentile(boot, 1.0 - alpha), 4)

    return {
        "icc": _round_or_none(point["icc"], 4),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "confidence": confidence,
        "n_resamples": len(boot),
        "n_strategies": len(strategies),
        "n_obs": n_obs,
        "sig2_strat": _round_or_none(point["sig2_strat"], 3),
        "sig2_run": _round_or_none(point["sig2_run"], 3),
        "sig2_iter": _round_or_none(point["sig2_iter"], 3),
    }


def _betacf(a: float, b: float, x: float) -> float:
    """Continued-fraction expansion for the incomplete beta (Lentz's method)."""
    tiny = 1e-30
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny  # pragma: no cover - Lentz zero-guard, not deterministically reachable
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny  # pragma: no cover - Lentz zero-guard
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny  # pragma: no cover - Lentz zero-guard
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny  # pragma: no cover - Lentz zero-guard
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny  # pragma: no cover - Lentz zero-guard
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _f_sf(f: float, df1: float, df2: float) -> float:
    """Survival function P(F >= f) for an F distribution with (df1, df2)."""
    if f <= 0.0:
        return 1.0
    x = df2 / (df2 + df1 * f)
    return _betai(df2 / 2.0, df1 / 2.0, x)


def _two_factor_f(rows: Sequence[Tuple[object, object, float]]) -> Dict[str, Dict[str, object]]:
    """Factorial ANOVA F/df/p for both main effects and the interaction.

    Standard balanced-design sum-of-squares decomposition; for unbalanced
    cells it is the unweighted-means approximation (ART assumes a balanced
    factorial design, so callers should keep cells equal-sized for an
    exact test).
    """
    values = [r[2] for r in rows]
    n = len(values)
    a_levels = sorted({r[0] for r in rows}, key=repr)
    b_levels = sorted({r[1] for r in rows}, key=repr)
    na = len(a_levels)
    nb = len(b_levels)
    gm = statistics.mean(values)

    by_a: Dict[object, List[float]] = defaultdict(list)
    by_b: Dict[object, List[float]] = defaultdict(list)
    by_ab: Dict[Tuple[object, object], List[float]] = defaultdict(list)
    for ra, rb, rv in rows:
        by_a[ra].append(rv)
        by_b[rb].append(rv)
        by_ab[(ra, rb)].append(rv)

    ss_total = sum((v - gm) ** 2 for v in values)
    ss_a = sum(len(g) * (statistics.mean(g) - gm) ** 2 for g in by_a.values())
    ss_b = sum(len(g) * (statistics.mean(g) - gm) ** 2 for g in by_b.values())
    ss_cells = sum(len(g) * (statistics.mean(g) - gm) ** 2 for g in by_ab.values())
    ss_ab = ss_cells - ss_a - ss_b
    ss_error = ss_total - ss_cells

    df_a = na - 1
    df_b = nb - 1
    df_ab = df_a * df_b
    df_error = n - na * nb

    def _effect(ss: float, df: int) -> Dict[str, object]:
        if df <= 0 or df_error <= 0 or ss_error <= 0:
            return {"f": None, "df1": df, "df2": df_error, "p": None}
        ms = ss / df
        ms_error = ss_error / df_error
        f = ms / ms_error
        return {
            "f": round(f, 3),
            "df1": df,
            "df2": df_error,
            "p": round(_f_sf(f, df, df_error), 4),
        }

    return {
        "factor_a": _effect(ss_a, df_a),
        "factor_b": _effect(ss_b, df_b),
        "interaction": _effect(ss_ab, df_ab),
    }


def art_anova(data: Sequence[Tuple[object, object, float]]) -> Dict[str, object]:
    """Aligned Rank Transform factorial ANOVA for a 2-factor design.

    The non-parametric test the E1 node-drain experiment needs: its
    headline is the *interaction* between placement and replica count
    (placement moves user-visible availability at 3 replicas but not at
    1), and ranks rather than raw values are the right currency for
    bounded, non-normal availability/recovery responses.  ART (Wobbrock
    et al., CHI 2011) aligns the response for each effect by subtracting
    the other effects' cell-mean estimates, ranks the aligned values, runs
    an ordinary factorial ANOVA on the ranks, and reports only the F for
    the aligned effect.

    Args:
        data: List of ``(factor_a_level, factor_b_level, value)`` tuples.

    Returns:
        Dict with ``factor_a``, ``factor_b``, and ``interaction`` — each a
        ``{f, df1, df2, p}`` dict — plus ``n``, ``levels_a``, ``levels_b``.
        F/p are ``None`` for an effect when the design has too few
        observations to leave error degrees of freedom.
    """
    rows = list(data)
    a_levels = sorted({r[0] for r in rows}, key=repr)
    b_levels = sorted({r[1] for r in rows}, key=repr)

    base: Dict[str, object] = {
        "n": len(rows),
        "levels_a": a_levels,
        "levels_b": b_levels,
    }
    if len(a_levels) < 2 or len(b_levels) < 2:
        empty = {"f": None, "df1": 0, "df2": 0, "p": None}
        base.update({"factor_a": empty, "factor_b": empty, "interaction": dict(empty)})
        return base

    gm = statistics.mean(r[2] for r in rows)
    a_groups: Dict[object, List[float]] = defaultdict(list)
    b_groups: Dict[object, List[float]] = defaultdict(list)
    ab_groups: Dict[Tuple[object, object], List[float]] = defaultdict(list)
    for ra, rb, rv in rows:
        a_groups[ra].append(rv)
        b_groups[rb].append(rv)
        ab_groups[(ra, rb)].append(rv)
    a_mean = {k: statistics.mean(v) for k, v in a_groups.items()}
    b_mean = {k: statistics.mean(v) for k, v in b_groups.items()}
    ab_mean = {k: statistics.mean(v) for k, v in ab_groups.items()}

    def _aligned(effect: str) -> List[Tuple[object, object, float]]:
        out: List[Tuple[object, object, float]] = []
        for a, b, y in rows:
            if effect == "a":
                aligned = y - ab_mean[(a, b)] + a_mean[a]
            elif effect == "b":
                aligned = y - ab_mean[(a, b)] + b_mean[b]
            else:  # interaction
                aligned = y - a_mean[a] - b_mean[b] + gm
            out.append((a, b, aligned))
        return out

    def _ranked(aligned: List[Tuple[object, object, float]]) -> List[Tuple[object, object, float]]:
        ranks = _rank_with_ties([r[2] for r in aligned])
        return [(r[0], r[1], rank) for r, rank in zip(aligned, ranks)]

    base["factor_a"] = _two_factor_f(_ranked(_aligned("a")))["factor_a"]
    base["factor_b"] = _two_factor_f(_ranked(_aligned("b")))["factor_b"]
    base["interaction"] = _two_factor_f(_ranked(_aligned("ab")))["interaction"]
    return base


def page_trend_test(blocks: Sequence[Sequence[float]]) -> Dict[str, object]:
    """Page's L trend test for a predicted monotone ordering across treatments.

    Page's L tests the ordered alternative that ``k`` related treatments rise in
    a *predicted* order across ``n`` blocks — for H1, the C1 east-west p95
    across the ordered cross-node-fraction levels, one value per level per
    session.  Each ``blocks[i]`` is one block's ``k`` values **already in the
    predicted-increasing order** (f = 0 → 1).  Within each block the values are
    ranked 1..k (1 = smallest; average ranks for ties), ``R_j`` is the rank sum
    of treatment ``j`` over blocks, and ``L = Σ_j j·R_j``.  A large ``L`` (high
    ranks on later-ordered treatments) supports the increasing trend; the
    one-sided p-value is the upper tail of the normal approximation
    ``Z = (L − E[L]) / sqrt(Var[L])`` with ``E[L] = n·k·(k+1)²/4``.  ``Var[L]``
    is the **tie-corrected** within-block-permutation variance (it reduces to
    the textbook ``n·k²·(k+1)·(k²−1)/144`` when no block has ties), consistent
    with the tie handling in :func:`mann_whitney_u` /
    :func:`wilcoxon_signed_rank`.

    Args:
        blocks: ``n`` blocks, each a sequence of ``k`` values in the predicted
            increasing order. Every block must have the same length ``k``.

    Returns:
        Dict with ``l_statistic``, ``z``, ``p_one_sided`` (increasing
        alternative), ``rank_sums`` (``R_j`` in predicted order), ``n_blocks``,
        and ``k``. ``z``/``p_one_sided`` are ``None`` when no trend is defined
        (no blocks, ``k < 2``) or there is no null variability (every block
        fully tied); ``l_statistic`` is still reported in the fully-tied case.

    Raises:
        ValueError: if the blocks differ in length.
    """
    n = len(blocks)
    k = len(blocks[0]) if n else 0
    if any(len(b) != k for b in blocks):
        raise ValueError("page_trend_test requires equal-length blocks")
    if n == 0 or k < 2:
        return {
            "l_statistic": None,
            "z": None,
            "p_one_sided": None,
            "rank_sums": [],
            "n_blocks": n,
            "k": k,
        }
    half = (k + 1) / 2.0
    rank_sums = [0.0] * k
    # Σ_i Σ_m (assigned-rank − (k+1)/2)² — the tie-aware within-block rank
    # spread (an all-tied block contributes 0; a 1..k block contributes the
    # full k(k²−1)/12).
    block_spread = 0.0
    for block in blocks:
        ranks = _rank_with_ties(list(block))
        for j in range(k):
            rank_sums[j] += ranks[j]
        block_spread += sum((r - half) ** 2 for r in ranks)
    l_stat = sum((j + 1) * rank_sums[j] for j in range(k))
    mean_l = n * k * (k + 1) ** 2 / 4.0
    # Var[L] under H0: within each block the ranks are a random permutation of
    # that block's (possibly tied, average) rank values, so for the linear
    # statistic L = Σ_j j·R_j, Var[L] = (S_c/(k−1))·Σ_i S_{a,i} with
    # S_c = Σ_j (j−(k+1)/2)² the predicted-score spread and S_{a,i} the block's
    # assigned-rank spread.  This is the tie-corrected form (matching
    # ``mann_whitney_u`` / ``wilcoxon_signed_rank`` above); it reduces exactly to
    # the textbook n·k²·(k+1)·(k²−1)/144 when no block has ties.
    s_c = sum((j + 1 - half) ** 2 for j in range(k))
    var_l = s_c * block_spread / (k - 1)
    if var_l <= 0:  # every block fully tied -> no null variability -> z undefined
        return {
            "l_statistic": round(l_stat, 2),
            "z": None,
            "p_one_sided": None,
            "rank_sums": [round(r, 2) for r in rank_sums],
            "n_blocks": n,
            "k": k,
        }
    z = (l_stat - mean_l) / math.sqrt(var_l)
    return {
        "l_statistic": round(l_stat, 2),
        "z": round(z, 3),
        "p_one_sided": round(_standard_normal_sf(z), 4),
        "rank_sums": [round(r, 2) for r in rank_sums],
        "n_blocks": n,
        "k": k,
    }
