"""Tests for scripts/thesis_figures.py — the thesis figure generator.

Pure-Python, no cluster: small synthetic summary fixtures mirror the shapes the
archived runs record (the same conventions as test_blast_radius.py and
test_cross_node_fraction.py). Every data-extraction/computation path is covered;
figure renderers get smoke tests (file written, non-empty) on synthetic data.
"""

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))  # sibling imports: campaign_status, blast_radius, ...
_SCRIPT = _SCRIPTS / "thesis_figures.py"
_spec = importlib.util.spec_from_file_location("thesis_figures", _SCRIPT)
assert _spec is not None and _spec.loader is not None
tf = importlib.util.module_from_spec(_spec)
sys.modules["thesis_figures"] = tf  # dataclasses resolves __module__ via sys.modules
_spec.loader.exec_module(tf)


# ── synthetic summary builders ────────────────────────────────────────────────


def _conntrack_phases(pre, during):
    return {
        "prometheus": {
            "phases": {
                "pre-chaos": {"metrics": {"conntrack_entries_per_node": {"mean": pre}}},
                "during-chaos": {"metrics": {"conntrack_entries_per_node": {"mean": during}}},
            }
        }
    }


def _churn_strategy(scores, pre=None, during=None):
    strat = {"experiment": {"perIterationScores": scores}, "metrics": {}}
    if pre is not None and during is not None:
        strat["metrics"] = _conntrack_phases(pre, during)
    return strat


def _churn_summary(strategies):
    return {"faults": {"pod-delete": {"strategies": strategies}}}


def _session(name, spread_flush, colocate_flush):
    """A campaign session where flush% is controllable per strategy."""
    summary = _churn_summary(
        {
            "baseline": _churn_strategy([100.0, 100.0]),
            "colocate": _churn_strategy([80.0, 60.0], 100.0, 100.0 - colocate_flush),
            "spread": _churn_strategy([70.0, 90.0], 100.0, 100.0 - spread_flush),
        }
    )
    return tf.extract_session(name, summary)


def _es_phase(ready_by_svc, captured_at):
    return {
        "capturedAt": captured_at,
        "services": {s: {"ready": r} for s, r in ready_by_svc.items()},
    }


def _drain_summary(colocate_during, spread_during):
    """Node-drain run: 3 services, colocate pinned to one node, spread across."""
    services_up = {"a": 1, "b": 1, "c": 1}

    def strat(assignments, during):
        return {
            "placement": {"assignments": assignments},
            "metrics": {
                "endpointSlices": {
                    "preChaos": _es_phase(services_up, "2026-06-08T20:00:00+00:00"),
                    "duringChaos": _es_phase(during, "2026-06-08T20:01:30+00:00"),
                    "postChaos": _es_phase(services_up, "2026-06-08T20:06:00+00:00"),
                }
            },
        }

    return {
        "faults": {
            "node-drain": {
                "strategies": {
                    "colocate": strat({"a": "w1", "b": "w1", "c": "w1"}, colocate_during),
                    "spread": strat({"a": "w1", "b": "w2", "c": "w3"}, spread_during),
                }
            }
        }
    }


def _load_summary_fixture():
    """Load-contention run with a node-local and a spreading strategy."""
    rva = [
        {"route": "frontend->cart", "latencyProber": {"during-chaos": {"meanP95_ms": 40.0}}},
        {"route": "cart->redis", "latencyProber": {"during-chaos": {"meanP95_ms": 50.0}}},
        {"route": "/home", "latencyProber": {"during-chaos": {"meanP95_ms": 900.0}}},
    ]

    def strat(placements):
        return {
            "aggregated": {"routeViewAggregate": rva},
            "iterations": [{"podPlacements": placements}],
        }

    colocated = {"frontend-abc12-x1": "w1", "cart-abc12-x1": "w1", "redis-abc12-x1": "w1"}
    spread = {"frontend-abc12-x1": "w1", "cart-abc12-x1": "w2", "redis-abc12-x1": "w3"}
    return {
        "faults": {
            "load-contention": {
                "strategies": {
                    "colocate": strat(colocated),
                    "spread": strat(spread),
                    "broken": {"aggregated": {}, "iterations": []},  # skipped: no data
                }
            }
        }
    }


