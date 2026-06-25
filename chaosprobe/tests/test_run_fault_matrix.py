"""Unit tests for the fault-matrix aggregation helpers extracted from ``run``.

The experiment-type union and the cmdProbe-image union were inline loops in the
~440-line ``run`` Click command, so they had no unit coverage. Extracting them
to module scope makes the order-preserving dedup directly testable.
"""

from chaosprobe.commands import run_cmd
from chaosprobe.commands.run_cmd import _collect_experiment_types, _unique_probe_images


def _fs(label, types, experiments=None):
    """A (label, scenario_dict, fault_types) triple as ``run`` builds them."""
    return (label, {"experiments": experiments or []}, types)


class TestCollectExperimentTypes:
    def test_dedups_union_across_scenarios_in_order(self):
        fs = [
            _fs("a", ["pod-delete", "pod-cpu-hog"]),
            _fs("b", ["pod-cpu-hog", "pod-network-loss"]),
        ]
        assert _collect_experiment_types(fs, ["default"]) == [
            "pod-delete",
            "pod-cpu-hog",
            "pod-network-loss",
        ]

    def test_baseline_appends_pod_cpu_hog(self):
        fs = [_fs("a", ["pod-delete"])]
        assert _collect_experiment_types(fs, ["baseline", "default"]) == [
            "pod-delete",
            "pod-cpu-hog",
        ]

    def test_baseline_does_not_double_add(self):
        fs = [_fs("a", ["pod-cpu-hog"])]
        assert _collect_experiment_types(fs, ["baseline"]) == ["pod-cpu-hog"]

    def test_no_baseline_no_cpu_hog_added(self):
        fs = [_fs("a", ["pod-delete"])]
        assert _collect_experiment_types(fs, ["default", "spread"]) == ["pod-delete"]


class TestUniqueProbeImages:
    def test_dedups_union_in_order(self, monkeypatch):
        # Treat each scenario's "experiments" value as its image list directly.
        monkeypatch.setattr(run_cmd, "extract_cmdprobe_images", lambda experiments: experiments)
        fs = [
            _fs("a", [], experiments=["img1", "img2"]),
            _fs("b", [], experiments=["img2", "img3"]),
        ]
        assert _unique_probe_images(fs) == ["img1", "img2", "img3"]

    def test_empty_when_no_images(self, monkeypatch):
        monkeypatch.setattr(run_cmd, "extract_cmdprobe_images", lambda experiments: [])
        assert _unique_probe_images([_fs("a", [])]) == []
