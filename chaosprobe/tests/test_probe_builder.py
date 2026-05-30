"""Tests for the Rust cmdProbe builder pipeline."""

import socket
import subprocess
import textwrap
from unittest.mock import MagicMock, patch

import pytest

from chaosprobe.probes.builder import (
    MUSL_TARGET,
    ProbeBuilderError,
    RustProbeBuilder,
    _port_accepts,
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


# ── Push-tunnel tests ─────────────────────────────────────────


class TestPushTunnel:
    @patch("chaosprobe.probes.builder._run_cmd")
    def test_push_image_saves_and_cranes_through_tunnel(self, mock_run_cmd):
        b = RustProbeBuilder(registry="192.168.56.11:30500", push=True)
        b._push_host = "127.0.0.1:45000"
        b._push_image("192.168.56.11:30500/sc-check:abc123")

        save_cmd = mock_run_cmd.call_args_list[0][0][0]
        push_cmd = mock_run_cmd.call_args_list[1][0][0]
        # docker save the built image, then daemon-less crane push to the tunnel
        assert save_cmd[:3] == ["docker", "save", "192.168.56.11:30500/sc-check:abc123"]
        assert push_cmd[:3] == ["crane", "push", "--insecure"]
        # re-addressed to the localhost tunnel; repo path preserved
        assert push_cmd[-1] == "127.0.0.1:45000/sc-check:abc123"

    @patch("chaosprobe.probes.builder._run_cmd")
    def test_push_image_direct_when_no_tunnel(self, mock_run_cmd):
        b = RustProbeBuilder(registry="192.168.56.11:30500", push=True)
        b._push_image("192.168.56.11:30500/sc-check:abc123")
        # docker save + crane push straight to image_tag (no tunnel re-address)
        assert mock_run_cmd.call_args_list[0][0][0][:2] == ["docker", "save"]
        push_cmd = mock_run_cmd.call_args_list[1][0][0]
        assert push_cmd[:3] == ["crane", "push", "--insecure"]
        assert push_cmd[-1] == "192.168.56.11:30500/sc-check:abc123"

    @patch("chaosprobe.probes.builder.time.sleep")
    @patch("chaosprobe.probes.builder.time.time", side_effect=[1000.0, 1001.0, 1002.0])
    @patch("chaosprobe.probes.builder._port_accepts", side_effect=[False, True])
    @patch("chaosprobe.probes.builder.subprocess.Popen")
    @patch("chaosprobe.probes.builder.shutil.which", return_value="/usr/bin/kubectl")
    def test_open_tunnel_waits_until_ready(
        self, mock_which, mock_popen, mock_acc, mock_time, mock_sleep
    ):
        proc = MagicMock()
        proc.poll.return_value = None  # alive; reachable on the second check
        mock_popen.return_value = proc

        b = RustProbeBuilder(registry="192.168.56.11:30500", push=True)
        b._open_push_tunnel()
        assert b._push_host is not None
        mock_sleep.assert_called_once()  # waited one iteration before it came up

    @patch("chaosprobe.probes.builder._port_accepts", return_value=True)
    @patch("chaosprobe.probes.builder.subprocess.Popen")
    @patch("chaosprobe.probes.builder.shutil.which", return_value="/usr/bin/kubectl")
    def test_open_tunnel_forwards_registry_service(self, mock_which, mock_popen, mock_accepts):
        proc = MagicMock()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        b = RustProbeBuilder(registry="192.168.56.11:30500", push=True)
        b._open_push_tunnel()

        assert b._push_host is not None and b._push_host.startswith("127.0.0.1:")
        cmd = mock_popen.call_args[0][0]
        assert cmd[:4] == ["kubectl", "port-forward", "-n", "registry"]
        assert "svc/registry" in cmd

        b._close_push_tunnel()
        assert b._push_host is None and b._tunnel is None

    @patch("chaosprobe.probes.builder._port_accepts", return_value=False)
    @patch("chaosprobe.probes.builder.subprocess.Popen")
    @patch("chaosprobe.probes.builder.shutil.which", return_value="/usr/bin/kubectl")
    def test_open_tunnel_raises_when_forward_exits(self, mock_which, mock_popen, mock_accepts):
        proc = MagicMock()
        proc.poll.return_value = 1  # exited immediately
        proc.stderr.read.return_value = b"error: services 'registry' not found"
        mock_popen.return_value = proc

        b = RustProbeBuilder(registry="192.168.56.11:30500", push=True)
        with pytest.raises(ProbeBuilderError, match="port-forward failed"):
            b._open_push_tunnel()

    @patch("chaosprobe.probes.builder.time.sleep")
    @patch("chaosprobe.probes.builder.time.time", side_effect=[1000.0, 1031.0])
    @patch("chaosprobe.probes.builder._port_accepts", return_value=False)
    @patch("chaosprobe.probes.builder.subprocess.Popen")
    @patch("chaosprobe.probes.builder.shutil.which", return_value="/usr/bin/kubectl")
    def test_open_tunnel_times_out(self, mock_which, mock_popen, mock_acc, mock_time, mock_sleep):
        proc = MagicMock()
        proc.poll.return_value = None  # stays alive but never reachable
        mock_popen.return_value = proc

        b = RustProbeBuilder(registry="192.168.56.11:30500", push=True)
        with pytest.raises(ProbeBuilderError, match="did not become ready"):
            b._open_push_tunnel()

    def test_close_tunnel_kills_on_wait_timeout(self):
        b = RustProbeBuilder(push=True)
        proc = MagicMock()
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="kubectl", timeout=5)
        b._tunnel = proc
        b._close_push_tunnel()
        proc.kill.assert_called_once()
        assert b._tunnel is None

    @patch("chaosprobe.probes.builder.subprocess.run")
    @patch("chaosprobe.probes.builder.time.sleep")
    @patch("chaosprobe.probes.builder._run_cmd")
    def test_push_image_retries_then_succeeds(self, mock_run_cmd, mock_sleep, mock_sub_run):
        # tag ok; push fails once, then succeeds
        mock_run_cmd.side_effect = [None, ProbeBuilderError("net"), None]
        b = RustProbeBuilder(registry="192.168.56.11:30500", push=True)
        b._push_host = "127.0.0.1:45000"
        b._push_image("192.168.56.11:30500/sc-check:abc", retries=3)
        assert mock_run_cmd.call_count == 3  # 1 tag + 2 push attempts
        mock_sleep.assert_called_once()

    @patch("chaosprobe.probes.builder.subprocess.run")
    @patch("chaosprobe.probes.builder.time.sleep")
    @patch("chaosprobe.probes.builder._run_cmd")
    def test_push_image_raises_after_retries(self, mock_run_cmd, mock_sleep, mock_sub_run):
        mock_run_cmd.side_effect = [None] + [
            ProbeBuilderError("net")
        ] * 3  # tag ok, all pushes fail
        b = RustProbeBuilder(registry="192.168.56.11:30500", push=True)
        b._push_host = "127.0.0.1:45000"
        with pytest.raises(ProbeBuilderError, match="net"):
            b._push_image("192.168.56.11:30500/sc-check:abc", retries=3)

    @patch("chaosprobe.probes.builder._run_cmd")
    @patch("chaosprobe.probes.builder.shutil.which", return_value="/usr/bin/docker")
    def test_build_image_pushes_when_enabled(self, mock_which, mock_run, tmp_path):
        binary = tmp_path / "p"
        binary.write_bytes(b"bin")
        b = RustProbeBuilder(registry="192.168.56.11:30500", push=True)
        with patch.object(b, "_push_image") as mock_push:
            b.build_image("p", str(binary), "sc")
        assert mock_push.call_count == 2  # the hash tag + :latest

    @patch("chaosprobe.probes.builder.shutil.which", return_value="/usr/bin/crane")
    @patch.object(RustProbeBuilder, "_close_push_tunnel")
    @patch.object(RustProbeBuilder, "_open_push_tunnel")
    @patch.object(RustProbeBuilder, "build_image", return_value="192.168.56.11:30500/sc-p:abc")
    @patch.object(RustProbeBuilder, "compile_probe")
    @patch.object(RustProbeBuilder, "discover_probes")
    def test_build_all_opens_and_closes_tunnel_when_pushing(
        self, mock_disc, mock_comp, mock_img, mock_open, mock_close, mock_which, tmp_path
    ):
        mock_disc.return_value = [{"name": "p", "path": "/x.rs", "kind": "single_file"}]
        mock_comp.return_value = str(tmp_path / "p")
        b = RustProbeBuilder(registry="192.168.56.11:30500", push=True)
        b.build_all(str(tmp_path))
        mock_open.assert_called_once()
        mock_close.assert_called_once()

    @patch("chaosprobe.probes.builder.shutil.which", return_value="/usr/bin/crane")
    @patch.object(RustProbeBuilder, "_close_push_tunnel")
    @patch.object(RustProbeBuilder, "_open_push_tunnel")
    @patch.object(RustProbeBuilder, "build_image", side_effect=ProbeBuilderError("push failed"))
    @patch.object(RustProbeBuilder, "compile_probe")
    @patch.object(RustProbeBuilder, "discover_probes")
    def test_build_all_closes_tunnel_even_on_failure(
        self, mock_disc, mock_comp, mock_img, mock_open, mock_close, mock_which, tmp_path
    ):
        mock_disc.return_value = [{"name": "p", "path": "/x.rs", "kind": "single_file"}]
        mock_comp.return_value = str(tmp_path / "p")
        b = RustProbeBuilder(registry="192.168.56.11:30500", push=True)
        with pytest.raises(ProbeBuilderError, match="failed to build"):
            b.build_all(str(tmp_path))
        mock_close.assert_called_once()  # tunnel torn down via finally despite the failure

    @patch("chaosprobe.probes.builder.shutil.which", return_value=None)
    @patch.object(RustProbeBuilder, "discover_probes")
    def test_build_all_requires_crane_when_pushing(self, mock_disc, mock_which, tmp_path):
        mock_disc.return_value = [{"name": "p", "path": "/x.rs", "kind": "single_file"}]
        b = RustProbeBuilder(registry="192.168.56.11:30500", push=True)
        with pytest.raises(ProbeBuilderError, match="crane not found"):
            b.build_all(str(tmp_path))

    def test_port_accepts_open_and_closed(self):
        # An open listening socket is accepted; a closed port is not.
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        open_port = srv.getsockname()[1]
        try:
            assert _port_accepts(open_port) is True
        finally:
            srv.close()
        assert _port_accepts(open_port) is False  # now closed


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

        mock_run.side_effect = subprocess.CalledProcessError(1, "cmd", stderr="compilation error")
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
