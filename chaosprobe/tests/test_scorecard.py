"""Tests for scripts/scorecard.py (V2-H5 layered scorecard, D-2026-06-13-01).

Covers the three sub-score formulas (availability, mechanism-reconvergence,
user-tail) incl. the None / clamp / never-recovers / never-drops edge cases,
the UDP conntrack reconvergence extractor, chaos_window_seconds sourcing, and
the frozen V2-H5 reliability evaluation on synthetic reliable-vs-noisy data
(pass vs fail; conjunction; user-tail exclusion; graceful "not evaluable").
"""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "scorecard.py"
_spec = importlib.util.spec_from_file_location("scorecard", _SCRIPT)
assert _spec is not None and _spec.loader is not None
sc = importlib.util.module_from_spec(_spec)
sys.modules["scorecard"] = sc
_spec.loader.exec_module(sc)

_BASE = datetime(2026, 6, 13, 12, 0, 0, tzinfo=timezone.utc)


def _ts(offset_s):
    """ISO timestamp at base + offset_s seconds."""
    return (_BASE + timedelta(seconds=offset_s)).isoformat()


def _es_sample(offset_s, phase, ready, svc="svc-a"):
    """One EndpointSlice time-series sample."""
    return {"ts": _ts(offset_s), "phase": phase, "services": {svc: {"ready": ready}}}


def _udp_sample(offset_s, phase, count, node="w1"):
    """One conntrack UDP sample."""
    return {"ts": _ts(offset_s), "node": node, "proto": "udp", "count": count, "phase": phase}


# ──────────────────────────────────────────────────────────────────────
# chaos_window_seconds
# ──────────────────────────────────────────────────────────────────────


def test_chaos_window_from_anomaly_labels():
    it = {
        "anomalyLabels": [
            {"parameters": {"duration_s": 60}},
            {"parameters": {"duration_s": 120}},  # the max wins
        ]
    }
    assert sc.chaos_window_seconds(it) == 120.0


def test_chaos_window_falls_back_to_endpointslice_span():
    it = {
        "anomalyLabels": [{"parameters": {"duration_s": 0}}],  # non-positive -> fall back
        "metrics": {
            "endpointSliceTimeSeries": {
                "samples": [
                    _es_sample(10, "during-chaos", 1),
                    _es_sample(40, "during-chaos", 1),
                ]
            }
        },
    }
    assert sc.chaos_window_seconds(it) == 30.0


def test_chaos_window_falls_back_to_conntrack_span():
    it = {
        "metrics": {
            "conntrackProtocolSamples": [
                _udp_sample(0, "during-chaos", 5),
                _udp_sample(25, "during-chaos", 5),
            ]
        }
    }
    assert sc.chaos_window_seconds(it) == 25.0


def test_chaos_window_none_when_unsourceable():
    assert sc.chaos_window_seconds({}) is None
    # Bad / single-sample spans yield None too.
    it = {"metrics": {"endpointSliceTimeSeries": {"samples": [_es_sample(0, "during-chaos", 1)]}}}
    assert sc.chaos_window_seconds(it) is None


def test_phase_span_skips_bad_timestamps():
    samples = [
        {"phase": "during-chaos"},  # no ts
        {"phase": "during-chaos", "ts": "not-a-ts"},  # unparseable
        {"phase": "during-chaos", "ts": _ts(0)},
        {"phase": "during-chaos", "ts": _ts(20)},
        {"phase": "pre-chaos", "ts": _ts(5)},  # wrong phase
    ]
    assert sc._phase_span(samples, "during-chaos") == 20.0
    assert sc._phase_span([], "during-chaos") is None


# ──────────────────────────────────────────────────────────────────────
# Baseline ready endpoints
# ──────────────────────────────────────────────────────────────────────


def test_baseline_from_last_pre_chaos_series_sample():
    m = {
        "endpointSliceTimeSeries": {
            "samples": [
                _es_sample(0, "pre-chaos", 2),
                _es_sample(15, "pre-chaos", 3),  # last pre-chaos -> baseline 3
                _es_sample(30, "during-chaos", 0),
            ]
        }
    }
    assert sc.baseline_ready_endpoints(m, ["svc-a"]) == 3


def test_baseline_falls_back_to_snapshot():
    m = {
        "endpointSlices": {"preChaos": {"services": {"svc-a": {"ready": 4}, "svc-b": {"ready": 1}}}}
    }
    assert sc.baseline_ready_endpoints(m, ["svc-a", "svc-b"]) == 5


def test_baseline_none_when_absent():
    assert sc.baseline_ready_endpoints({}, ["svc-a"]) is None
    # Series present but no pre-chaos sample, and no snapshot -> None.
    m = {"endpointSliceTimeSeries": {"samples": [_es_sample(0, "during-chaos", 1)]}}
    assert sc.baseline_ready_endpoints(m, ["svc-a"]) is None


def test_baseline_series_skips_unusable_and_bad_ts_samples():
    m = {
        "endpointSliceTimeSeries": {
            "samples": [
                {"phase": "pre-chaos", "services": {"svc-a": {"ready": 9}}},  # no ts -> skipped
                {"ts": "bad", "phase": "pre-chaos", "services": {"svc-a": {"ready": 9}}},
                _es_sample(0, "pre-chaos", 3),
                {
                    "ts": _ts(15),
                    "phase": "pre-chaos",
                    "services": {"other": {"ready": 7}},
                },  # no svc
            ]
        }
    }
    assert sc.baseline_ready_endpoints(m, ["svc-a"]) == 3


