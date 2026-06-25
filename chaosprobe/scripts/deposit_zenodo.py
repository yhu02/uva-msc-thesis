#!/usr/bin/env python3
"""Deposit a ChaosProbe campaign archive to Zenodo.

Creates a Zenodo deposition, uploads the campaign tarball plus its frozen
integrity manifest and a README, sets the dataset metadata (license, related
identifiers linking the concept record and the pre-registration), and reserves a
DOI. By default it leaves the deposition as a **draft** and prints the reserved
DOI and the review URL — you inspect it in the Zenodo UI and click *Publish*
there. Pass ``--publish`` to publish non-interactively.

The token is read from the ``ZENODO_TOKEN`` environment variable and is sent in
the Authorization header only — it never appears in a URL, argument, or log line.
It is never written to disk. Run it as, e.g.::

    ZENODO_TOKEN=<your-token> uv run python scripts/deposit_zenodo.py \
        --tarball /tmp/c4-nodedrain-deposit.tar.gz \
        --manifest ../v2-design/c4-nodedrain-manifest.sha256 \
        --readme   ../v2-design/c4-deposit-README.md

Use ``--sandbox`` first to dry-run against sandbox.zenodo.org with a sandbox
token. The defaults describe campaign C4 (the design-corrected re-analysis).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import requests

PROD_BASE = "https://zenodo.org/api"
SANDBOX_BASE = "https://sandbox.zenodo.org/api"

# ── C4 default metadata (the design-corrected re-analysis) ──────────────────
DEFAULT_TITLE = (
    "ChaosProbe C4: node-drain dose-response (design-corrected re-analysis) "
    "— raw experiment archive"
)
DEFAULT_DESCRIPTION = (
    "<p>Raw, tamper-evident run archive for campaign <strong>C4</strong>, the "
    "exploratory design-corrected re-analysis of the availability axis in the MSc "
    "thesis <em>Measuring Placement-Sensitive Resilience under Chaos: A "
    "Pre-Registered, Layered Study in Kubernetes</em> (Yvo Hu, University of "
    "Amsterdam, 2026).</p>"
    "<p>C4 is exploratory and outside the frozen confirmatory Holm family. It "
    "recomputes the three construction-limited availability-side results (H3 "
    "trough depth, H4 frontier, H5 availability sub-score) under "
    "<code>node-drain</code>, whose blast radius is placement-dependent. Its "
    "corrected criteria were pre-declared before the new data were examined. "
    "8 complete-block sessions, five cross-node fraction levels "
    "f &#8712; {0, 0.25, 0.5, 0.75, 1.0}, r=1, on Google Online Boutique "
    "(eight 2-vCPU/4-GiB workers, Kubernetes v1.28.6, kube-proxy ipvs). All 8 "
    "sessions pass <code>doctor --strict</code>. The frozen pre-analysis SHA-256 "
    "manifest (<code>c4-nodedrain-manifest.sha256</code>, 56/56 files) is "
    "included.</p>"
    "<p>Code, manuscript, and analysis scripts: "
    '<a href="https://github.com/yhu02/uva-msc-thesis">'
    "github.com/yhu02/uva-msc-thesis</a>. License: CC-BY-4.0.</p>"
)
DEFAULT_KEYWORDS = [
    "chaos engineering",
    "Kubernetes",
    "pod placement",
    "resilience",
    "node-drain",
    "pre-registration",
    "ChaosProbe",
]
# Concept record (continues), frozen pre-registration (isSupplementTo), and the
# three confirmatory campaigns (references).
DEFAULT_RELATED = [
    {"relation": "continues", "identifier": "10.5281/zenodo.20639145", "scheme": "doi"},
    {"relation": "isSupplementTo", "identifier": "10.5281/zenodo.20690836", "scheme": "doi"},
    {"relation": "references", "identifier": "10.5281/zenodo.20690737", "scheme": "doi"},
    {"relation": "references", "identifier": "10.5281/zenodo.20726729", "scheme": "doi"},
    {"relation": "references", "identifier": "10.5281/zenodo.20748970", "scheme": "doi"},
    {
        "relation": "isSupplementTo",
        "identifier": "https://github.com/yhu02/uva-msc-thesis",
        "scheme": "url",
    },
]


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--tarball",
        default="/tmp/c4-nodedrain-deposit.tar.gz",
        help="campaign data tarball to upload",
    )
    ap.add_argument(
        "--tarball-name",
        default="c4-nodedrain-dose.tar.gz",
        help="filename the tarball gets in the deposition",
    )
    ap.add_argument(
        "--manifest", default=None, help="frozen integrity manifest to include (optional)"
    )
    ap.add_argument("--readme", default=None, help="README to include as README.md (optional)")
    ap.add_argument("--title", default=DEFAULT_TITLE)
    ap.add_argument("--creator-name", default="Hu, Yvo")
    ap.add_argument("--creator-affiliation", default="University of Amsterdam")
    ap.add_argument("--license", default="cc-by-4.0")
    ap.add_argument(
        "--sandbox", action="store_true", help="use sandbox.zenodo.org (needs a sandbox token)"
    )
    ap.add_argument(
        "--publish",
        action="store_true",
        help="publish immediately (irreversible); default leaves a draft",
    )
    args = ap.parse_args()

    token = os.environ.get("ZENODO_TOKEN")
    if not token:
        die(
            "ZENODO_TOKEN is not set. Export your (rotated) token and re-run, e.g.\n"
            "  ZENODO_TOKEN=<token> uv run python scripts/deposit_zenodo.py ..."
        )
    if not os.path.isfile(args.tarball):
        die(f"tarball not found: {args.tarball}")

    base = SANDBOX_BASE if args.sandbox else PROD_BASE
    sess = requests.Session()
    sess.headers["Authorization"] = f"Bearer {token}"

    def check(r: requests.Response, what: str) -> dict:
        if r.status_code >= 300:
            body = r.text[:600]
            die(f"{what} failed (HTTP {r.status_code}): {body}")
        return r.json() if r.content else {}

    # 1. Create an empty draft deposition.
    print(f"[1/4] creating draft on {base} ...")
    dep = check(sess.post(f"{base}/deposit/depositions", json={}), "create deposition")
    dep_id = dep["id"]
    bucket = dep["links"]["bucket"]

    # 2. Upload the files via the bucket API (raw PUT, not multipart).
    uploads = [(args.tarball, args.tarball_name)]
    if args.manifest:
        if not os.path.isfile(args.manifest):
            die(f"manifest not found: {args.manifest}")
        uploads.append((args.manifest, os.path.basename(args.manifest)))
    if args.readme:
        if not os.path.isfile(args.readme):
            die(f"readme not found: {args.readme}")
        uploads.append((args.readme, "README.md"))

    for path, name in uploads:
        size = os.path.getsize(path)
        print(f"[2/4] uploading {name} ({size/1e6:.1f} MB) ...")
        with open(path, "rb") as fh:
            check(sess.put(f"{bucket}/{name}", data=fh), f"upload {name}")

    # 3. Set the dataset metadata (reserves a DOI).
    print("[3/4] setting metadata ...")
    metadata = {
        "metadata": {
            "upload_type": "dataset",
            "title": args.title,
            "creators": [{"name": args.creator_name, "affiliation": args.creator_affiliation}],
            "description": DEFAULT_DESCRIPTION,
            "access_right": "open",
            "license": args.license,
            "keywords": DEFAULT_KEYWORDS,
            "related_identifiers": DEFAULT_RELATED,
            "prereserve_doi": True,
        }
    }
    dep = check(
        sess.put(f"{base}/deposit/depositions/{dep_id}", json=metadata),
        "set metadata",
    )
    reserved = dep.get("metadata", {}).get("prereserve_doi", {}).get("doi")
    html_url = dep.get("links", {}).get("html")

    # 4. Optionally publish.
    if args.publish:
        print("[4/4] publishing ...")
        pub = check(
            sess.post(f"{base}/deposit/depositions/{dep_id}/actions/publish"),
            "publish",
        )
        doi = pub.get("doi") or reserved
        print("\n  PUBLISHED")
        print(f"  DOI:    {doi}")
        print(f"  record: {pub.get('links', {}).get('record_html', html_url)}")
    else:
        print("\n[4/4] left as DRAFT (not published)")
        print(f"  reserved DOI: {reserved}")
        print(f"  review + publish here: {html_url}")
        print("  (re-run with --publish to publish non-interactively)")

    print("\nnext: put the DOI into thesis/latex/appendix/a-provenance.tex,")
    print("      chapters/05-results.tex, and thesis/figures/MANIFEST.md")
    # Emit a machine-readable line for scripting.
    print(
        json.dumps(
            {
                "deposition_id": dep_id,
                "reserved_doi": reserved,
                "published": bool(args.publish),
                "sandbox": args.sandbox,
            }
        )
    )


if __name__ == "__main__":
    main()