# ── extract_session ───────────────────────────────────────────────────────────


def test_extract_session_scores_and_flush():
    summary = _churn_summary(
        {
            "baseline": _churn_strategy([100.0]),
            "colocate": _churn_strategy([80.0, 60.0], 100.0, 95.0),
            "spread": _churn_strategy([70.0], 200.0, 120.0),
        }
    )
    sess = tf.extract_session("s01", summary)
    assert sess.scores["baseline"] == [100.0]
    assert sess.scores["colocate"] == [80.0, 60.0]
    assert "baseline" not in sess.flush  # control excluded from the H2 metric
    assert sess.flush["colocate"] == pytest.approx(5.0)
    assert sess.flush["spread"] == pytest.approx(40.0)


def test_extract_session_ignores_non_churn_faults():
    summary = {
        "faults": {
            "node-cpu-hog": {"strategies": {"colocate": _churn_strategy([10.0], 100.0, 50.0)}}
        }
    }
    sess = tf.extract_session("s01", summary)
    assert sess.scores == {} and sess.flush == {}


def test_extract_session_tolerates_missing_blocks():
    summary = _churn_summary({"colocate": {"experiment": {}, "metrics": {}}})
    sess = tf.extract_session("s01", summary)
    assert sess.scores == {} and sess.flush == {}


# ── score cells + ICC ─────────────────────────────────────────────────────────


def test_score_cells_excludes_baseline():
    sessions = [_session("s01", 40.0, 2.0), _session("s02", 35.0, 1.0)]
    cells = tf.score_cells(sessions)
    assert ("baseline", "s01") not in cells
    assert cells[("colocate", "s01")] == [80.0, 60.0]
    assert cells[("spread", "s02")] == [70.0, 90.0]


def test_icc_point_and_trajectory():
    sessions = [_session("s01", 40.0, 2.0), _session("s02", 35.0, 1.0)]
    traj = tf.icc_trajectory(sessions, n_resamples=50)
    assert [p.n_sessions for p in traj] == [1, 2]
    final = tf.icc_point(sessions, n_resamples=50)
    assert final.icc is not None and 0.0 <= final.icc <= 1.0
    assert final.ci_low is not None and final.ci_high is not None
    assert final.ci_low <= final.ci_high


def test_icc_point_empty_sessions():
    point = tf.icc_point([], n_resamples=10)
    assert point.icc is None and point.ci_low is None and point.ci_high is None


# ── flush stats ───────────────────────────────────────────────────────────────


def test_flush_stats_pairs_and_direction():
    sessions = [_session("s01", 40.0, 2.0), _session("s02", 35.0, 1.0)]
    stats = tf.flush_stats(sessions)
    assert stats.pairs == [("s01", 40.0, 2.0), ("s02", 35.0, 1.0)]
    assert stats.wins == 2
    assert stats.median("spread") == pytest.approx(37.5)
    assert stats.median("colocate") == pytest.approx(1.5)
    assert stats.sign_p is not None and 0.0 <= stats.sign_p <= 1.0
    assert stats.wilcoxon_p is not None


def test_flush_stats_counts_only_winning_sessions():
    stats = tf.flush_stats([_session("s01", 40.0, 2.0), _session("s02", 1.0, 5.0)])
    assert stats.wins == 1


def test_flush_stats_no_pairs():
    sess = tf.extract_session(
        "s01", _churn_summary({"colocate": _churn_strategy([80.0], 100.0, 95.0)})
    )
    stats = tf.flush_stats([sess])
    assert stats.pairs == [] and stats.wins == 0
    assert stats.sign_p is None and stats.wilcoxon_p is None
    assert stats.median("spread") is None


# ── H3 scatter ────────────────────────────────────────────────────────────────