def test_baseline_series_uses_latest_pre_chaos_despite_input_order():
    # An earlier pre-chaos sample listed AFTER a later one must not override the
    # latest-timestamp baseline.
    m = {
        "endpointSliceTimeSeries": {
            "samples": [
                _es_sample(15, "pre-chaos", 5),  # later ts, listed first
                _es_sample(0, "pre-chaos", 2),  # earlier ts, listed second
            ]
        }
    }
    assert sc.baseline_ready_endpoints(m, ["svc-a"]) == 5


def test_snapshot_baseline_none_when_no_app_service_ready():
    es = {"preChaos": {"services": {"other": {"ready": 5}}}}
    assert sc._snapshot_pre_baseline(es, ["svc-a"]) is None


# ──────────────────────────────────────────────────────────────────────
# UDP reconvergence extractor
# ──────────────────────────────────────────────────────────────────────


def test_udp_reconvergence_drops_and_recovers():
    samples = [
        _udp_sample(0, "pre-chaos", 100),  # baseline pool = 100
        _udp_sample(15, "during-chaos", 40),  # drop start (chaos start = first during = 15)
        _udp_sample(30, "during-chaos", 40),
        _udp_sample(45, "post-chaos", 100),  # back to baseline
    ]
    # chaos start = 15, recover at 45 -> 30s.
    assert sc.udp_reconvergence_time_s(samples) == 30.0


def test_udp_reconvergence_never_drops_is_zero():
    samples = [
        _udp_sample(0, "pre-chaos", 50),
        _udp_sample(15, "during-chaos", 60),  # above baseline, never drops
        _udp_sample(30, "post-chaos", 55),
    ]
    assert sc.udp_reconvergence_time_s(samples) == 0.0


def test_udp_reconvergence_never_recovers_is_window_lower_bound():
    samples = [
        _udp_sample(0, "pre-chaos", 100),
        _udp_sample(15, "during-chaos", 30),  # drop, chaos start = 15
        _udp_sample(45, "post-chaos", 40),  # window end still below baseline
    ]
    # 45 - 15 = 30s lower bound.
    assert sc.udp_reconvergence_time_s(samples) == 30.0


def test_udp_reconvergence_none_without_baseline_or_samples():
    # No pre-chaos pool -> None.
    only_during = [_udp_sample(15, "during-chaos", 30)]
    assert sc.udp_reconvergence_time_s(only_during) is None
    # Baseline present but no during/post samples -> None.
    only_pre = [_udp_sample(0, "pre-chaos", 100)]
    assert sc.udp_reconvergence_time_s(only_pre) is None
    assert sc.udp_reconvergence_time_s([]) is None


def test_udp_reconvergence_explicit_chaos_start_and_sums_nodes():
    samples = [
        _udp_sample(0, "pre-chaos", 50, node="w1"),
        _udp_sample(0, "pre-chaos", 50, node="w2"),  # baseline pool = 100
        _udp_sample(15, "during-chaos", 10, node="w1"),
        _udp_sample(15, "during-chaos", 10, node="w2"),  # summed = 20 < 100 -> drop
        _udp_sample(30, "during-chaos", 60, node="w1"),
        _udp_sample(30, "during-chaos", 60, node="w2"),  # summed = 120 >= 100 -> recover
    ]
    # Explicit chaos start at offset 10 -> recover at 30 -> 20s.
    assert sc.udp_reconvergence_time_s(samples, chaos_start_epoch=_BASE.timestamp() + 10) == 20.0


def test_udp_reconvergence_skips_bad_ts_and_non_udp():
    samples = [
        _udp_sample(0, "pre-chaos", 100),
        {"ts": "bad", "node": "w1", "proto": "udp", "count": 1, "phase": "during-chaos"},
        {"ts": _ts(15), "node": "w1", "proto": "udp", "phase": "during-chaos"},  # no count -> 0
        {"ts": _ts(15), "node": "w1", "proto": "tcp", "count": 99, "phase": "during-chaos"},
        _udp_sample(30, "post-chaos", 100),
    ]
    # At t=15 only the udp-no-count sample counts (0 < 100 -> drop); recover at t=30 -> 15s.
    assert sc.udp_reconvergence_time_s(samples) == 15.0


def test_udp_reconvergence_all_samples_before_chaos_start_is_none():
    # Baseline + during/post samples exist, but the explicit chaos start is
    # after every sample -> the post-start window is empty -> None.
    samples = [
        _udp_sample(0, "pre-chaos", 100),
        _udp_sample(15, "during-chaos", 50),
        _udp_sample(30, "post-chaos", 100),
    ]
    far_future = _BASE.timestamp() + 999
    assert sc.udp_reconvergence_time_s(samples, chaos_start_epoch=far_future) is None


def test_udp_reconvergence_after_empty_returns_none():
    # Baseline present, but every during/post sample predates the chaos start
    # filter is impossible here; instead exercise the `not after` guard by giving
    # only a pre-chaos baseline plus an unparseable during sample.
    samples = [
        _udp_sample(0, "pre-chaos", 100),
        {"ts": "bad", "node": "w1", "proto": "udp", "count": 5, "phase": "during-chaos"},
    ]
    assert sc.udp_reconvergence_time_s(samples) is None


# ──────────────────────────────────────────────────────────────────────
# availability sub-score
# ──────────────────────────────────────────────────────────────────────


