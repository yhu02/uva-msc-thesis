# Thesis manuscript scaffold

This directory is the **canonical scaffold of the UvA MSc dissertation**. It
exists because the repository previously contained extensive technical
documentation but **no manuscript** (flagged by the external advisory review).
The scaffold fixes the structure, the load-bearing tables, and the final
campaign numbers; the author's prose goes where the `TODO` markers are.

Format: Markdown, deliberately plain — portable to LaTeX/Overleaf later
(tables are pipe tables, figures are stubs with stable labels, citations are
author-year keyed to [`../references.md`](../references.md)).

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
| [`00-abstract.md`](00-abstract.md) | Abstract | Defensible abstract, extended with H5/H6 + campaign scale |
| [`01-introduction.md`](01-introduction.md) | 1. Introduction | Problem, research question (verbatim), four contribution claims |
| [`02-related-work.md`](02-related-work.md) | 2. Related work | Positioning quadrant + scheduler/chaos/methodology lineages |
| [`03-chaosprobe.md`](03-chaosprobe.md) | 3. The ChaosProbe framework | System architecture + provenance capture |
| [`04-methodology.md`](04-methodology.md) | 4. Methodology | Three-layer design, fault classes, campaign design, statistics, environment |
| [`05-results.md`](05-results.md) | 5. Results | Per-hypothesis findings (final numbers), figure stubs, claims→evidence table |
| [`06-discussion.md`](06-discussion.md) | 6. Discussion | Layered decoupling, the trade-off, practical implications |
| [`07-threats.md`](07-threats.md) | 7. Threats to validity | Threats table + what-generalizes table |
| [`08-conclusion.md`](08-conclusion.md) | 8. Conclusion & future work | Synthesis + E1/P2/H7 future work |
| [`09-appendix-provenance.md`](09-appendix-provenance.md) | Appendix A/B | Archived-run provenance table, claims→runs mapping, negative findings |

## Status

| Section | Status |
|---|---|
| Abstract | **Final draft** — all results incl. probe, H5 two-batch verdict, H6 gradient |
| Introduction | **Drafted** — full prose (problem, sub-questions, contributions, outline) |
| Related work | **Drafted** — full prose around the positioning quadrant + lineages |
| ChaosProbe (system) | **Drafted** — full prose; workflow figure = `figures/fig-01-workflow.png` |
| Methodology | **Drafted** — full prose incl. formal model, cluster bootstrap, node-spec table |
| Results | **Final draft** — all data banked (campaign, probe, H5 batch 2, H6 gradient); figures wired |
| Discussion | **Drafted** — full prose (decoupling, trade-off, H2 attribution, L1–L3, implications) |
| Threats to validity | **Drafted** — tables + validity-vocabulary framing prose |
| Conclusion & future work | **Drafted** — synthesis + prioritized future work |
| Provenance appendix | **Drafted** (generated from `dist/` manifests) |

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
3. `TODO(author):` markers are where dissertation prose goes; everything
   outside them is structural and should survive into the final text.