def test_h3_scatter_pairs_and_rho():
    rows = [
        {"conntrack_flush_pct": 1.0, "dep_p95": 10.0},
        {"conntrack_flush_pct": 2.0, "dep_p95": 20.0},
        {"conntrack_flush_pct": 3.0, "dep_p95": 30.0},
        {"conntrack_flush_pct": 4.0, "dep_p95": 40.0},
        {"conntrack_flush_pct": None, "dep_p95": 50.0},  # dropped
        {"conntrack_flush_pct": 5.0, "dep_p95": None},  # dropped
    ]
    stats = tf.h3_scatter(rows, "dep_p95")
    assert stats.n == 4 and len(stats.pairs) == 4
    assert stats.rho == pytest.approx(1.0)
    assert stats.p == pytest.approx(0.0)


def test_h3_scatter_too_few_points():
    stats = tf.h3_scatter([{"conntrack_flush_pct": 1.0, "ctrl_p95": 2.0}], "ctrl_p95")
    assert math.isnan(stats.rho)


# ── H5 points ─────────────────────────────────────────────────────────────────


def test_h5_points_fraction_tail_and_locality():
    points = {p.strategy: p for p in tf.h5_points(_load_summary_fixture())}
    assert set(points) == {"colocate", "spread"}  # 'broken' skipped
    assert points["colocate"].fraction == pytest.approx(0.0)
    assert points["colocate"].node_local is True
    assert points["spread"].fraction == pytest.approx(1.0)
    assert points["spread"].node_local is False
    # median over the east-west routes only (the /home route is excluded)
    assert points["spread"].ew_p95 == pytest.approx(45.0)


def test_h5_spearman():
    points = [
        tf.H5Point("a", 0.0, 30.0, True),
        tf.H5Point("b", 0.5, 40.0, False),
        tf.H5Point("c", 0.8, 45.0, False),
        tf.H5Point("d", 1.0, 50.0, False),
    ]
    rho, p, n = tf.h5_spearman(points)
    assert rho == pytest.approx(1.0) and n == 4


# ── H6 blast + trough trajectories ────────────────────────────────────────────


def test_h6_blast_takes_deepest_trough_across_runs():
    shallow = _drain_summary({"a": 1, "b": 1, "c": 0}, {"a": 1, "b": 1, "c": 1})
    deep = _drain_summary({"a": 0, "b": 0, "c": 0}, {"a": 0, "b": 1, "c": 1})
    blast = tf.h6_blast([("runA", shallow), ("runB", deep)])
    assert blast["colocate"].blast == 3 and blast["colocate"].measured == 3
    assert blast["colocate"].per_run == {"runA": 1, "runB": 3}
    assert blast["spread"].blast == 1
    assert blast["spread"].per_run == {"runA": 0, "runB": 1}


def test_endpoint_trajectories_times_and_totals():
    summary = _drain_summary({"a": 0, "b": 0, "c": 0}, {"a": 0, "b": 1, "c": 1})
    trajs = tf.endpoint_trajectories([("runB", summary)], ("colocate", "spread"))
    by_strategy = {t.strategy: t for t in trajs}
    colocate = by_strategy["colocate"]
    assert colocate.phases == ["preChaos", "duringChaos", "postChaos"]
    assert colocate.ready == [3, 0, 3]
    assert colocate.minutes == pytest.approx([0.0, 1.5, 6.0])
    assert colocate.n_services == 3
    assert by_strategy["spread"].ready == [3, 2, 3]


def test_endpoint_trajectories_skips_missing_strategy_and_short_series():
    summary = _drain_summary({"a": 0, "b": 0, "c": 0}, {"a": 1, "b": 1, "c": 1})
    strategies = summary["faults"]["node-drain"]["strategies"]
    del strategies["spread"]["metrics"]["endpointSlices"]["duringChaos"]
    del strategies["spread"]["metrics"]["endpointSlices"]["postChaos"]
    trajs = tf.endpoint_trajectories([("r", summary)], ("colocate", "spread", "absent"))
    assert [t.strategy for t in trajs] == ["colocate"]  # spread has <2 stamped phases


def test_total_ready_ignores_malformed_entries():
    phase = {"services": {"a": {"ready": 2}, "b": None, "c": {"ready": "x"}}}
    assert tf._total_ready(phase) == 2