def _avail_iter(
    *,
    baseline_ready=4,
    depth=2,
    dur_offsets=(0, 15, 30),
    dur_readys=(4, 0, 4),
    err_routes=None,
    window=60,
):
    """A raw iteration carrying the availability inputs."""
    es_samples = [
        _es_sample(o, p, r)
        for o, p, r in zip(dur_offsets, ("pre-chaos", "during-chaos", "post-chaos"), dur_readys)
    ]
    metrics = {
        "endpointSlices": {
            "preChaos": {"services": {"svc-a": {"ready": baseline_ready}}},
            "duringChaos": {"services": {"svc-a": {"ready": baseline_ready - depth}}},
        },
        "endpointSliceTimeSeries": {"samples": es_samples},
        "latency": {"phases": {"during-chaos": {"routes": err_routes or {}}}},
    }
    return {"anomalyLabels": [{"parameters": {"duration_s": window}}], "metrics": metrics}


def test_availability_subscore_basic():
    # baseline 4, depth 2 -> depth_loss 0.5; real duration 30s / 60 window -> 0.5;
    # one user route with 0 errors -> error_loss 0.  mean = 1/3 -> 100*(1-1/3)=66.67.
    it = _avail_iter(
        baseline_ready=4,
        depth=2,
        dur_readys=(4, 0, 4),  # drop at 15, recover at 30 -> 15... see below
        err_routes={"/": {"sampleCount": 10, "errorCount": 0}},
    )
    # series: pre@0=4, during@15=0 (drop), post@30=4 (recover) -> duration 15s.
    # depth_loss=0.5, duration_loss=15/60=0.25, error_loss=0 -> mean=0.25 -> 75.
    assert sc.availability_subscore(it, ["svc-a"]) == 75.0


def test_availability_subscore_clamps_losses():
    # depth bigger than baseline and duration bigger than window -> losses clamp to 1.
    it = _avail_iter(
        baseline_ready=2,
        depth=2,  # depth_loss raw = 1.0
        dur_readys=(2, 0, 0),  # never recovers; window end at 30 -> 30s
        window=10,  # duration_loss raw = 30/10=3 -> clamp 1
        err_routes={"/": {"sampleCount": 0, "errorCount": 5}},  # error_loss 1
    )
    assert sc.availability_subscore(it, ["svc-a"]) == 0.0


def test_availability_subscore_none_when_baseline_nonpositive():
    it = _avail_iter(baseline_ready=0)
    # baseline 0 from snapshot AND series last pre-chaos is 4 -> use series 4? No:
    # series baseline wins; force no series baseline by removing pre-chaos sample.
    it["metrics"]["endpointSliceTimeSeries"]["samples"] = [_es_sample(15, "during-chaos", 0)]
    it["metrics"]["endpointSlices"]["preChaos"]["services"]["svc-a"]["ready"] = 0
    assert sc.availability_subscore(it, ["svc-a"]) is None


def test_availability_subscore_none_when_real_series_absent():
    it = _avail_iter()
    it["metrics"]["endpointSliceTimeSeries"] = {}  # no real duration series
    assert sc.availability_subscore(it, ["svc-a"]) is None


def test_availability_subscore_none_when_window_unsourceable():
    it = _avail_iter()
    it["anomalyLabels"] = []
    # Single during sample -> no derivable span either.
    it["metrics"]["endpointSliceTimeSeries"]["samples"] = [
        _es_sample(0, "pre-chaos", 4),
        _es_sample(15, "during-chaos", 0),
        _es_sample(30, "post-chaos", 4),
    ]
    # during-chaos span needs >=2 during samples; only one -> None window.
    assert sc.availability_subscore(it, ["svc-a"]) is None


def test_availability_subscore_none_when_error_rate_absent():
    it = _avail_iter(err_routes={})  # no user routes -> user_error_rate None
    assert sc.availability_subscore(it, ["svc-a"]) is None


def test_availability_subscore_none_when_depth_unmeasurable():
    it = _avail_iter(err_routes={"/": {"sampleCount": 1, "errorCount": 0}})
    it["metrics"]["endpointSlices"] = {}  # es_trough -> (None, None)
    assert sc.availability_subscore(it, ["svc-a"]) is None


# ──────────────────────────────────────────────────────────────────────
# mechanism sub-score
# ──────────────────────────────────────────────────────────────────────


def _mech_iter(udp_samples, window=60):
    return {
        "anomalyLabels": [{"parameters": {"duration_s": window}}],
        "metrics": {"conntrackProtocolSamples": udp_samples},
    }


def test_mechanism_subscore_basic():
    samples = [
        _udp_sample(0, "pre-chaos", 100),
        _udp_sample(15, "during-chaos", 50),  # drop 50 -> disturbance_loss 0.5
        _udp_sample(45, "post-chaos", 100),  # recover -> reconv 30s / 60 = 0.5
    ]
    it = _mech_iter(samples)
    # mean(0.5, 0.5)=0.5 -> 50.
    assert sc.mechanism_subscore(it) == 50.0


def test_mechanism_subscore_zero_pool_disturbance_is_zero():
    samples = [
        _udp_sample(0, "pre-chaos", 0),  # pool 0 -> disturbance_loss 0.0
        _udp_sample(15, "during-chaos", 0),
        _udp_sample(30, "post-chaos", 0),
    ]
    it = _mech_iter(samples)
    # disturbance 0; reconverg: baseline 0, during 0 >= 0 -> never drops -> 0.
    assert sc.mechanism_subscore(it) == 100.0


