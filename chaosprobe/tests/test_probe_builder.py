"""Tests for the Rust cmdProbe builder pipeline."""

import textwrap
from unittest.mock import MagicMock, patch

import pytest

from chaosprobe.probes.builder import (
    MUSL_TARGET,
    ProbeBuilderError,
    RustProbeBuilder,
    _require_tool,
    _run_cmd,
    patch_probe_images,
)
from chaosprobe.probes.templates import (
    generate_cargo_toml,
    generate_dockerfile,
    generate_main_rs,
    generate_single_file_rs,
)

# ── Template generation tests ────────────────────────────────


class TestTemplates:
    def test_cargo_toml_contains_name(self):
        toml = generate_cargo_toml("check-db")
        assert 'name = "check-db"' in toml
        assert "edition" in toml
        assert "[[bin]]" in toml

    def test_cargo_toml_sanitises_spaces(self):
        toml = generate_cargo_toml("my probe")
        assert 'name = "my-probe"' in toml

    def test_main_rs_contains_probe_name(self):
        rs = generate_main_rs("check-db")
        assert "check-db" in rs
        assert "fn main()" in rs
        assert "fn run_check()" in rs

    def test_single_file_rs(self):
        rs = generate_single_file_rs("health")
        assert "health" in rs
        assert "fn main()" in rs
        assert "fn run_check()" in rs

    def test_dockerfile_scratch_base(self):
        df = generate_dockerfile("myprobe")
        assert "FROM busybox:stable-musl" in df
        assert "COPY myprobe /probe/myprobe" in df
        assert 'ENTRYPOINT ["/probe/myprobe"]' in df


# ── Discovery tests ──────────────────────────────────────────


class TestDiscovery:
    def test_discover_no_probes_dir(self, tmp_path):
        probes = RustProbeBuilder.discover_probes(str(tmp_path))
        assert probes == []

    def test_discover_empty_probes_dir(self, tmp_path):
        (tmp_path / "probes").mkdir()
        probes = RustProbeBuilder.discover_probes(str(tmp_path))
        assert probes == []

    def test_discover_single_file(self, tmp_path):
        probes_dir = tmp_path / "probes"
        probes_dir.mkdir()
        (probes_dir / "health.rs").write_text("fn main() {}")

        probes = RustProbeBuilder.discover_probes(str(tmp_path))
        assert len(probes) == 1
        assert probes[0]["name"] == "health"
        assert probes[0]["kind"] == "single_file"
        assert probes[0]["path"] == str(probes_dir / "health.rs")

    def test_discover_cargo_project(self, tmp_path):
        probes_dir = tmp_path / "probes" / "check-db"
        probes_dir.mkdir(parents=True)
        (probes_dir / "Cargo.toml").write_text("[package]\nname = 'check-db'")
        (probes_dir / "src").mkdir()
        (probes_dir / "src" / "main.rs").write_text("fn main() {}")

        probes = RustProbeBuilder.discover_probes(str(tmp_path))
        assert len(probes) == 1
        assert probes[0]["name"] == "check-db"
        assert probes[0]["kind"] == "cargo"

    def test_discover_mixed(self, tmp_path):
        probes_dir = tmp_path / "probes"
        probes_dir.mkdir()

        # Single file
        (probes_dir / "alpha.rs").write_text("fn main() {}")

        # Cargo project
        cargo = probes_dir / "beta"
        cargo.mkdir()
        (cargo / "Cargo.toml").write_text("[package]")

        probes = RustProbeBuilder.discover_probes(str(tmp_path))
        assert len(probes) == 2
        names = {p["name"] for p in probes}
        assert names == {"alpha", "beta"}

    def test_discover_ignores_dirs_without_cargo_toml(self, tmp_path):
        probes_dir = tmp_path / "probes" / "noproject"
        probes_dir.mkdir(parents=True)
        (probes_dir / "main.rs").write_text("fn main() {}")

        probes = RustProbeBuilder.discover_probes(str(tmp_path))
        assert probes == []

    def test_discover_sorted_order(self, tmp_path):
        probes_dir = tmp_path / "probes"
        probes_dir.mkdir()
        (probes_dir / "zebra.rs").write_text("fn main() {}")
        (probes_dir / "alpha.rs").write_text("fn main() {}")

        probes = RustProbeBuilder.discover_probes(str(tmp_path))
        assert [p["name"] for p in probes] == ["alpha", "zebra"]


# ── Compilation tests (mocked) ───────────────────────────────


