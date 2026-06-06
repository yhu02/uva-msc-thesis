"""Tests for the ``chaosprobe doctor`` data-quality CLI command."""

import json
from pathlib import Path

from click.testing import CliRunner

from chaosprobe.commands.doctor_cmd import (
    _check_cross_strategy,
    _check_run_metadata,
    _check_schema_version,
    _check_strategy,
    doctor,
)
from chaosprobe.output import SCHEMA_VERSION


def _write_summary(
    tmp_path: Path,
    strategies: dict,
    include_run_metadata: bool = True,
    include_schema_version: bool = True,
) -> Path:
    """Helper.  Includes a complete ``runMetadata`` block and current
    ``schemaVersion`` by default so legacy doctor tests don't trip the
    metadata / schema-version checks (#85, #86)."""
    from chaosprobe.output import SCHEMA_VERSION as _SCHEMA_VERSION

    path = tmp_path / "summary.json"
    payload: dict = {"strategies": strategies}
    if include_schema_version:
        payload["schemaVersion"] = _SCHEMA_VERSION
    if include_run_metadata:
        payload["runMetadata"] = {
            "git": {"commit": "abc123", "shortCommit": "abc123", "dirty": False},
            "kubernetes": {
                "serverVersion": "v1.28.6",
                "containerRuntimeOnFirstNode": "containerd",
            },
            "cniHint": "calico",
            "kubeProxy": {"mode": "iptables", "conntrack": {"min": 131072}},
        }
    path.write_text(json.dumps(payload))
    return path


class TestCheckStrategy:
    def test_clean_strategy_no_issues(self):
        sdata = {
            "iterations": [{"resilienceScore": 80}] * 5,
            "aggregated": {
                "taintedIterations": 0,
                "errors": 0,
                "meanRecoveryTime_ms": 1000,
            },
        }
        assert _check_strategy("colocate", sdata) == []

    def test_tainted_iterations_warned(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {
                "taintedIterations": 2,
                "taintReasonCounts": {"pre_chaos_errors_high": 2},
                "meanRecoveryTime_ms": 1000,
            },
        }
        issues = _check_strategy("colocate", sdata)
        sevs = [s for s, _ in issues]
        msgs = [m for _, m in issues]
        assert "warn" in sevs
        assert any("2/5 iteration(s) tainted" in m for m in msgs)
        assert any("pre_chaos_errors_high" in m for m in msgs)

    def test_all_iterations_tainted_is_error(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {
                "taintedIterations": 5,
                "allIterationsTainted": True,
                "meanRecoveryTime_ms": 1000,
            },
        }
        issues = _check_strategy("colocate", sdata)
        sevs = [s for s, _ in issues]
        assert "error" in sevs

    def test_error_iterations_warned(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {"errors": 1, "meanRecoveryTime_ms": 1000},
        }
        issues = _check_strategy("colocate", sdata)
        assert any("1/5 iteration(s) errored" in msg for _, msg in issues)

    def test_low_placement_match_rate_warned(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {"meanRecoveryTime_ms": 1000},
            "placement": {
                "metadata": {
                    "intendedActualDiff": {
                        "matchRate": 0.85,
                        "mismatched": [{"deployment": "frontend"}],
                    }
                }
            },
        }
        issues = _check_strategy("colocate", sdata)
        sevs_msgs = [(s, m) for s, m in issues if "match rate" in m]
        assert sevs_msgs
        assert sevs_msgs[0][0] == "warn"

    def test_very_low_placement_match_rate_is_error(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {"meanRecoveryTime_ms": 1000},
            "placement": {
                "metadata": {
                    "intendedActualDiff": {
                        "matchRate": 0.5,
                        "mismatched": [{}, {}, {}],
                    }
                }
            },
        }
        issues = _check_strategy("colocate", sdata)
        sevs_msgs = [(s, m) for s, m in issues if "match rate" in m]
        assert sevs_msgs[0][0] == "error"

    def test_oomkills_warned(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {
                "meanRecoveryTime_ms": 1000,
                "totalOOMKills": 3,
                "iterationsWithOOMKills": 2,
            },
        }
        issues = _check_strategy("colocate", sdata)
        assert any("OOMKill" in msg for _, msg in issues)

    def test_node_pressure_fired_warned(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {
                "meanRecoveryTime_ms": 1000,
                "nodePressureEvents": {
                    "MemoryPressure": {"iterationsWithEvent": 3, "totalNodeEvents": 5},
                    "DiskPressure": {"iterationsWithEvent": 0, "totalNodeEvents": 0},
                },
            },
        }
        issues = _check_strategy("colocate", sdata)
        assert any("MemoryPressure" in msg and "DiskPressure" not in msg for _, msg in issues)

    def test_missing_recovery_warned(self):
        sdata = {
            "iterations": [{}] * 5,
            "aggregated": {"meanRecoveryTime_ms": None},
        }
        issues = _check_strategy("colocate", sdata)
        assert any("no recovery times" in msg for _, msg in issues)

    def test_low_n_warned(self):
        sdata = {
            "iterations": [{}] * 2,
            "aggregated": {"meanRecoveryTime_ms": 1000},
        }
        issues = _check_strategy("colocate", sdata)
        assert any("only 2 iteration" in msg for _, msg in issues)


