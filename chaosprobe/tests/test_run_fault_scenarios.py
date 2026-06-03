"""Unit tests for the fault-matrix builder extracted from ``run``.

The primary-reuse, filename-stem labeling, and deploy=False-for-additional rules
were inline in the ~440-line ``run`` command.
"""

from chaosprobe.commands import run_cmd
from chaosprobe.commands.run_cmd import _build_fault_scenarios, _collect_built_images


def _exp(probes):
    """Build a scenario ``experiments`` entry from ``(name, image)`` probes.

    ``image=None`` makes an httpProbe (no source image); any string makes a
    cmdProbe with that ``source.image`` — mirroring the nesting the scenario
    loader produces (``spec.spec.experiments[].spec.probe[]``).
    """
    probe_specs = []
    for name, image in probes:
        if image is None:
            probe_specs.append({"name": name, "type": "httpProbe"})
        else:
            probe_specs.append(
                {
                    "name": name,
                    "type": "cmdProbe",
                    "cmdProbe/inputs": {"source": {"image": image}},
                }
            )
    return {"spec": {"spec": {"experiments": [{"spec": {"probe": probe_specs}}]}}}


def _probe_image(scenario):
    """Pull the first cmdProbe's resolved ``source.image`` back out."""
    probe = scenario["experiments"][0]["spec"]["spec"]["experiments"][0]["spec"]["probe"][0]
    return probe["cmdProbe/inputs"]["source"]["image"]


def test_single_experiment_reuses_primary_without_reloading(monkeypatch):
    monkeypatch.setattr(run_cmd, "extract_experiment_types", lambda scn: scn.get("types", []))
    loads = []
    monkeypatch.setattr(
        run_cmd,
        "_load_and_prepare_scenario",
        lambda *a, **k: loads.append(a) or ({}, "ns", "f", {}),
    )
    shared = {"types": ["pod-delete"]}

    fs = _build_fault_scenarios(
        ("/x/placement-experiment.yaml",), "/x/placement-experiment.yaml", shared, "demo"
    )

    assert fs == [("placement-experiment", shared, ["pod-delete"])]
    assert loads == []  # primary reused; no extra scenario load


def test_additional_experiments_loaded_without_redeploy(monkeypatch):
    monkeypatch.setattr(run_cmd, "extract_experiment_types", lambda scn: scn.get("types", []))
    add_scn = {"types": ["pod-cpu-hog"]}

    def fake_load(path, ns, deploy=True):
        assert deploy is False  # additional scenarios must not redeploy
        return add_scn, ns, "f2", {}

    monkeypatch.setattr(run_cmd, "_load_and_prepare_scenario", fake_load)
    shared = {"types": ["pod-delete"]}

    fs = _build_fault_scenarios(
        ("/x/placement-experiment.yaml", "/x/cpuhog.yaml"),
        "/x/placement-experiment.yaml",
        shared,
        "demo",
    )

    assert fs == [
        ("placement-experiment", shared, ["pod-delete"]),
        ("cpuhog", add_scn, ["pod-cpu-hog"]),
    ]


class TestCollectBuiltImages:
    def test_maps_resolved_cmdprobe_images_by_name(self):
        exps = [_exp([("check-redis", "reg:5000/check-redis:abc")])]
        assert _collect_built_images(exps) == {"check-redis": "reg:5000/check-redis:abc"}

    def test_skips_placeholders_and_non_cmdprobes(self):
        exps = [
            _exp(
                [
                    ("check-redis", "reg:5000/check-redis:abc"),
                    ("check-http", "auto"),  # unresolved placeholder
                    ("frontend-healthz", None),  # httpProbe, no image
                ]
            )
        ]
        assert _collect_built_images(exps) == {"check-redis": "reg:5000/check-redis:abc"}

    def test_empty_when_no_experiments(self):
        assert _collect_built_images([]) == {}


def test_secondary_scenario_inherits_primary_probe_images(monkeypatch):
    # Regression: in a multi-fault run the secondary scenario is loaded with
    # deploy=False and so never builds/patches its own probes — its image: auto
    # placeholders must inherit the primary's already-resolved registry images,
    # otherwise the cmdProbe pods ImagePullBackOff and the whole fault errors.
    monkeypatch.setattr(run_cmd, "extract_experiment_types", lambda scn: scn.get("types", []))
    primary = {
        "types": ["pod-delete"],
        "experiments": [_exp([("check-redis", "reg:5000/check-redis:abc")])],
    }
    secondary = {"types": ["pod-cpu-hog"], "experiments": [_exp([("check-redis", "auto")])]}
    monkeypatch.setattr(
        run_cmd, "_load_and_prepare_scenario", lambda *a, **k: (secondary, "ns", "f2", {})
    )

    _build_fault_scenarios(
        ("/x/placement-experiment.yaml", "/x/cpuhog.yaml"),
        "/x/placement-experiment.yaml",
        primary,
        "demo",
    )

    assert _probe_image(secondary) == "reg:5000/check-redis:abc"
