# Thesis manuscript

The **canonical manuscript is the LaTeX source in [`latex/`](latex/)** —
[`latex/main.tex`](latex/main.tex), Overleaf-ready. It is the
single source of truth for the dissertation (title: *Measuring
Placement-Sensitive Resilience under Chaos: A Layered, Provenance-Gated Study in
Kubernetes*). See [`latex/README.md`](latex/README.md) for the chapter map and
Overleaf import steps.

An earlier Markdown draft of the manuscript previously lived in this directory.
It described a superseded, exploratory study design and was removed once the
LaTeX confirmatory manuscript became canonical; recover it from git history if
needed.

## Contents

- [`latex/`](latex/) — the canonical manuscript (LaTeX) and its `references.bib`.
- [`figures/`](figures/) — committed figures shared by the manuscript;
  [`figures/MANIFEST.md`](figures/MANIFEST.md) maps filenames to figure numbers
  and regeneration commands.
- [`data/`](data/) — raw measurement data referenced by the manuscript
  (e.g. the conntrack protocol-probe samples).

## Sources of truth for numbers and claims

The manuscript quotes numbers and claims only from these, and a chapter that
disagrees with a source is a bug:

- [`../chaosprobe/docs/explanation/hypotheses.md`](../chaosprobe/docs/explanation/hypotheses.md) — hypotheses and campaign numbers.
- [`../chaosprobe/docs/explanation/scope-of-claims.md`](../chaosprobe/docs/explanation/scope-of-claims.md) — what may and may not be claimed (governs wording).
- [`../references.md`](../references.md) — annotated bibliography (the human-readable source `references.bib` is derived from).
- `../chaosprobe/dist/` per-archive `artifact-manifest.json` — run provenance.