class TestDoctorCommand:
    def test_clean_summary_reports_no_issues(self, tmp_path):
        path = _write_summary(
            tmp_path,
            {
                "colocate": {
                    "iterations": [{}] * 5,
                    "aggregated": {"meanRecoveryTime_ms": 1000},
                }
            },
        )
        runner = CliRunner()
        result = runner.invoke(doctor, ["-s", str(path)])
        assert result.exit_code == 0
        assert "no issues" in result.output

    def test_summary_with_warnings_exits_zero_without_strict(self, tmp_path):
        path = _write_summary(
            tmp_path,
            {
                "colocate": {
                    "iterations": [{}] * 5,
                    "aggregated": {
                        "meanRecoveryTime_ms": 1000,
                        "totalOOMKills": 1,
                        "iterationsWithOOMKills": 1,
                    },
                }
            },
        )
        runner = CliRunner()
        result = runner.invoke(doctor, ["-s", str(path)])
        assert result.exit_code == 0
        assert "OOMKill" in result.output

    def test_summary_with_warnings_exits_one_in_strict_mode(self, tmp_path):
        path = _write_summary(
            tmp_path,
            {
                "colocate": {
                    "iterations": [{}] * 5,
                    "aggregated": {
                        "meanRecoveryTime_ms": 1000,
                        "totalOOMKills": 1,
                        "iterationsWithOOMKills": 1,
                    },
                }
            },
        )
        runner = CliRunner()
        result = runner.invoke(doctor, ["-s", str(path), "--strict"])
        assert result.exit_code == 1

    def test_summary_with_errors_exits_one(self, tmp_path):
        path = _write_summary(
            tmp_path,
            {
                "colocate": {
                    "iterations": [{}] * 5,
                    "aggregated": {
                        "allIterationsTainted": True,
                        "taintedIterations": 5,
                        "meanRecoveryTime_ms": 1000,
                    },
                }
            },
        )
        runner = CliRunner()
        result = runner.invoke(doctor, ["-s", str(path)])
        assert result.exit_code == 1
        assert "every iteration was tainted" in result.output

    def test_json_output(self, tmp_path):
        path = _write_summary(
            tmp_path,
            {
                "colocate": {
                    "iterations": [{}] * 5,
                    "aggregated": {
                        "meanRecoveryTime_ms": 1000,
                        "totalOOMKills": 1,
                        "iterationsWithOOMKills": 1,
                    },
                }
            },
        )
        runner = CliRunner()
        result = runner.invoke(doctor, ["-s", str(path), "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["strategiesChecked"] == 1
        assert payload["warnCount"] >= 1
        assert "colocate" in payload["findings"]

    def test_per_strategy_summary_lines(self, tmp_path):
        path = _write_summary(
            tmp_path,
            {
                "colocate": {
                    "iterations": [{}] * 5,
                    "aggregated": {
                        "meanRecoveryTime_ms": 1000,
                        "totalOOMKills": 2,
                        "iterationsWithOOMKills": 1,
                    },
                },
                "spread": {
                    "iterations": [{}] * 5,
                    "aggregated": {"meanRecoveryTime_ms": 1000},
                },
            },
        )
        runner = CliRunner()
        result = runner.invoke(doctor, ["-s", str(path)])
        assert result.exit_code == 0
        # Only colocate has issues; spread should not have a header.
        assert "colocate" in result.output
        assert "spread" not in result.output


def _strategy_with_ci(low, high):
    return {
        "iterations": [{}] * 5,
        "aggregated": {
            "meanRecoveryTime_ms": 1000,
            "meanResilienceScore_ci95": {"low": low, "high": high, "n": 5},
        },
    }


class TestCrossStrategyChecks:
    def test_all_overlapping_cis_warned(self):
        strategies = {
            "a": _strategy_with_ci(40, 60),
            "b": _strategy_with_ci(45, 65),
            "c": _strategy_with_ci(50, 70),
        }
        issues = _check_cross_strategy(strategies)
        assert any("statistically inconclusive" in msg for _, msg in issues)

    def test_disjoint_cis_no_warning(self):
        strategies = {
            "a": _strategy_with_ci(10, 20),
            "b": _strategy_with_ci(50, 60),
        }
        issues = _check_cross_strategy(strategies)
        assert not any("statistically inconclusive" in msg for _, msg in issues)

    def test_single_strategy_no_cross_checks(self):
        strategies = {"a": _strategy_with_ci(40, 60)}
        assert _check_cross_strategy(strategies) == []

    def test_all_strategies_oom_warned(self):
        strategies = {
            name: {
                "iterations": [{}] * 5,
                "aggregated": {
                    "meanRecoveryTime_ms": 1000,
                    "totalOOMKills": 1,
                },
            }
            for name in ("a", "b", "c")
        }
        issues = _check_cross_strategy(strategies)
        assert any("every strategy hit OOMKills" in msg for _, msg in issues)

    def test_one_strategy_oom_not_warned(self):
        strategies = {
            "a": {
                "iterations": [{}] * 5,
                "aggregated": {"meanRecoveryTime_ms": 1000, "totalOOMKills": 5},
            },
            "b": {
                "iterations": [{}] * 5,
                "aggregated": {"meanRecoveryTime_ms": 1000, "totalOOMKills": 0},
            },
            "c": {
                "iterations": [{}] * 5,
                "aggregated": {"meanRecoveryTime_ms": 1000, "totalOOMKills": 0},
            },
        }
        issues = _check_cross_strategy(strategies)
        assert not any("every strategy hit OOMKills" in msg for _, msg in issues)

    def test_all_tainted_warned(self):
        strategies = {
            name: {
                "iterations": [{}] * 5,
                "aggregated": {"meanRecoveryTime_ms": 1000, "taintedIterations": 1},
            }
            for name in ("a", "b", "c")
        }
        issues = _check_cross_strategy(strategies)
        assert any("cluster is unstable" in msg for _, msg in issues)

    def test_rps_skew_warned(self):
        strategies = {
            "a": {
                "iterations": [{}] * 5,
                "aggregated": {
                    "meanRecoveryTime_ms": 1000,
                    "loadGenerationAggregate": {"meanRequestsPerSecond": 10.0},
                },
            },
            "b": {
                "iterations": [{}] * 5,
                "aggregated": {
                    "meanRecoveryTime_ms": 1000,
                    "loadGenerationAggregate": {"meanRequestsPerSecond": 20.0},
                },
            },
        }
        issues = _check_cross_strategy(strategies)
        assert any("Locust offered RPS varies" in msg for _, msg in issues)

    def test_small_rps_skew_not_warned(self):
        strategies = {
            "a": {
                "iterations": [{}] * 5,
                "aggregated": {
                    "meanRecoveryTime_ms": 1000,
                    "loadGenerationAggregate": {"meanRequestsPerSecond": 10.0},
                },
            },
            "b": {
                "iterations": [{}] * 5,
                "aggregated": {
                    "meanRecoveryTime_ms": 1000,
                    "loadGenerationAggregate": {"meanRequestsPerSecond": 10.5},
                },
            },
        }
        issues = _check_cross_strategy(strategies)
        assert not any("Locust offered RPS varies" in msg for _, msg in issues)


class TestDoctorCrossStrategyIntegration:
    def test_cross_strategy_section_in_text_output(self, tmp_path):
        path = _write_summary(
            tmp_path,
            {
                "a": _strategy_with_ci(40, 60),
                "b": _strategy_with_ci(45, 65),
            },
        )
        runner = CliRunner()
        result = runner.invoke(doctor, ["-s", str(path)])
        assert result.exit_code == 0
        assert "cross-strategy" in result.output

    def test_cross_strategy_section_in_json_output(self, tmp_path):
        path = _write_summary(
            tmp_path,
            {
                "a": _strategy_with_ci(40, 60),
                "b": _strategy_with_ci(45, 65),
            },
        )
        runner = CliRunner()
        result = runner.invoke(doctor, ["-s", str(path), "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "__cross_strategy__" in payload["findings"]


class TestCheckRunMetadata:
    def test_missing_run_metadata_warned(self):
        issues = _check_run_metadata({"strategies": {}})
        sevs_msgs = [(s, m) for s, m in issues if "runMetadata absent" in m]
        assert sevs_msgs
        assert sevs_msgs[0][0] == "warn"

    def test_complete_metadata_no_issues(self):
        raw = {
            "runMetadata": {
                "git": {"commit": "abc123", "shortCommit": "abc12", "dirty": False},
                "kubernetes": {
                    "serverVersion": "v1.28.6",
                    "containerRuntimeOnFirstNode": "containerd",
                },
                "cniHint": "calico",
                "kubeProxy": {"mode": "iptables", "conntrack": {"min": 131072}},
            }
        }
        assert _check_run_metadata(raw) == []

    def test_missing_kube_proxy_mode_warned(self):
        raw = {
            "runMetadata": {
                "git": {"commit": "abc", "dirty": False},
                "kubernetes": {"serverVersion": "v1.28"},
                "cniHint": "calico",
                "kubeProxy": {"mode": None, "conntrack": None},
            }
        }
        issues = _check_run_metadata(raw)
        assert any("kube-proxy mode not recorded" in msg for _, msg in issues)

    def test_absent_kube_proxy_block_warned(self):
        # An older summary without the kubeProxy block at all still trips
        # the mode check (md.get returns {} → mode is None).
        raw = {
            "runMetadata": {
                "git": {"commit": "abc", "dirty": False},
                "kubernetes": {"serverVersion": "v1.28"},
                "cniHint": "calico",
            }
        }
        issues = _check_run_metadata(raw)
        assert any("kube-proxy mode not recorded" in msg for _, msg in issues)

    def test_dirty_working_tree_warned(self):
        raw = {
            "runMetadata": {
                "git": {"commit": "abc123", "shortCommit": "abc12345", "dirty": True},
                "kubernetes": {"serverVersion": "v1.28.6"},
                "cniHint": "calico",
            }
        }
        issues = _check_run_metadata(raw)
        assert any("dirty working tree" in msg for _, msg in issues)
        assert any("abc12345" in msg for _, msg in issues)

    def test_missing_git_commit_warned(self):
        raw = {
            "runMetadata": {
                "git": {"commit": None, "dirty": False},
                "kubernetes": {"serverVersion": "v1.28"},
                "cniHint": "calico",
            }
        }
        issues = _check_run_metadata(raw)
        assert any("git commit not recorded" in msg for _, msg in issues)

    def test_missing_k8s_version_warned(self):
        raw = {
            "runMetadata": {
                "git": {"commit": "abc", "dirty": False},
                "kubernetes": {"serverVersion": None},
                "cniHint": "calico",
            }
        }
        issues = _check_run_metadata(raw)
        assert any("Kubernetes server version not recorded" in msg for _, msg in issues)

    def test_missing_cni_hint_warned(self):
        raw = {
            "runMetadata": {
                "git": {"commit": "abc", "dirty": False},
                "kubernetes": {"serverVersion": "v1.28"},
                "cniHint": None,
            }
        }
        issues = _check_run_metadata(raw)
        assert any("CNI hint not recorded" in msg for _, msg in issues)


class TestDoctorRunMetadataIntegration:
    def test_run_metadata_section_in_text_output(self, tmp_path):
        path = tmp_path / "summary.json"
        path.write_text(
            json.dumps(
                {
                    "strategies": {
                        "colocate": {
                            "iterations": [{}] * 5,
                            "aggregated": {"meanRecoveryTime_ms": 1000},
                        }
                    }
                    # No runMetadata.
                }
            )
        )
        runner = CliRunner()
        result = runner.invoke(doctor, ["-s", str(path)])
        assert result.exit_code == 0
        assert "run metadata" in result.output
        assert "runMetadata absent" in result.output

    def test_run_metadata_section_in_json_output(self, tmp_path):
        path = tmp_path / "summary.json"
        path.write_text(
            json.dumps(
                {
                    "strategies": {
                        "colocate": {
                            "iterations": [{}] * 5,
                            "aggregated": {"meanRecoveryTime_ms": 1000},
                        }
                    }
                }
            )
        )
        runner = CliRunner()
        result = runner.invoke(doctor, ["-s", str(path), "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "__run_metadata__" in payload["findings"]


class TestCheckSchemaVersion:
    def test_missing_schema_version_warned(self):
        issues = _check_schema_version({})
        assert any("schemaVersion missing" in msg for _, msg in issues)

    def test_current_schema_version_no_issues(self):
        assert _check_schema_version({"schemaVersion": SCHEMA_VERSION}) == []

    def test_mismatched_version_warned(self):
        issues = _check_schema_version({"schemaVersion": "1.0.0"})
        assert any("differs from current" in msg for _, msg in issues)


class TestDoctorSchemaVersionIntegration:
    def test_schema_version_in_text_output(self, tmp_path):
        # Build a summary that has runMetadata + good strategy, but no
        # schemaVersion → only the schema warning fires.
        path = tmp_path / "summary.json"
        path.write_text(
            json.dumps(
                {
                    "strategies": {
                        "colocate": {
                            "iterations": [{}] * 5,
                            "aggregated": {"meanRecoveryTime_ms": 1000},
                        }
                    },
                    "runMetadata": {
                        "git": {"commit": "abc", "shortCommit": "abc", "dirty": False},
                        "kubernetes": {"serverVersion": "v1.28"},
                        "cniHint": "calico",
                    },
                    # No schemaVersion.
                }
            )
        )
        runner = CliRunner()
        result = runner.invoke(doctor, ["-s", str(path)])
        assert result.exit_code == 0
        assert "schema version" in result.output
        assert "schemaVersion missing" in result.output

    def test_current_schema_version_doesnt_fire(self, tmp_path):
        path = tmp_path / "summary.json"
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "strategies": {
                        "colocate": {
                            "iterations": [{}] * 5,
                            "aggregated": {"meanRecoveryTime_ms": 1000},
                        }
                    },
                    "runMetadata": {
                        "git": {"commit": "abc", "shortCommit": "abc", "dirty": False},
                        "kubernetes": {"serverVersion": "v1.28"},
                        "cniHint": "calico",
                    },
                }
            )
        )
        runner = CliRunner()
        result = runner.invoke(doctor, ["-s", str(path)])
        assert result.exit_code == 0
        assert "schemaVersion" not in result.output