# ── matrix ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "fault, cls",
    [
        ("pod-delete", "churn"),
        ("load-contention", "load contention"),
        ("node-cpu-hog", "load contention"),
        ("node-memory-hog", "load contention"),
        ("node-drain", "node drain"),
        ("pod-network-loss", None),
    ],
)
def test_matrix_class(fault, cls):
    assert tf.matrix_class(fault) == cls


def test_matrix_counts_across_runs():
    churn = _churn_summary({"colocate": {}, "spread": {}})
    churn["faults"]["pod-network-loss"] = {"strategies": {"colocate": {}}}  # unmapped: ignored
    drain = {"faults": {"node-drain": {"strategies": {"colocate": {}}}}}
    counts = tf.matrix_counts([("s01", churn), ("s02", churn), ("d1", drain)])
    assert not any(cls not in tf.MATRIX_CLASSES for _, cls in counts)
    assert counts[("colocate", "churn")] == 2
    assert counts[("spread", "churn")] == 2
    assert counts[("colocate", "node drain")] == 1
    assert ("spread", "node drain") not in counts


def test_fault_presence():
    summary = {"faults": {"pod-delete": {"strategies": {"b": {}, "a": {}}}, "x": None}}
    assert tf.fault_presence(summary) == {"pod-delete": ["a", "b"], "x": []}


def test_presence_as_faults_roundtrip():
    summary = _churn_summary({"colocate": _churn_strategy([1.0])})
    skeleton = tf._presence_as_faults(summary)
    assert skeleton == {"pod-delete": {"strategies": {"colocate": {}}}}
    assert tf.matrix_counts([("s01", {"faults": skeleton})])[("colocate", "churn")] == 1


# ── loading helpers ───────────────────────────────────────────────────────────


def test_campaign_session_paths_filters_to_snn(tmp_path):
    for name in ("s01", "s03"):
        d = tmp_path / name
        d.mkdir()
        (d / "summary.json").write_text("{}")
    (tmp_path / "s02").mkdir()  # no summary.json -> skipped
    partial = tmp_path / "20260610-172018"  # in-flight run -> skipped
    partial.mkdir()
    (partial / "summary.json").write_text("{}")
    assert tf.campaign_session_paths(str(tmp_path)) == [
        ("s01", str(tmp_path / "s01" / "summary.json")),
        ("s03", str(tmp_path / "s03" / "summary.json")),
    ]


def test_load_summary_roundtrip(tmp_path):
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({"faults": {}}))
    assert tf.load_summary(str(path)) == {"faults": {}}


def test_load_summary_rejects_non_object(tmp_path):
    path = tmp_path / "summary.json"
    path.write_text("[1, 2]")
    with pytest.raises(ValueError, match="expected a JSON object"):
        tf.load_summary(str(path))


# ── CLI plumbing ──────────────────────────────────────────────────────────────


def test_parse_figures_all():
    assert tf.parse_figures("all") == list(range(1, 10))


def test_parse_figures_subset():
    assert tf.parse_figures("5,3, 9") == [3, 5, 9]


def test_parse_figures_rejects_unknown():
    with pytest.raises(ValueError, match="unknown figure"):
        tf.parse_figures("0,12")


def test_apply_thesis_style_sets_publication_dpi():
    import matplotlib

    tf.apply_thesis_style()
    assert matplotlib.rcParams["savefig.dpi"] == 200
    assert matplotlib.rcParams["font.family"] == ["serif"]


# ── figure smoke tests (synthetic data, Agg backend) ──────────────────────────


def _assert_png(path):
    p = Path(path)
    assert p.is_file() and p.stat().st_size > 0 and p.suffix == ".png"


def test_fig01_workflow(tmp_path):
    tf.apply_thesis_style()
    _assert_png(tf.fig01_workflow(str(tmp_path)))


def test_fig02_core_matrix(tmp_path):
    counts = {("colocate", "churn"): 7, ("spread", "node drain"): 2}
    _assert_png(tf.fig02_core_matrix(counts, str(tmp_path)))


def test_fig02_core_matrix_empty(tmp_path):
    _assert_png(tf.fig02_core_matrix({}, str(tmp_path)))