def test_mechanism_subscore_none_when_udp_absent():
    assert sc.mechanism_subscore(_mech_iter([])) is None
    # pre present, during absent -> None.
    only_pre = [_udp_sample(0, "pre-chaos", 100)]
    assert sc.mechanism_subscore(_mech_iter(only_pre)) is None


def test_mechanism_subscore_none_when_reconverg_unmeasurable():
    # pre + during present (drop) but no recovery samples AND no post -> reconv lower bound,
    # so make reconv None by removing the during/post timestamped samples after computing drop:
    # use a during sample with a bad ts so by_ts is empty -> reconv None.
    samples = [
        _udp_sample(0, "pre-chaos", 100),
        {"node": "w1", "proto": "udp", "count": 10, "phase": "during-chaos"},  # no ts
    ]
    # udp_cluster_phase_mean uses count regardless of ts, so udp_dur is measurable (10),
    # but reconvergence's by_ts is empty -> reconv None -> sub-score None.
    assert sc.mechanism_subscore(_mech_iter(samples)) is None


def test_mechanism_subscore_none_when_window_unsourceable():
    samples = [
        _udp_sample(0, "pre-chaos", 100),
        _udp_sample(15, "during-chaos", 50),
        _udp_sample(45, "post-chaos", 100),
    ]
    it = _mech_iter(samples)
    it["anomalyLabels"] = []
    # Only one during-chaos sample -> no derivable during span -> window None.
    assert sc.mechanism_subscore(it) is None


# ──────────────────────────────────────────────────────────────────────
# user-tail sub-score
# ──────────────────────────────────────────────────────────────────────


def _user_iter(routes):
    return {"metrics": {"latency": {"phases": {"during-chaos": {"routes": routes}}}}}


def test_user_tail_subscore_basic():
    # dependent (/product) p95 = 200, control (/cart) p95 = 50 -> 100*min(1,50/200)=25.
    it = _user_iter({"/product": {"p95_ms": 200.0}, "/cart": {"p95_ms": 50.0}})
    assert sc.user_tail_subscore(it) == 25.0


def test_user_tail_subscore_caps_at_100():
    # control slower than dependent -> ratio > 1 -> capped at 100.
    it = _user_iter({"/product": {"p95_ms": 50.0}, "/cart": {"p95_ms": 200.0}})
    assert sc.user_tail_subscore(it) == 100.0


def test_user_tail_subscore_median_over_multiple_routes():
    it = _user_iter(
        {
            "/product/1": {"p95_ms": 100.0},
            "productcatalogservice->x": {"p95_ms": 300.0},  # dep median = 200
            "/cart": {"p95_ms": 40.0},
            "checkoutservice->paymentservice": {"p95_ms": 60.0},  # ctrl median = 50
        }
    )
    assert sc.user_tail_subscore(it) == 25.0


def test_user_tail_subscore_none_without_dependent_or_control():
    assert sc.user_tail_subscore(_user_iter({"/cart": {"p95_ms": 50.0}})) is None  # no dep
    assert sc.user_tail_subscore(_user_iter({"/product": {"p95_ms": 50.0}})) is None  # no ctrl
    # dependent p95 <= 0 -> None.
    it = _user_iter({"/product": {"p95_ms": 0.0}, "/cart": {"p95_ms": 50.0}})
    assert sc.user_tail_subscore(it) is None


def test_route_p95_median_ignores_nonnumeric():
    latency = {"phases": {"during-chaos": {"routes": {"/product": {"p95_ms": "bad"}}}}}
    assert sc._route_p95_median(latency, "during-chaos", sc._dep) is None


def test_clamp01():
    assert sc._clamp01(-1.0) == 0.0
    assert sc._clamp01(2.0) == 1.0
    assert sc._clamp01(0.3) == 0.3


def test_iteration_subscores_bundles_all_three():
    it = _avail_iter(err_routes={"/": {"sampleCount": 10, "errorCount": 0}})
    it["metrics"]["conntrackProtocolSamples"] = [
        _udp_sample(0, "pre-chaos", 100),
        _udp_sample(15, "during-chaos", 50),
        _udp_sample(45, "post-chaos", 100),
    ]
    it["metrics"]["latency"]["phases"]["during-chaos"]["routes"].update(
        {"/product": {"p95_ms": 100.0}, "/cart": {"p95_ms": 50.0}}
    )
    row = sc.iteration_subscores(it, ["svc-a"])
    assert set(row) == {"availability", "mechanism", "user_tail"}
    assert row["availability"] is not None
    assert row["mechanism"] is not None
    assert row["user_tail"] == 50.0


# ──────────────────────────────────────────────────────────────────────
# Synthetic campaign fixtures for the reliability evaluation
# ──────────────────────────────────────────────────────────────────────