class TestCompilation:
    @patch("chaosprobe.probes.builder.shutil.which", return_value="/usr/bin/rustc")
    @patch("chaosprobe.probes.builder._run_cmd")
    def test_compile_single_file(self, mock_run, mock_which, tmp_path):
        builder = RustProbeBuilder()
        probe = {
            "name": "health",
            "path": "/tmp/probes/health.rs",
            "kind": "single_file",
        }
        out = builder.compile_probe(probe, str(tmp_path))

        assert out == str(tmp_path / "health")
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "rustc" in cmd
        assert MUSL_TARGET in cmd
        assert "/tmp/probes/health.rs" in cmd

    @patch("chaosprobe.probes.builder.shutil.which", return_value="/usr/bin/cargo")
    @patch("chaosprobe.probes.builder._run_cmd")
    @patch("chaosprobe.probes.builder.shutil.copy2")
    @patch("chaosprobe.probes.builder.Path.exists", return_value=True)
    def test_compile_cargo_project(self, mock_exists, mock_copy, mock_run, mock_which, tmp_path):
        builder = RustProbeBuilder()
        probe = {
            "name": "check-db",
            "path": "/tmp/probes/check-db",
            "kind": "cargo",
        }
        out = builder.compile_probe(probe, str(tmp_path))

        assert out == str(tmp_path / "check-db")
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "cargo" in cmd
        assert "--release" in cmd

    @patch("chaosprobe.probes.builder.shutil.which", return_value=None)
    def test_compile_fails_without_rustc(self, mock_which, tmp_path):
        builder = RustProbeBuilder()
        probe = {"name": "x", "path": "/tmp/x.rs", "kind": "single_file"}
        with pytest.raises(ProbeBuilderError, match="rustc"):
            builder.compile_probe(probe, str(tmp_path))

    def test_compile_unknown_kind_raises(self, tmp_path):
        builder = RustProbeBuilder()
        probe = {"name": "x", "path": "/tmp/x", "kind": "unknown"}
        with patch("chaosprobe.probes.builder.shutil.which", return_value="/usr/bin/rustc"):
            with pytest.raises(ProbeBuilderError, match="Unknown probe kind"):
                builder.compile_probe(probe, str(tmp_path))


# ── Image building tests (mocked) ────────────────────────────


class TestImageBuilding:
    @patch("chaosprobe.probes.builder._run_cmd")
    @patch("chaosprobe.probes.builder.shutil.which", return_value="/usr/bin/docker")
    def test_build_image_tags(self, mock_which, mock_run, tmp_path):
        # Create a fake binary
        binary = tmp_path / "myprobe"
        binary.write_bytes(b"ELF-binary-content")

        builder = RustProbeBuilder(registry="myregistry")
        tag = builder.build_image("myprobe", str(binary), "myscenario")

        assert tag.startswith("myregistry/myscenario-myprobe:")
        assert len(tag.split(":")[-1]) == 8  # sha256 hash prefix

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "docker" in cmd
        assert "build" in cmd

    @patch("chaosprobe.probes.builder._run_cmd")
    @patch("chaosprobe.probes.builder.shutil.which", return_value="/usr/bin/docker")
    def test_build_image_kind_load(self, mock_which, mock_run, tmp_path):
        binary = tmp_path / "probe"
        binary.write_bytes(b"binary")

        builder = RustProbeBuilder(load_kind=True)

        # Mock _kind_load separately
        with patch.object(builder, "_kind_load") as mock_load:
            tag = builder.build_image("probe", str(binary))
            mock_load.assert_called_once_with(tag)

    @patch("chaosprobe.probes.builder.shutil.which", return_value=None)
    def test_build_image_fails_without_docker(self, mock_which, tmp_path):
        binary = tmp_path / "x"
        binary.write_bytes(b"bin")

        builder = RustProbeBuilder()
        with pytest.raises(ProbeBuilderError, match="Docker not found"):
            builder.build_image("x", str(binary))


# ── Orchestrator tests ────────────────────────────────────────