def test_fig03_score_distributions(tmp_path):
    sessions = [_session("s01", 40.0, 2.0), _session("s02", 35.0, 1.0)]
    _assert_png(
        tf.fig03_score_distributions(
            sessions, tf.icc_point(sessions, n_resamples=20), str(tmp_path)
        )
    )


def test_fig03_handles_missing_icc(tmp_path):
    sessions = [_session("s01", 40.0, 2.0)]
    point = tf.IccPoint(n_sessions=1, icc=None, ci_low=None, ci_high=None)
    _assert_png(tf.fig03_score_distributions(sessions, point, str(tmp_path)))


def test_fig04_icc_trajectory(tmp_path):
    points = [
        tf.IccPoint(1, 0.8, 0.5, 0.9),
        tf.IccPoint(2, 0.2, 0.1, 0.4),
        tf.IccPoint(3, None, None, None),
    ]
    _assert_png(tf.fig04_icc_trajectory(points, str(tmp_path)))


def test_fig05_conntrack(tmp_path):
    stats = tf.flush_stats([_session("s01", 40.0, 2.0), _session("s02", 35.0, 1.0)])
    _assert_png(tf.fig05_conntrack(stats, str(tmp_path)))


def test_fig06_h3_scatter(tmp_path):
    rows = [
        {"conntrack_flush_pct": float(i), "dep_p95": 10.0 * i, "ctrl_p95": 50.0 - i}
        for i in range(1, 6)
    ]
    dep = tf.h3_scatter(rows, "dep_p95")
    ctrl = tf.h3_scatter(rows, "ctrl_p95")
    _assert_png(tf.fig06_h3_scatter(dep, ctrl, str(tmp_path)))


def test_cluster_by_fraction_splits_on_gaps():
    pts = [
        tf.H5Point("colocate", 0.0, 34.0, True),
        tf.H5Point("best-fit", 0.133, 35.3, True),
        tf.H5Point("baseline", 0.70, 43.5, False),
        tf.H5Point("spread", 0.733, 43.5, False),
        tf.H5Point("random", 0.80, 43.9, False),
    ]
    clusters = tf.cluster_by_fraction(pts)
    names = [[p.strategy for p in c] for c in clusters]
    # 0.0 -> 0.133 exceeds the gap; the 0.70..0.80 strategies chain together.
    assert names == [["colocate"], ["best-fit"], ["baseline", "spread", "random"]]


def test_cluster_by_fraction_empty():
    assert tf.cluster_by_fraction([]) == []


def test_fig07_fraction_vs_tail(tmp_path):
    _assert_png(tf.fig07_fraction_vs_tail(tf.h5_points(_load_summary_fixture()), str(tmp_path)))


def test_fig07_stacks_labels_for_crowded_clusters(tmp_path):
    pts = [
        tf.H5Point("colocate", 0.0, 34.0, True),
        tf.H5Point("baseline", 0.70, 43.5, False),
        tf.H5Point("spread", 0.73, 43.5, False),
        tf.H5Point("dependency-aware", 0.74, 42.6, False),
    ]
    _assert_png(tf.fig07_fraction_vs_tail(pts, str(tmp_path)))


def test_fig08_trough_timeline(tmp_path):
    summary = _drain_summary({"a": 0, "b": 0, "c": 0}, {"a": 0, "b": 1, "c": 1})
    named = [("runB", summary)]
    trajs = tf.endpoint_trajectories(named, ("colocate", "spread"))
    _assert_png(tf.fig08_trough_timeline(trajs, tf.h6_blast(named), str(tmp_path)))


def test_fig09_label_groups_chains_the_crowded_spreading_cluster():
    # The spreading strategies crowd x ~ 42.6-43.9 (incl. adversarial + spread
    # exactly co-located at 43.5) -> one cluster; the node-local pair sits
    # apart and stays as singletons.
    h5 = [
        tf.H5Point("colocate", 0.0, 33.9, True),
        tf.H5Point("best-fit", 0.13, 35.3, True),
        tf.H5Point("dependency-aware", 0.73, 42.6, False),
        tf.H5Point("spread", 0.73, 43.5, False),
        tf.H5Point("adversarial", 0.80, 43.5, False),
        tf.H5Point("random", 0.80, 43.9, False),
    ]
    groups = tf.fig09_label_groups(h5)
    names = [[p.strategy for p in g] for g in groups]
    assert names == [
        ["colocate"],
        ["best-fit"],
        ["dependency-aware", "adversarial", "spread", "random"],
    ]