def _full_iter(
    *,
    depth,
    udp_during,
    dep_p95,
    score,
    window=60,
    baseline=10,
    pool=100,
    err=0.0,
    verdict="PASS",
    pre_taints=(),
):
    """A raw iteration with every sub-score measurable, parametrised by the
    knobs that drive the three sub-scores (so reliable vs noisy fixtures can be
    built by holding them steady vs jittering them across sessions)."""
    es_samples = [
        _es_sample(0, "pre-chaos", baseline),
        _es_sample(15, "during-chaos", baseline - depth),
        _es_sample(45, "post-chaos", baseline),  # recover -> 30s duration
    ]
    udp_samples = [
        _udp_sample(0, "pre-chaos", pool),
        _udp_sample(15, "during-chaos", udp_during),
        _udp_sample(45, "post-chaos", pool),  # recover -> reconv 30s
    ]
    metrics = {
        "endpointSlices": {
            "preChaos": {"services": {"svc-a": {"ready": baseline}}},
            "duringChaos": {"services": {"svc-a": {"ready": baseline - depth}}},
        },
        "endpointSliceTimeSeries": {"samples": es_samples},
        "conntrackProtocolSamples": udp_samples,
        "latency": {
            "phases": {
                "during-chaos": {
                    "routes": {
                        "/": {"sampleCount": 100, "errorCount": int(err * 100)},
                        "/product": {"p95_ms": dep_p95},
                        "/cart": {"p95_ms": 50.0},
                    }
                }
            }
        },
    }
    it = {
        "iteration": 1,
        "verdict": verdict,
        "resilienceScore": score,
        "anomalyLabels": [{"parameters": {"duration_s": window}}],
        "metrics": metrics,
    }
    if pre_taints:
        it["preChaosTaintReasons"] = list(pre_taints)
    return it


def _write_session(results_dir, name, conditions, *, timestamp, accepted=None, taints=None):
    """Write one v2 session: summary.json + raw per-condition files.

    ``conditions`` maps condition -> list of raw iteration dicts.
    """
    accepted = {} if accepted is None else accepted
    taints = taints or {}
    run_dir = results_dir / name
    run_dir.mkdir(parents=True)
    per_level = []
    for cond, iters in conditions.items():
        rec = {
            "condition": cond,
            "targetF": 0.5,
            "liveAchievedF": 0.5,
            "accepted": accepted.get(cond, True),
            "rejectionReasons": [] if accepted.get(cond, True) else ["fraction_target_missed"],
        }
        if cond in taints:
            rec["perIteration"] = [
                {"iteration": it, "taintReasons": list(r)} for it, r in sorted(taints[cond].items())
            ]
        per_level.append(rec)
        # Renumber iterations 1..n so per-iteration taint keys line up.
        numbered = [dict(it, iteration=i) for i, it in enumerate(iters, start=1)]
        raw = {"placement": {"assignments": {"svc-a": "w1"}}, "iterations": numbered}
        (run_dir / f"{cond}.json").write_text(json.dumps(raw))
    summary = {
        "runId": name,
        "timestamp": timestamp,
        "faults": {"pod-delete": {"strategies": {}}},
        "v2Session": {
            "solverSeed": 0,
            "replicas": 1,
            "mode": "packed",
            "levels": [0.5] * len(conditions),
            "workers": ["w1", "w2"],
            "perLevel": per_level,
        },
    }
    (run_dir / "summary.json").write_text(json.dumps(summary))


# Four distinct conditions, each given a characteristic sub-score level, so the
# between-condition variance is real.  Reliable = the same sub-score levels
# reproduce across sessions; the v1 ``score`` is DELIBERATELY scrambled session
# to session (the v1 aggregate is the known-unreliable comparator V2-H5 must
# beat).  noisy = the sub-score levels themselves are scrambled too.
_COND_LEVELS = {
    # condition: (depth, udp_during, dep_p95)
    "f-a": (1, 90, 60.0),
    "f-b": (3, 70, 120.0),
    "f-c": (5, 50, 200.0),
    "f-d": (7, 30, 320.0),
}

# Per-session v1 scores, scrambled so ICC_v1 is low while the sub-scores stay
# reproducible (the realistic V2-H5 win condition).
_SCRAMBLED_SCORES = {
    "20260101-000000": {"f-a": 20.0, "f-b": 80.0, "f-c": 40.0, "f-d": 60.0},
    "20260102-000000": {"f-a": 70.0, "f-b": 30.0, "f-c": 90.0, "f-d": 10.0},
    "20260103-000000": {"f-a": 50.0, "f-b": 55.0, "f-c": 25.0, "f-d": 75.0},
}


def _reliable_session(results_dir, name, timestamp):
    scores = _SCRAMBLED_SCORES[name]
    conditions = {}
    for cond, (depth, udp, dep) in _COND_LEVELS.items():
        conditions[cond] = [
            _full_iter(depth=depth, udp_during=udp, dep_p95=dep, score=scores[cond]),
            _full_iter(depth=depth, udp_during=udp, dep_p95=dep, score=scores[cond] + 1.0),
        ]
    _write_session(results_dir, name, conditions, timestamp=timestamp)


def test_evaluation_reliable_passes(tmp_path):
    # Same condition levels reproduce across 3 sessions -> high between-condition
    # ICC for both required sub-scores -> V2-H5 PASS.
    _reliable_session(tmp_path, "20260101-000000", "2026-01-01T00:00:00+00:00")
    _reliable_session(tmp_path, "20260102-000000", "2026-01-02T00:00:00+00:00")
    _reliable_session(tmp_path, "20260103-000000", "2026-01-03T00:00:00+00:00")
    result = sc.analyze(str(tmp_path), n_resamples=400, seed=1)
    by = {r["subscore"]: r for r in result["subscores"]}
    assert by["availability"]["evaluable"] and by["availability"]["pass"]
    assert by["mechanism"]["evaluable"] and by["mechanism"]["pass"]
    assert result["decision"]["conjunctionPass"] is True
    assert result["decision"]["verdict"] == "PASS"
    assert result["decision"]["holmInput"] is not None
    # user-tail is computed + reported but flagged exploratory.
    assert by["user_tail"]["role"] == "exploratory"