class TestBuildAll:
    @patch.object(RustProbeBuilder, "build_image", return_value="chaosprobe/sc-p:abc123")
    @patch.object(RustProbeBuilder, "compile_probe")
    @patch.object(RustProbeBuilder, "discover_probes")
    def test_build_all_end_to_end(self, mock_discover, mock_compile, mock_image, tmp_path):
        mock_discover.return_value = [
            {"name": "probe-a", "path": "/probes/a.rs", "kind": "single_file"},
        ]
        mock_compile.return_value = str(tmp_path / "probe-a")

        builder = RustProbeBuilder()
        result = builder.build_all(str(tmp_path))

        assert result == {"probe-a": "chaosprobe/sc-p:abc123"}
        mock_compile.assert_called_once()
        mock_image.assert_called_once()

    @patch.object(RustProbeBuilder, "discover_probes", return_value=[])
    def test_build_all_no_probes(self, mock_discover, tmp_path):
        builder = RustProbeBuilder()
        result = builder.build_all(str(tmp_path))
        assert result == {}

    @patch.object(RustProbeBuilder, "build_image")
    @patch.object(RustProbeBuilder, "compile_probe")
    @patch.object(RustProbeBuilder, "discover_probes")
    def test_build_all_uses_dir_name(self, mock_discover, mock_compile, mock_image, tmp_path):
        mock_discover.return_value = [
            {"name": "p", "path": "/x.rs", "kind": "single_file"},
        ]
        mock_compile.return_value = "/tmp/p"
        mock_image.return_value = "r/x-p:1"

        scenario = tmp_path / "my-scenario"
        scenario.mkdir()

        builder = RustProbeBuilder()
        builder.build_all(str(scenario))

        # scenario_name defaults to directory name
        mock_image.assert_called_once_with("p", "/tmp/p", "my-scenario")


# ── Patching tests ────────────────────────────────────────────