def test_fig09_label_groups_single_point_zero_span():
    groups = tf.fig09_label_groups([tf.H5Point("colocate", 0.0, 33.9, True)])
    assert [[p.strategy for p in g] for g in groups] == [["colocate"]]


def test_fig09_tradeoff_stacks_coincident_labels(tmp_path):
    # Render path through the stacked-label (len(group) > 1) branch.
    h5 = [
        tf.H5Point("colocate", 0.0, 33.9, True),
        tf.H5Point("spread", 0.73, 43.5, False),
        tf.H5Point("adversarial", 0.80, 43.5, False),
    ]
    blast = {
        "colocate": tf.H6Blast("colocate", 11, 11, {"g": 11}),
        "spread": tf.H6Blast("spread", 2, 11, {"g": 2}),
        "adversarial": tf.H6Blast("adversarial", 2, 11, {"g": 2}),
    }
    _assert_png(tf.fig09_tradeoff(h5, blast, str(tmp_path)))


def test_fig09_tradeoff_with_pending_rug(tmp_path):
    h5 = [
        tf.H5Point("colocate", 0.0, 34.0, True),
        tf.H5Point("spread", 0.73, 43.5, False),
        tf.H5Point("random", 0.8, 44.0, False),  # no drain data -> rug mark
        tf.H5Point("baseline", 0.7, 43.0, False),  # control: neither point nor rug
    ]
    blast = {
        "colocate": tf.H6Blast("colocate", 11, 11, {"r": 11}),
        "spread": tf.H6Blast("spread", 2, 11, {"r": 2}),
    }
    _assert_png(tf.fig09_tradeoff(h5, blast, str(tmp_path)))


# ── generate(): end-to-end on a synthetic results tree ────────────────────────


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_generate_all_figures_end_to_end(tmp_path):
    campaign = tmp_path / "campaign"
    for i, (sp, co) in enumerate(((40.0, 2.0), (35.0, 1.0)), start=1):
        _write(
            campaign / f"s0{i}" / "summary.json",
            _churn_summary(
                {
                    "baseline": _churn_strategy([100.0, 100.0]),
                    "colocate": _churn_strategy([80.0, 60.0], 100.0, 100.0 - co),
                    "spread": _churn_strategy([70.0, 90.0], 100.0, 100.0 - sp),
                }
            ),
        )
    h4 = tmp_path / "h4run"
    _write(h4 / "summary.json", {"faults": {"load-contention": {"strategies": {"colocate": {}}}}})
    h5 = tmp_path / "h5run"
    _write(h5 / "summary.json", _load_summary_fixture())
    h6a = tmp_path / "h6a"
    _write(h6a / "summary.json", _drain_summary({"a": 1, "b": 1, "c": 0}, {"a": 1, "b": 1, "c": 1}))
    grad = tmp_path / "gradient"
    _write(
        grad / "summary.json", _drain_summary({"a": 0, "b": 0, "c": 0}, {"a": 0, "b": 1, "c": 1})
    )

    out = tmp_path / "figs"
    written = tf.generate(
        out_dir=str(out),
        figures=tf.parse_figures("all"),
        campaign_dir=str(campaign),
        h4_runs=(str(h4),),
        h5_run=str(h5),
        h6_runs=(str(h6a),),
        gradient_run=str(grad),
        n_resamples=20,
    )
    assert len(written) == 9
    for path in written:
        _assert_png(path)
    names = sorted(Path(p).name for p in written)
    assert names[0] == "fig-01-workflow.png" and names[-1] == "fig-09-tradeoff.png"


def test_generate_single_figure(tmp_path):
    out = tmp_path / "figs"
    written = tf.generate(out_dir=str(out), figures=[1])
    assert [Path(p).name for p in written] == ["fig-01-workflow.png"]
