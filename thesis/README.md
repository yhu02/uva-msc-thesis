# Thesis manuscript scaffold

This directory is the **canonical manuscript of the UvA MSc dissertation**.
It began as a scaffold (the repository previously contained extensive
technical documentation but no manuscript, flagged by the external advisory
review); all ten chapters are now complete prose with final campaign numbers.
The remaining authored work is a voice pass and the LaTeX/Overleaf port.

Format: Markdown, deliberately plain — portable to LaTeX/Overleaf later
(tables are pipe tables, figures are committed PNGs embedded with stable
labels — see the filename↔figure-number crosswalk in
[`figures/MANIFEST.md`](figures/MANIFEST.md) — and citations are author-year
keyed to [`../references.md`](../references.md)).

## Sources of truth

The scaffold quotes numbers and claims **only** from these documents — if a
chapter and a source disagree, the source wins and the chapter is a bug:

- [`chaosprobe/docs/explanation/hypotheses.md`](../chaosprobe/docs/explanation/hypotheses.md) — H1–H6, final campaign numbers.
- [`chaosprobe/docs/explanation/scope-of-claims.md`](../chaosprobe/docs/explanation/scope-of-claims.md) — what may and may not be claimed (**governs all wording**).
- [`chaosprobe/docs/explanation/proposed-experiments.md`](../chaosprobe/docs/explanation/proposed-experiments.md) — the P1–P3 roadmap (future work).
- [`references.md`](../references.md) — annotated bibliography.
- [`chaosprobe/dist/`](../chaosprobe/dist) per-archive `artifact-manifest.json` — run provenance.

## Chapter map

| File | Dissertation chapter | Role |
|---|---|---|
| [`00-abstract.md`](00-abstract.md) | Abstract | Punchline-first abstract: trade-off + layered decoupling, one anchoring number per fault class |
| [`01-introduction.md`](01-introduction.md) | 1. Introduction | Problem, research question (verbatim), SQ↔H crosswalk, contribution claims 1a–1c/2–4 |
| [`02-related-work.md`](02-related-work.md) | 2. Related work | Positioning quadrant (stated once) + five lineage sections (scheduler, chaos, methodology, tail latency, novelty bounds) |
| [`03-chaosprobe.md`](03-chaosprobe.md) | 3. The ChaosProbe framework | System architecture + provenance capture; workflow figure embedded (Fig 3.1) |
| [`04-methodology.md`](04-methodology.md) | 4. Methodology | Three-layer design, fault classes, campaign design, statistics, environment |
| [`05-results.md`](05-results.md) | 5. Results | Per-hypothesis findings with embedded figures (Figs 5.1–5.8), mechanism interpretation, claims→evidence table, §5.8 gate retractions |
| [`06-discussion.md`](06-discussion.md) | 6. Discussion | Layered decoupling, the trade-off, H2 attribution, L1–L3, practical implications |
| [`07-threats.md`](07-threats.md) | 7. Threats to validity | Threats table + what-generalizes table (the canonical portability boundary) |
| [`08-conclusion.md`](08-conclusion.md) | 8. Conclusion & future work | Synthesis + E1/P2/H7 future work |
| [`09-appendix-provenance.md`](09-appendix-provenance.md) | Appendix A/B | Archived-run provenance table, claims→runs mapping, negative findings |

## Status

All chapters are **complete**: full prose, final campaign numbers, committed
figures wired in. The only remaining authored work is a voice pass and the
LaTeX/Overleaf port.

| Section | Status |
|---|---|
| Abstract | **Complete** — punchline-first, ≤350 words, one anchoring number per fault-class beat |
| Introduction | **Complete** — problem, sub-questions + crosswalk, contributions 1a–1c/2–4, outline |
| Related work | **Complete** — positioning quadrant stated once + lineages |
| ChaosProbe (system) | **Complete** — full prose; `figures/fig-01-workflow.png` embedded |
| Methodology | **Complete** — formal model, cluster bootstrap, node-spec table |
| Results | **Complete** — all data banked (campaign, probe, H5 batch 2, H6 gradient); figures embedded; §5.8 retraction summary |
| Discussion | **Complete** — decoupling, trade-off, H2 attribution, L1–L3, implications |
| Threats to validity | **Complete** — tables + validity-vocabulary framing prose |
| Conclusion & future work | **Complete** — synthesis + prioritized future work |
| Provenance appendix | **Complete** (generated from `dist/` manifests) |

## Non-negotiable rules

1. **Every quoted number must trace to an archived run.** A figure quoted as a
   *finding* must come from a `doctor --strict`-clean run archived under
   `chaosprobe/dist/`, and must appear in the provenance appendix's
   claims→runs mapping ([`09-appendix-provenance.md`](09-appendix-provenance.md)).
   No archive, no number.
2. **Claims discipline** per
   [`scope-of-claims.md`](../chaosprobe/docs/explanation/scope-of-claims.md):
   never "refuted", "proven", "best strategy", unqualified "reproducible", or
   "generalizes". H1 is "the score cannot *rank placement strategies under
   session variance*" — never "aggregate scores don't work". No user-visible
   placement claim under load. H6 is the *quantification of a known qualitative
   trade-off*, not its discovery. H5 validates a *static predictor*, not the
   locality concept (NetMARKS/TraDE own that as an optimization objective).
3. The manuscript carries no `TODO(author):` markers — every chapter is
   complete prose. Any future `TODO(author):` marker means a regression to
   draft state and must not survive a merge to `main`.
