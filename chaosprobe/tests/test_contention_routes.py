"""Tests for scripts/contention_routes.py — during-load route-tail comparison."""

import importlib.util
import json
from pathlib import Path

import pytest

# scripts/ is not a package; load the module by path (mirrors test_archive_run.py).
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "contention_routes.py"
_spec = importlib.util.spec_from_file_location("contention_routes", _SCRIPT)
assert _spec is not None and _spec.loader is not None
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)


def _entry(route, prober_p95, locust_p95=None):
    return {
        "route": route,
        "iterations": 4,
        "locust": {"meanP95_ms": locust_p95} if locust_p95 is not None else {},
        "latencyProber": {"during-chaos": {"meanP95_ms": prober_p95}},
    }


def _summary(single_fault=True):
    rva_colo = [
        _entry("/", 800.0),
        _entry("/product/X", 500.0),
        _entry("/_healthz", 100.0),
        _entry("frontend->checkoutservice", 30.0),
    ]
    rva_spread = [
        _entry("/", 1600.0),  # 2.0x
        _entry("/product/X", 1250.0),  # 2.5x
        _entry("/_healthz", 130.0),  # 1.3x (control, smaller)
        _entry("frontend->checkoutservice", 45.0),  # 1.5x
    ]
    strats = {
        "colocate": {"aggregated": {"routeViewAggregate": rva_colo}},
        "spread": {"aggregated": {"routeViewAggregate": rva_spread}},
    }
    return {"strategies": strats} if single_fault else {"faults": {"load": {"strategies": strats}}}


def test_strategies_single_and_multi_fault_shapes():
    assert set(cr._strategies(_summary(single_fault=True))) == {"colocate", "spread"}
    assert set(cr._strategies(_summary(single_fault=False))) == {"colocate", "spread"}


def test_during_p95_reads_canonical_field():
    rva = cr._strategies(_summary())["spread"]
    p = cr._during_p95(rva)
    assert p["/"]["prober"] == 1600.0
    assert p["frontend->checkoutservice"]["prober"] == 45.0


def test_during_p95_skips_entries_without_route():
    out = cr._during_p95(
        [
            {"latencyProber": {"during-chaos": {"meanP95_ms": 5.0}}},  # no "route" → skipped
            {"route": "/x", "latencyProber": {"during-chaos": {"meanP95_ms": 9.0}}},
        ]
    )
    assert out == {"/x": {"prober": 9.0, "locust": None}}


@pytest.mark.parametrize(
    "route, ns, ew, dep",
    [
        ("/", True, False, True),
        ("/product/X", True, False, True),
        ("/_healthz", True, False, False),
        ("frontend->checkoutservice", False, True, False),
    ],
)
def test_route_classification(route, ns, ew, dep):
    assert cr._is_ns(route) is ns
    assert cr._is_ew(route) is ew
    assert cr._is_dependent_ns(route) is dep


def test_ratio_guards_zero_and_none():
    assert cr._ratio(100.0, 250.0) == 2.5
    assert cr._ratio(None, 250.0) is None
    assert cr._ratio(0.0, 250.0) is None


def test_report_propagation_verdict_and_numbers(capsys):
    cr.report(_summary(), ("colocate", "spread"), None)
    out = capsys.readouterr().out
    # dependent routes (/, /product) median ratio = median(2.0, 2.5) = 2.25 > control 1.30
    assert "2.25" in out
    assert "1.30" in out  # control ratio
    assert "propagates to the user layer" in out


def test_report_missing_strategy(capsys):
    cr.report(_summary(), ("colocate", "nonexistent"), None)
    assert "not both present" in capsys.readouterr().out


def test_report_no_propagation_verdict(capsys):
    # Make the control route degrade MORE than the dependent routes.
    s = _summary()
    for e in s["strategies"]["spread"]["aggregated"]["routeViewAggregate"]:
        if e["route"] == "/_healthz":
            e["latencyProber"]["during-chaos"]["meanP95_ms"] = 1000.0  # 10x control
    cr.report(s, ("colocate", "spread"), None)
    assert "effect not specific to the dependency path" in capsys.readouterr().out


def test_main_with_csv(tmp_path, monkeypatch, capsys):
    sfile = tmp_path / "summary.json"
    sfile.write_text(json.dumps(_summary()))
    csv_out = tmp_path / "routes.csv"
    monkeypatch.setattr(
        "sys.argv", ["contention_routes.py", "-s", str(sfile), "--csv", str(csv_out)]
    )
    cr.main()
    assert csv_out.exists()
    assert "ratio" in csv_out.read_text()
    assert "wrote" in capsys.readouterr().out
