"""Tests for scripts/archive_run.py — run bundle + artifact manifest."""

import importlib.util
import json
import tarfile
from pathlib import Path

import pytest

# scripts/ is not a package; load the module by path.
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "archive_run.py"
_spec = importlib.util.spec_from_file_location("archive_run", _SCRIPT)
assert _spec is not None and _spec.loader is not None
archive_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(archive_run)


def _write_run(
    tmp_path: Path,
    *,
    dirty: bool = False,
    with_hashes: bool = True,
    with_metadata: bool = True,
    extra_files: bool = True,
) -> Path:
    run_dir = tmp_path / "20260606-120000"
    run_dir.mkdir()
    summary: dict = {
        "runId": "run-20260606-120000",
        "batchId": "thesis-churn",
        "timestamp": "2026-06-06T12:00:00+00:00",
        "namespace": "online-boutique",
        "iterations": 8,
        "schemaVersion": "2.0.0",
        "faultExperiments": ["pod-delete"],
        "strategies": {"default": {}, "colocate": {}, "spread": {}},
    }
    if with_metadata:
        summary["runMetadata"] = {
            "git": {"commit": "abc123", "shortCommit": "abc123", "dirty": dirty},
            "kubernetes": {"serverVersion": "v1.28.6"},
            "cniHint": "calico",
            "kubeProxy": {"mode": "iptables", "conntrack": {"min": 131072}},
        }
    if with_hashes:
        summary["scenarioHashes"] = [{"file": "pod-delete.yaml", "sha256": "deadbeef"}]
    run_dir.joinpath("summary.json").write_text(json.dumps(summary))
    if extra_files:
        run_dir.joinpath("colocate.json").write_text('{"x": 1}')
        charts = run_dir / "charts"
        charts.mkdir()
        charts.joinpath("score.png").write_bytes(b"\x89PNG fake")
    return run_dir


class TestBuildManifest:
    def test_collects_identity_and_provenance(self, tmp_path):
        run_dir = _write_run(tmp_path)
        m = archive_run.build_manifest(run_dir)
        assert m["runId"] == "run-20260606-120000"
        assert m["batchId"] == "thesis-churn"
        assert m["iterations"] == 8
        assert m["faults"] == ["pod-delete"]
        assert m["strategies"] == ["colocate", "default", "spread"]
        assert m["kubeProxy"]["mode"] == "iptables"
        assert m["scenarioHashes"] == [{"file": "pod-delete.yaml", "sha256": "deadbeef"}]
        assert m["provenanceWarnings"] == []

    def test_hashes_every_file_except_manifest(self, tmp_path):
        run_dir = _write_run(tmp_path)
        # A stale manifest must not be hashed into the new one.
        run_dir.joinpath("artifact-manifest.json").write_text("{}")
        m = archive_run.build_manifest(run_dir)
        names = {f["file"] for f in m["files"]}
        assert "summary.json" in names
        assert "colocate.json" in names
        assert "charts/score.png" in names
        assert "artifact-manifest.json" not in names
        assert m["fileCount"] == len(m["files"])
        assert all(len(f["sha256"]) == 64 and f["bytes"] >= 0 for f in m["files"])

    def test_dirty_tree_warned(self, tmp_path):
        m = archive_run.build_manifest(_write_run(tmp_path, dirty=True))
        assert any("dirty" in w for w in m["provenanceWarnings"])

    def test_missing_hashes_warned(self, tmp_path):
        m = archive_run.build_manifest(_write_run(tmp_path, with_hashes=False))
        assert any("scenarioHashes" in w for w in m["provenanceWarnings"])

    def test_missing_metadata_warned(self, tmp_path):
        m = archive_run.build_manifest(_write_run(tmp_path, with_metadata=False))
        assert any("runMetadata absent" in w for w in m["provenanceWarnings"])
        assert m["git"] == {}

    def test_missing_commit_warned(self, tmp_path):
        run_dir = _write_run(tmp_path)
        data = json.loads((run_dir / "summary.json").read_text())
        data["runMetadata"]["git"]["commit"] = None
        (run_dir / "summary.json").write_text(json.dumps(data))
        m = archive_run.build_manifest(run_dir)
        assert any("git commit not recorded" in w for w in m["provenanceWarnings"])

    def test_missing_summary_raises(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError):
            archive_run.build_manifest(empty)


class TestWriteArchive:
    def test_writes_manifest_and_tarball(self, tmp_path):
        run_dir = _write_run(tmp_path)
        out = tmp_path / "dist"
        manifest_path, archive_path = archive_run.write_archive(run_dir, out)
        assert manifest_path.exists() and archive_path.exists()
        # Manifest also written inside the run dir so the tarball self-describes.
        assert (run_dir / "artifact-manifest.json").exists()
        with tarfile.open(archive_path) as tar:
            members = tar.getnames()
        assert any(name.endswith("summary.json") for name in members)
        assert archive_path.name == "run-20260606-120000.tar.gz"

    def test_archive_name_falls_back_to_dirname(self, tmp_path):
        run_dir = _write_run(tmp_path)
        # Drop runId → archive name comes from the directory.
        data = json.loads((run_dir / "summary.json").read_text())
        del data["runId"]
        (run_dir / "summary.json").write_text(json.dumps(data))
        _, archive_path = archive_run.write_archive(run_dir, tmp_path / "dist")
        assert archive_path.name == "20260606-120000.tar.gz"


class TestMain:
    def test_clean_run_exits_zero(self, tmp_path, capsys):
        run_dir = _write_run(tmp_path)
        rc = archive_run.main(["--results-dir", str(run_dir), "-o", str(tmp_path / "dist")])
        assert rc == 0
        assert "Archived" in capsys.readouterr().out

    def test_not_a_directory_exits_two(self, tmp_path):
        rc = archive_run.main(["--results-dir", str(tmp_path / "nope"), "-o", str(tmp_path / "d")])
        assert rc == 2

    def test_directory_without_summary_exits_two(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        rc = archive_run.main(["--results-dir", str(empty), "-o", str(tmp_path / "d")])
        assert rc == 2

    def test_dirty_run_nonstrict_exits_zero(self, tmp_path, capsys):
        run_dir = _write_run(tmp_path, dirty=True)
        rc = archive_run.main(["--results-dir", str(run_dir), "-o", str(tmp_path / "dist")])
        assert rc == 0
        assert "provenance warnings" in capsys.readouterr().err

    def test_dirty_run_strict_exits_one(self, tmp_path):
        run_dir = _write_run(tmp_path, dirty=True)
        rc = archive_run.main(
            ["--results-dir", str(run_dir), "-o", str(tmp_path / "dist"), "--strict"]
        )
        assert rc == 1