def _noisy_session(results_dir, name, timestamp, order):
    """A session whose conditions get scrambled sub-score levels (low ICC)."""
    levels = list(_COND_LEVELS.values())
    scores = _SCRAMBLED_SCORES[name]
    conditions = {}
    for cond, idx in zip(_COND_LEVELS, order):
        depth, udp, dep = levels[idx]
        conditions[cond] = [
            _full_iter(depth=depth, udp_during=udp, dep_p95=dep, score=scores[cond]),
            _full_iter(depth=depth, udp_during=udp, dep_p95=dep, score=scores[cond] + 1.0),
        ]
    _write_session(results_dir, name, conditions, timestamp=timestamp)


def test_evaluation_noisy_fails(tmp_path):
    # Each session assigns different levels to each condition -> no between-
    # condition reproducibility -> ICC near 0 -> V2-H5 FAIL.
    _noisy_session(tmp_path, "20260101-000000", "2026-01-01T00:00:00+00:00", [0, 1, 2, 3])
    _noisy_session(tmp_path, "20260102-000000", "2026-01-02T00:00:00+00:00", [3, 2, 1, 0])
    _noisy_session(tmp_path, "20260103-000000", "2026-01-03T00:00:00+00:00", [1, 3, 0, 2])
    result = sc.analyze(str(tmp_path), n_resamples=400, seed=1)
    by = {r["subscore"]: r for r in result["subscores"]}
    assert by["availability"]["evaluable"]
    assert not by["availability"]["pass"]
    assert result["decision"]["conjunctionPass"] is False
    assert result["decision"]["verdict"] == "FAIL"


def test_evaluation_not_evaluable_on_aa_block(tmp_path):
    # Frozen-A/A-style data: no EndpointSlice time series, no conntrack -> the
    # required sub-scores are all None -> NOT_EVALUABLE, no crash.
    conditions = {}
    for cond in ("f-a", "f-b"):
        conditions[cond] = [
            {"iteration": 1, "verdict": "PASS", "resilienceScore": 50.0, "metrics": {}},
            {"iteration": 2, "verdict": "PASS", "resilienceScore": 51.0, "metrics": {}},
        ]
    _write_session(tmp_path, "20260101-000000", conditions, timestamp="2026-01-01T00:00:00+00:00")
    _write_session(tmp_path, "20260102-000000", conditions, timestamp="2026-01-02T00:00:00+00:00")
    result = sc.analyze(str(tmp_path), n_resamples=200, seed=1)
    by = {r["subscore"]: r for r in result["subscores"]}
    assert not by["availability"]["evaluable"]
    assert not by["mechanism"]["evaluable"]
    assert result["decision"]["verdict"] == "NOT_EVALUABLE"
    assert result["decision"]["conjunctionPass"] is None
    assert result["decision"]["holmInput"] is None


def test_collect_excludes_rejected_and_missing_raw(tmp_path):
    # A rejected condition and a condition whose raw file is missing are both
    # excluded with a warning.
    run_dir = tmp_path / "20260101-000000"
    conditions = {"f-a": [_full_iter(depth=1, udp_during=90, dep_p95=60.0, score=80.0)]}
    _write_session(
        tmp_path,
        "20260101-000000",
        conditions,
        timestamp="2026-01-01T00:00:00+00:00",
        accepted={"f-a": False},
    )
    # Add a perLevel entry whose raw file does not exist.
    summary = json.loads((run_dir / "summary.json").read_text())
    summary["v2Session"]["perLevel"].append(
        {
            "condition": "f-z",
            "targetF": 0.5,
            "liveAchievedF": 0.5,
            "accepted": True,
            "rejectionReasons": [],
        }
    )
    (run_dir / "summary.json").write_text(json.dumps(summary))
    conditions_out, warnings, _taints = sc.collect_conditions(str(tmp_path))
    assert conditions_out == []  # f-a rejected, f-z missing raw
    assert any("not accepted" in w for w in warnings)
    assert any("missing" in w for w in warnings)


def test_collect_skips_unreadable_and_non_v2(tmp_path):
    # Unreadable summary.json -> warning, skipped.
    bad = tmp_path / "20260101-000000"
    bad.mkdir()
    (bad / "summary.json").write_text("{not json")
    # A non-v2 summary -> skipped via parse_session.
    nonv2 = tmp_path / "20260102-000000"
    nonv2.mkdir()
    (nonv2 / "summary.json").write_text(json.dumps({"runId": "x"}))
    conditions, warnings, _taints = sc.collect_conditions(str(tmp_path))
    assert conditions == []
    assert any("unreadable" in w for w in warnings)


def test_tainted_iterations_excluded(tmp_path):
    # A tainted iteration contributes a None row (excluded from the median).
    conditions = {
        "f-a": [
            _full_iter(depth=1, udp_during=90, dep_p95=60.0, score=80.0),
            _full_iter(depth=9, udp_during=10, dep_p95=999.0, score=1.0),  # would skew if kept
        ]
    }
    _write_session(
        tmp_path,
        "20260101-000000",
        conditions,
        timestamp="2026-01-01T00:00:00+00:00",
        taints={"f-a": {2: ["udp_preslope"]}},
    )
    conds, _warn, taints = sc.collect_conditions(str(tmp_path))
    assert len(conds) == 1
    obs = conds[0]
    # iteration 2 tainted -> None row; the median sees only iteration 1.
    assert obs.subscores["availability"][1] is None
    assert sc._cell_median(obs.subscores["availability"]) == obs.subscores["availability"][0]
    assert any("udp_preslope" in t for t in taints)


