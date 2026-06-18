"""Tests for scripts/holm_family.py (V2 confirmatory-family Holm capstone)."""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


hf = _load("holm_family")


# ── holm() core algorithm ──────────────────────────────────────────────


def test_holm_empty():
    assert hf.holm([]) == ([], [])


def test_holm_single_significant():
    adj, rej = hf.holm([0.01])
    assert adj == [0.01] and rej == [True]


def test_holm_single_not_significant():
    adj, rej = hf.holm([0.2])
    assert adj == [0.2] and rej == [False]


def test_holm_classic_example():
    # Textbook Holm: p = [.01,.04,.03,.005], m=4. Sorted: .005,.01,.03,.04.
    # adj: .005*4=.02; .01*3=.03; .03*2=.06; .04*1=.06(monotone) -> .02,.03,.06,.06
    adj, rej = hf.holm([0.01, 0.04, 0.03, 0.005], alpha=0.05)
    assert adj[3] == pytest.approx(0.02)  # p=.005
    assert adj[0] == pytest.approx(0.03)  # p=.01
    assert adj[2] == pytest.approx(0.06)  # p=.03
    assert adj[1] == pytest.approx(0.06)  # p=.04 (lifted to .03's neighbour by monotonicity)
    assert rej == [True, False, False, True]


def test_holm_monotonicity_enforced():
    # A small p after a large one must not produce a smaller adjusted value.
    adj, _ = hf.holm([0.5, 0.001])
    assert adj[0] >= adj[1] or adj[1] >= adj[0]  # both defined
    # sorted: .001*2=.002 ; .5*1=.5 -> .002, .5
    assert adj[1] == pytest.approx(0.002)
    assert adj[0] == pytest.approx(0.5)


def test_holm_caps_at_one():
    adj, rej = hf.holm([0.9, 0.95, 0.99])
    assert all(a <= 1.0 for a in adj)
    assert rej == [False, False, False]


def test_holm_stepdown_stops_at_first_failure():
    # Once one fails, all larger fail even if a later raw p < alpha/(remaining).
    # p=[.001, .04, .045], m=3: .001*3=.003 (rej); .04*2=.08 (fail); .045*1 ->.08 (fail)
    adj, rej = hf.holm([0.001, 0.04, 0.045], alpha=0.05)
    assert rej == [True, False, False]


def test_holm_family_actual_values():
    # The real V2 family: H1=.0002, H2=.98875, H3=.0065, H5=.2501 (input order H1,H2,H3,H5).
    adj, rej = hf.holm([0.0002, 0.98875, 0.0065, 0.2501], alpha=0.05)
    assert adj[0] == pytest.approx(0.0008)  # H1
    assert adj[2] == pytest.approx(0.0195)  # H3
    assert adj[3] == pytest.approx(0.5002)  # H5
    assert adj[1] == pytest.approx(0.98875)  # H2
    assert rej == [True, False, True, False]  # H1,H3 significant; H2,H5 not


# ── per-hypothesis extractors ──────────────────────────────────────────


def test_h1_input_sub_sesoi():
    doc = {
        "pageTrendTest": {"p_one_sided": 0.0002},
        "sesoi": {"meetsSesoi": False, "pctChange": 13.35, "sesoiPct": 15.0},
    }
    p, bar, note = hf.h1_input(doc)
    assert p == 0.0002 and bar is False and "sub-SESOI" in note


def test_h1_input_meets_sesoi():
    doc = {
        "pageTrendTest": {"p_one_sided": 0.0002},
        "sesoi": {"meetsSesoi": True, "pctChange": 20.0, "sesoiPct": 15.0},
    }
    _, bar, note = hf.h1_input(doc)
    assert bar is True and "meets" in note


def test_h2_input():
    p, bar, _ = hf.h2_input({"familyInputMaxP": 0.98875, "conjunction": False})
    assert p == 0.98875 and bar is False


def test_h3_input_takes_max_of_coprimaries():
    doc = {
        "troughDepthFraction": {"artInteraction": {"p": 0.0065}},
        "userErrorRate": {"artInteraction": {"p": 0.0}},
        "conjunctionRescue": False,
    }
    p, bar, _ = hf.h3_input(doc)
    assert p == 0.0065 and bar is False


def test_h5_input():
    p, bar, _ = hf.h5_input({"decision": {"holmInput": 0.2501, "conjunctionPass": False}})
    assert p == 0.2501 and bar is False


def test_get_raises_with_path_on_miss():
    with pytest.raises(KeyError, match="pageTrendTest"):
        hf.h1_input({"sesoi": {}})


# ── analyze() / supported logic ────────────────────────────────────────


def _family_docs(tmp_path, h1_meets=False, h3_conj=False):
    import json

    docs = {
        "V2-H1": {
            "pageTrendTest": {"p_one_sided": 0.0002},
            "sesoi": {"meetsSesoi": h1_meets, "pctChange": 13.35, "sesoiPct": 15.0},
        },
        "V2-H2": {"familyInputMaxP": 0.98875, "conjunction": False},
        "V2-H3": {
            "troughDepthFraction": {"artInteraction": {"p": 0.0065}},
            "userErrorRate": {"artInteraction": {"p": 0.0}},
            "conjunctionRescue": h3_conj,
        },
        "V2-H5": {"decision": {"holmInput": 0.2501, "conjunctionPass": False}},
    }
    paths = {}
    for hyp, doc in docs.items():
        p = tmp_path / f"{hyp}.json"
        p.write_text(json.dumps(doc))
        paths[hyp] = str(p)
    return paths


def test_analyze_none_supported(tmp_path):
    res = hf.analyze(_family_docs(tmp_path))
    by = {r["hyp"]: r for r in res["members"]}
    assert by["V2-H1"]["holmSignificant"] is True and by["V2-H1"]["supported"] is False  # sub-SESOI
    assert by["V2-H3"]["holmSignificant"] is True and by["V2-H3"]["supported"] is False  # margin
    assert by["V2-H2"]["holmSignificant"] is False and by["V2-H2"]["supported"] is False
    assert by["V2-H5"]["holmSignificant"] is False and by["V2-H5"]["supported"] is False
    assert res["anySupported"] is False


def test_analyze_support_requires_both_sig_and_bar(tmp_path):
    # If H3's rescue conjunction passed, its significant interaction would make it supported.
    res = hf.analyze(_family_docs(tmp_path, h3_conj=True))
    by = {r["hyp"]: r for r in res["members"]}
    assert by["V2-H3"]["supported"] is True
    assert res["anySupported"] is True


def test_analyze_bar_without_significance_not_supported(tmp_path):
    # H1 meeting SESOI but staying significant -> supported; flip: bar met, not sig.
    import json

    paths = _family_docs(tmp_path)
    # Force H1 p high so it's not Holm-significant, but SESOI met.
    doc = {
        "pageTrendTest": {"p_one_sided": 0.9},
        "sesoi": {"meetsSesoi": True, "pctChange": 20.0, "sesoiPct": 15.0},
    }
    (tmp_path / "V2-H1.json").write_text(json.dumps(doc))
    res = hf.analyze(paths)
    by = {r["hyp"]: r for r in res["members"]}
    assert by["V2-H1"]["barMet"] is True
    assert by["V2-H1"]["holmSignificant"] is False
    assert by["V2-H1"]["supported"] is False


def test_render_contains_verdict(tmp_path):
    res = hf.analyze(_family_docs(tmp_path))
    out = hf.render(res)
    assert "NO confirmatory hypothesis is supported" in out
    assert "V2-H1" in out and "V2-H5" in out