class TestPatchProbeImages:
    def test_patches_matching_cmdprobe(self):
        experiments = [
            {
                "file": "exp.yaml",
                "spec": {
                    "spec": {
                        "experiments": [
                            {
                                "name": "pod-delete",
                                "spec": {
                                    "probe": [
                                        {
                                            "name": "check-db",
                                            "type": "cmdProbe",
                                            "mode": "Edge",
                                            "cmdProbe/inputs": {
                                                "command": "check-db",
                                                "comparator": {
                                                    "type": "string",
                                                    "criteria": "contains",
                                                    "value": "OK",
                                                },
                                            },
                                            "runProperties": {
                                                "probeTimeout": "5s",
                                                "interval": "5s",
                                                "retry": 1,
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                },
            }
        ]

        built = {"check-db": "myregistry/sc-check-db:abc123"}
        count = patch_probe_images(experiments, built)

        assert count == 1
        probe = experiments[0]["spec"]["spec"]["experiments"][0]["spec"]["probe"][0]
        assert probe["cmdProbe/inputs"]["source"]["image"] == "myregistry/sc-check-db:abc123"
        assert probe["cmdProbe/inputs"]["command"] == "/probe/check-db"

    def test_does_not_patch_non_cmdprobe(self):
        experiments = [
            {
                "file": "exp.yaml",
                "spec": {
                    "spec": {
                        "experiments": [
                            {
                                "name": "pod-delete",
                                "spec": {
                                    "probe": [
                                        {
                                            "name": "http-check",
                                            "type": "httpProbe",
                                            "mode": "Continuous",
                                            "httpProbe/inputs": {
                                                "url": "http://svc:80",
                                                "method": {
                                                    "get": {
                                                        "criteria": "==",
                                                        "responseCode": "200",
                                                    }
                                                },
                                            },
                                            "runProperties": {
                                                "probeTimeout": "5s",
                                                "interval": "2s",
                                                "retry": 1,
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                },
            }
        ]

        count = patch_probe_images(experiments, {"http-check": "img:tag"})
        assert count == 0

    def test_does_not_patch_unmatched_name(self):
        experiments = [
            {
                "file": "exp.yaml",
                "spec": {
                    "spec": {
                        "experiments": [
                            {
                                "name": "pod-delete",
                                "spec": {
                                    "probe": [
                                        {
                                            "name": "other-probe",
                                            "type": "cmdProbe",
                                            "mode": "Edge",
                                            "cmdProbe/inputs": {
                                                "command": "other-probe",
                                                "comparator": {
                                                    "type": "string",
                                                    "criteria": "equal",
                                                    "value": "OK",
                                                },
                                            },
                                            "runProperties": {
                                                "probeTimeout": "5s",
                                                "interval": "5s",
                                                "retry": 1,
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                },
            }
        ]

        count = patch_probe_images(experiments, {"check-db": "img:tag"})
        assert count == 0

    def test_preserves_custom_command(self):
        experiments = [
            {
                "file": "exp.yaml",
                "spec": {
                    "spec": {
                        "experiments": [
                            {
                                "name": "pod-delete",
                                "spec": {
                                    "probe": [
                                        {
                                            "name": "check-db",
                                            "type": "cmdProbe",
                                            "mode": "Edge",
                                            "cmdProbe/inputs": {
                                                "command": "/custom/path --arg1",
                                                "comparator": {
                                                    "type": "string",
                                                    "criteria": "equal",
                                                    "value": "OK",
                                                },
                                            },
                                            "runProperties": {
                                                "probeTimeout": "5s",
                                                "interval": "5s",
                                                "retry": 1,
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                },
            }
        ]

        count = patch_probe_images(experiments, {"check-db": "img:tag"})
        assert count == 1
        probe = experiments[0]["spec"]["spec"]["experiments"][0]["spec"]["probe"][0]
        # Custom command should NOT be overwritten
        assert probe["cmdProbe/inputs"]["command"] == "/custom/path --arg1"

    def test_patches_multiple_probes(self):
        experiments = [
            {
                "file": "exp.yaml",
                "spec": {
                    "spec": {
                        "experiments": [
                            {
                                "name": "pod-delete",
                                "spec": {
                                    "probe": [
                                        {
                                            "name": "probe-a",
                                            "type": "cmdProbe",
                                            "mode": "Edge",
                                            "cmdProbe/inputs": {
                                                "command": "probe-a",
                                                "comparator": {
                                                    "type": "string",
                                                    "criteria": "equal",
                                                    "value": "OK",
                                                },
                                            },
                                            "runProperties": {
                                                "probeTimeout": "5s",
                                                "interval": "5s",
                                                "retry": 1,
                                            },
                                        },
                                        {
                                            "name": "probe-b",
                                            "type": "cmdProbe",
                                            "mode": "Edge",
                                            "cmdProbe/inputs": {
                                                "command": "probe-b",
                                                "comparator": {
                                                    "type": "string",
                                                    "criteria": "equal",
                                                    "value": "OK",
                                                },
                                            },
                                            "runProperties": {
                                                "probeTimeout": "5s",
                                                "interval": "5s",
                                                "retry": 1,
                                            },
                                        },
                                    ],
                                },
                            }
                        ],
                    },
                },
            }
        ]

        built = {
            "probe-a": "reg/sc-a:1",
            "probe-b": "reg/sc-b:2",
        }
        count = patch_probe_images(experiments, built)
        assert count == 2

    def test_patches_empty_experiments(self):
        count = patch_probe_images([], {"x": "y"})
        assert count == 0


# ── Utility tests ─────────────────────────────────────────────


class TestUtilities:
    @patch("chaosprobe.probes.builder.shutil.which", return_value=None)
    def test_require_tool_missing(self, mock_which):
        with pytest.raises(ProbeBuilderError, match="not found"):
            _require_tool("rustc", "rustc not found")

    @patch("chaosprobe.probes.builder.shutil.which", return_value="/usr/bin/rustc")
    def test_require_tool_present(self, mock_which):
        _require_tool("rustc", "rustc not found")  # should not raise

    @patch("chaosprobe.probes.builder.subprocess.run")
    def test_run_cmd_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        result = _run_cmd(["echo", "hi"], "failed")
        assert result is not None

    @patch("chaosprobe.probes.builder.subprocess.run")
    def test_run_cmd_failure(self, mock_run):
        import subprocess

        mock_run.side_effect = subprocess.CalledProcessError(
            1, "cmd", stderr="compilation error"
        )
        with pytest.raises(ProbeBuilderError, match="compilation error"):
            _run_cmd(["bad-cmd"], "build failed")


# ── Config loader integration tests ──────────────────────────


class TestLoaderDiscovery:
    def test_load_scenario_detects_rust_probes(self, tmp_path):
        from chaosprobe.config.loader import load_scenario

        # Create experiment yaml
        exp = tmp_path / "experiment.yaml"
        exp.write_text(textwrap.dedent("""\
            apiVersion: litmuschaos.io/v1alpha1
            kind: ChaosEngine
            metadata:
              name: test
            spec:
              engineState: active
              appinfo:
                appns: default
                applabel: app=test
                appkind: deployment
              chaosServiceAccount: litmus-admin
              experiments:
                - name: pod-delete
        """))

        # Create probes directory with a single file probe
        probes_dir = tmp_path / "probes"
        probes_dir.mkdir()
        (probes_dir / "check-health.rs").write_text("fn main() {}")

        scenario = load_scenario(str(tmp_path))
        assert "probes" in scenario
        assert len(scenario["probes"]) == 1
        assert scenario["probes"][0]["name"] == "check-health"
        assert scenario["probes"][0]["kind"] == "single_file"

    def test_load_scenario_no_probes(self, tmp_path):
        from chaosprobe.config.loader import load_scenario

        exp = tmp_path / "experiment.yaml"
        exp.write_text(textwrap.dedent("""\
            apiVersion: litmuschaos.io/v1alpha1
            kind: ChaosEngine
            metadata:
              name: test
            spec:
              engineState: active
              appinfo:
                appns: default
                applabel: app=test
                appkind: deployment
              chaosServiceAccount: litmus-admin
              experiments:
                - name: pod-delete
        """))

        scenario = load_scenario(str(tmp_path))
        assert "probes" not in scenario