def test_pre_chaos_taint_from_raw_excluded(tmp_path):
    conditions = {
        "f-a": [
            _full_iter(depth=1, udp_during=90, dep_p95=60.0, score=80.0),
            _full_iter(
                depth=2,
                udp_during=80,
                dep_p95=70.0,
                score=70.0,
                pre_taints=["pre-window UDP slope"],
            ),
        ]
    }
    _write_session(tmp_path, "20260101-000000", conditions, timestamp="2026-01-01T00:00:00+00:00")
    conds, _warn, taints = sc.collect_conditions(str(tmp_path))
    obs = conds[0]
    assert obs.subscores["mechanism"][1] is None  # raw pre-chaos taint excluded
    assert any("pre-chaos" in t for t in taints)


def test_error_verdict_score_excluded(tmp_path):
    conditions = {
        "f-a": [
            _full_iter(depth=1, udp_during=90, dep_p95=60.0, score=80.0),
            _full_iter(depth=2, udp_during=80, dep_p95=70.0, score=0.0, verdict="ERROR"),
        ]
    }
    _write_session(tmp_path, "20260101-000000", conditions, timestamp="2026-01-01T00:00:00+00:00")
    conds, _warn, _taints = sc.collect_conditions(str(tmp_path))
    obs = conds[0]
    assert obs.v1_score[1] is None  # ERROR verdict's fabricated 0.0 dropped


# ──────────────────────────────────────────────────────────────────────
# CLI / report
# ──────────────────────────────────────────────────────────────────────


def test_main_pass_exit_zero_and_json(tmp_path, capsys):
    _reliable_session(tmp_path, "20260101-000000", "2026-01-01T00:00:00+00:00")
    _reliable_session(tmp_path, "20260102-000000", "2026-01-02T00:00:00+00:00")
    _reliable_session(tmp_path, "20260103-000000", "2026-01-03T00:00:00+00:00")
    out_json = tmp_path / "out.json"
    rc = sc.main(
        [
            "--results-dir",
            str(tmp_path),
            "--json",
            str(out_json),
            "--n-resamples",
            "300",
            "--seed",
            "1",
        ]
    )
    assert rc == 0
    written = json.loads(out_json.read_text())
    assert written["decision"]["verdict"] == "PASS"
    captured = capsys.readouterr().out
    assert "VERDICT: PASS" in captured
    assert "JSON written to" in captured


def test_main_fail_exit_one(tmp_path):
    _noisy_session(tmp_path, "20260101-000000", "2026-01-01T00:00:00+00:00", [0, 1, 2, 3])
    _noisy_session(tmp_path, "20260102-000000", "2026-01-02T00:00:00+00:00", [3, 2, 1, 0])
    _noisy_session(tmp_path, "20260103-000000", "2026-01-03T00:00:00+00:00", [1, 3, 0, 2])
    rc = sc.main(["--results-dir", str(tmp_path), "--n-resamples", "300", "--seed", "1"])
    assert rc == 1


def test_main_empty_exit_two(tmp_path):
    rc = sc.main(["--results-dir", str(tmp_path)])
    assert rc == 2


def test_main_not_evaluable_with_cells_exit_one(tmp_path):
    conditions = {
        "f-a": [{"iteration": 1, "verdict": "PASS", "resilienceScore": 50.0, "metrics": {}}],
        "f-b": [{"iteration": 1, "verdict": "PASS", "resilienceScore": 60.0, "metrics": {}}],
    }
    _write_session(tmp_path, "20260101-000000", conditions, timestamp="2026-01-01T00:00:00+00:00")
    _write_session(tmp_path, "20260102-000000", conditions, timestamp="2026-01-02T00:00:00+00:00")
    rc = sc.main(["--results-dir", str(tmp_path), "--n-resamples", "100", "--seed", "1"])
    assert rc == 1  # cells exist but required sub-scores not evaluable


def test_report_renders_all_sections(tmp_path, capsys):
    # Reliable + a taint + a warning so the report exercises every branch.
    _reliable_session(tmp_path, "20260101-000000", "2026-01-01T00:00:00+00:00")
    _reliable_session(tmp_path, "20260102-000000", "2026-01-02T00:00:00+00:00")
    result = sc.analyze(str(tmp_path), n_resamples=200, seed=1)
    result["warnings"].append("synthetic warning")
    result["taintedIterations"].append("run-x: synthetic taint")
    sc.print_report(result)
    out = capsys.readouterr().out
    assert "Warnings" in out
    assert "Tainted iterations" in out
    assert "Sub-scores" in out
    assert "V2-H5 decision" in out


def test_report_renders_not_evaluable(tmp_path, capsys):
    result = sc.analyze(str(tmp_path), n_resamples=50, seed=1)  # empty -> not evaluable
    sc.print_report(result)
    out = capsys.readouterr().out
    assert "NOT EVALUABLE" in out


def test_diff_bootstrap_empty_cells_degrades():
    # Empty cells -> no point diff, no bootstrap CI (the defensive guards).
    out = sc._diff_bootstrap_excludes_zero({}, {}, 0.95, 50, 1)
    assert out["pointDiff"] is None
    assert out["ciLow"] is None and out["ciHigh"] is None
    assert out["excludesZero"] is False
    assert out["pValue"] is None
    assert out["nResamples"] == 0


