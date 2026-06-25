#!/usr/bin/env python3
"""Bundle a run's outputs into an immutable archive + a single artifact manifest.

Every number quoted as a *finding* in the thesis must be traceable to an
archived, clean-provenance run (see ``docs/how-to/reproducing-thesis-results.md``).
This script turns a ``results/<timestamp>/`` directory into two submission-ready
artifacts:

- **``artifact-manifest.json``** — the one file an examiner reads first: run id,
  batch id, git commit + dirty flag, environment fingerprint (K8s / CNI /
  kube-proxy mode), the recorded scenario SHA-256 hashes, the strategy/fault
  matrix, and a SHA-256 of *every* file in the bundle so the archive is
  tamper-evident.
- **``<name>.tar.gz``** — an immutable gzipped tarball of the whole results
  directory (raw ``summary.json``, per-strategy JSONs, exports, charts).

It also runs the same provenance discipline ``doctor`` enforces: a **dirty**
launching tree, **missing ``scenarioHashes``**, or **missing ``runMetadata``**
are flagged, and ``--strict`` makes any of them a non-zero exit so a tainted run
can't be silently packaged as a finding.

Usage::

    python scripts/archive_run.py --results-dir results/churn/20260606-120000
    python scripts/archive_run.py --results-dir results/churn/<ts> -o dist --strict
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_MANIFEST_NAME = "artifact-manifest.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _list_files(results_dir: Path) -> List[Path]:
    """Every regular file under *results_dir*, sorted, excluding a prior manifest.

    A previously-written ``artifact-manifest.json`` is skipped so re-archiving is
    idempotent (the manifest never hashes itself).
    """
    return sorted(p for p in results_dir.rglob("*") if p.is_file() and p.name != _MANIFEST_NAME)


def _provenance_warnings(summary: Dict[str, Any]) -> List[str]:
    """The same provenance gaps ``doctor`` flags, derived from a summary dict."""
    warnings: List[str] = []
    metadata = summary.get("runMetadata")
    if not isinstance(metadata, dict):
        warnings.append("runMetadata absent — reproducibility provenance is incomplete")
    else:
        git = metadata.get("git") or {}
        if git.get("dirty") is True:
            warnings.append(
                "launching tree was dirty — the recorded commit does not represent the running code"
            )
        if git.get("commit") is None:
            warnings.append("git commit not recorded")
    hashes = summary.get("scenarioHashes")
    if not isinstance(hashes, list) or not hashes:
        warnings.append("scenarioHashes absent/empty — silent scenario drift can't be ruled out")
    return warnings


def build_manifest(results_dir: Path) -> Dict[str, Any]:
    """Build the artifact manifest for a results directory.

    Reads ``summary.json`` for run identity + provenance, then hashes every file
    in the bundle. Raises ``FileNotFoundError`` if there is no ``summary.json``
    (an un-archivable directory), so callers fail loudly rather than shipping an
    empty bundle.
    """
    summary_path = results_dir / "summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"no summary.json in {results_dir}")
    summary = json.loads(summary_path.read_text())

    metadata = summary.get("runMetadata") if isinstance(summary.get("runMetadata"), dict) else {}
    files = [
        {
            "file": str(p.relative_to(results_dir)),
            "sha256": _sha256_file(p),
            "bytes": p.stat().st_size,
        }
        for p in _list_files(results_dir)
    ]
    return {
        "runId": summary.get("runId"),
        "batchId": summary.get("batchId"),
        "timestamp": summary.get("timestamp"),
        "namespace": summary.get("namespace"),
        "iterations": summary.get("iterations"),
        "schemaVersion": summary.get("schemaVersion"),
        "faults": summary.get("faultExperiments") or list((summary.get("faults") or {}).keys()),
        "strategies": sorted((summary.get("strategies") or {}).keys()),
        "git": metadata.get("git") or {},
        "kubernetes": metadata.get("kubernetes") or {},
        "cniHint": metadata.get("cniHint"),
        "kubeProxy": metadata.get("kubeProxy") or {},
        "scenarioHashes": summary.get("scenarioHashes") or [],
        "provenanceWarnings": _provenance_warnings(summary),
        "files": files,
        "fileCount": len(files),
    }


def write_archive(results_dir: Path, output_dir: Path) -> Tuple[Path, Path]:
    """Write ``artifact-manifest.json`` + ``<dir>.tar.gz`` for *results_dir*.

    The manifest is written into *results_dir* (so it travels inside the tarball
    too) and a copy of the tarball + manifest land in *output_dir*. Returns
    ``(manifest_path, archive_path)`` in *output_dir*.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(results_dir)

    # Write the manifest inside the run dir first so the tarball is
    # self-describing, then copy it out alongside the archive.
    (results_dir / _MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True))

    name = manifest.get("runId") or results_dir.name
    archive_path = output_dir / f"{name}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(results_dir, arcname=results_dir.name)

    manifest_path = output_dir / _MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest_path, archive_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--results-dir",
        required=True,
        help="A single run directory containing summary.json (e.g. results/churn/<ts>).",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default="dist",
        help="Where to write the manifest + tarball (default: dist).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any provenance warning is present.",
    )
    args = parser.parse_args(argv)

    results_dir = Path(args.results_dir)
    if not results_dir.is_dir():
        print(f"error: {results_dir} is not a directory", file=sys.stderr)
        return 2

    try:
        manifest_path, archive_path = write_archive(results_dir, Path(args.output_dir))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    manifest = json.loads(manifest_path.read_text())
    warnings = manifest["provenanceWarnings"]
    print(f"Archived {manifest['fileCount']} file(s) from {results_dir}")
    print(f"  manifest: {manifest_path}")
    print(f"  archive:  {archive_path}")
    if warnings:
        print("  provenance warnings:", file=sys.stderr)
        for w in warnings:
            print(f"    - {w}", file=sys.stderr)
        if args.strict:
            print("strict: refusing to bless a run with provenance gaps", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point, exercised via main()
    sys.exit(main())