def test_metric_cells_skip_unmeasurable_median():
    # One condition fully measurable, one all-None -> the None cell is skipped
    # while the measurable one survives (exercises the median-None branch).
    measurable = sc.ConditionObs(run="r1", condition="f-a")
    measurable.subscores["availability"] = [75.0, 80.0]
    measurable.v1_score = [50.0, 51.0]
    empty = sc.ConditionObs(run="r1", condition="f-b")
    empty.subscores["availability"] = [None, None]
    empty.v1_score = [None, None]
    avail = sc._metric_cells([measurable, empty], "availability")
    assert set(avail) == {("f-a", "r1")}
    v1 = sc._metric_cells_v1([measurable, empty])
    assert set(v1) == {("f-a", "r1")}


def test_v1_cells_aligned_skips_kept_cell_with_none_median():
    # A cell in `keep` whose v1 median is None is skipped (the alignment guard).
    has_v1 = sc.ConditionObs(run="r1", condition="f-a")
    has_v1.v1_score = [50.0]
    no_v1 = sc.ConditionObs(run="r1", condition="f-b")
    no_v1.v1_score = [None]
    keep = {("f-a", "r1"), ("f-b", "r1")}
    aligned = sc._v1_cells_aligned([has_v1, no_v1], keep)
    assert set(aligned) == {("f-a", "r1")}


def test_build_parser_defaults():
    args = sc.build_parser().parse_args([])
    assert args.results_dir == "results/v2-c1"
    assert args.confidence == 0.95
    assert args.n_resamples == 2000
    assert args.seed == 42


def test_thin_replication_conditions_flags_single_session_conditions():
    cells = {
        ("f-000", "s1"): [80.0],
        ("f-000", "s2"): [82.0],  # f-000 has 2 sessions -> ok
        ("f-050", "s1"): [60.0],  # f-050 has only 1 session -> flagged
        ("f-100", "s3"): [40.0],  # f-100 has only 1 session -> flagged
    }
    assert sc._thin_replication_conditions(cells) == ["f-050", "f-100"]


def test_thin_replication_empty_when_all_conditions_replicated():
    cells = {
        ("f-000", "s1"): [80.0],
        ("f-000", "s2"): [82.0],
        ("f-050", "s1"): [60.0],
        ("f-050", "s2"): [61.0],
    }
    assert sc._thin_replication_conditions(cells) == []


def test_evaluate_subscore_reports_thin_replication():
    # one session per condition -> every condition is thin
    sub = {("f-000", "s1"): [80.0], ("f-050", "s1"): [60.0], ("f-100", "s1"): [40.0]}
    v1 = {("f-000", "s1"): [70.0], ("f-050", "s1"): [55.0], ("f-100", "s1"): [35.0]}
    row = sc.evaluate_subscore("availability", sub, v1, 0.95, 200, 42)
    assert row["thinReplicationConditions"] == ["f-000", "f-050", "f-100"]


def test_report_renders_thin_replication_warning(tmp_path, capsys):
    # Build a real result, then mark one subscore row thin -> inline warning.
    _reliable_session(tmp_path, "20260101-000000", "2026-01-01T00:00:00+00:00")
    _reliable_session(tmp_path, "20260102-000000", "2026-01-02T00:00:00+00:00")
    result = sc.analyze(str(tmp_path), n_resamples=200, seed=1)
    result["subscores"][0]["thinReplicationConditions"] = ["f-050", "f-100"]
    sc.print_report(result)
    out = capsys.readouterr().out
    assert "thin replication" in out
    assert "f-050, f-100" in out


def _slope_iter(n, slope_epm):
    """Iteration carrying a one-node pre-chaos UDP slope of exactly slope_epm."""
    return {
        "iteration": n,
        "verdict": "PASS",
        "metrics": {
            "conntrackProtocolSamples": [
                {
                    "node": "w1",
                    "proto": "udp",
                    "count": 1000.0,
                    "phase": "pre-chaos",
                    "ts": "2026-01-01T00:00:00+00:00",
                },
                {
                    "node": "w1",
                    "proto": "udp",
                    "count": 1000.0 + slope_epm,
                    "phase": "pre-chaos",
                    "ts": "2026-01-01T00:01:00+00:00",
                },
            ]
        },
    }


def test_load_condition_subscores_d3_slope_band_gate(tmp_path):
    # f-050 band is [414, 1084]: it1 slope 700 (in-band), it2 slope 5000 (out).
    _write_session(
        tmp_path,
        "s1",
        {"f-050": [_slope_iter(1, 700), _slope_iter(2, 5000)]},
        timestamp="2026-01-01T00:00:00+00:00",
    )
    run_dir = str(tmp_path / "s1")

    # Gate OFF (default loader): the out-of-band iteration is NOT slope-tainted.
    off_tainted: set = set()
    sc.load_condition_subscores(run_dir, "f-050", off_tainted, [])
    assert ("f-050", 2) not in off_tainted

    # Gate ON (the C1 path): the out-of-band iteration is tainted -> None rows.
    tainted: set = set()
    taints: list = []
    obs = sc.load_condition_subscores(run_dir, "f-050", tainted, taints, slope_band_taint=True)
    assert ("f-050", 2) in tainted
    assert any("udp_preslope_out_of_band" in t for t in taints)
    assert obs.v1_score[1] is None  # tainted iteration -> None v1 row


def test_cli_slope_band_flag_default_off_and_enable():
    # Default OFF per deviation D-2026-06-14-02; --slope-band-taint re-applies it.
    parser = sc.build_parser()
    assert parser.parse_args([]).slope_band_taint is False
    assert parser.parse_args(["--slope-band-taint"]).slope_band_taint is True
