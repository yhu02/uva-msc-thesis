# Thesis manuscript (LaTeX, Overleaf-ready)

This directory is the **canonical thesis manuscript**. It began as a hand-written
LaTeX port of an earlier Markdown draft, but was then developed into the current
confirmatory study (dose-response knobs, the interventional DNS arm, the C1/C2/C3
campaigns) beyond what that draft contained; the superseded Markdown draft has
since been removed, so **this LaTeX is now the single source of truth for the
manuscript wording.** Numbers and claims still trace to the sources of truth
listed in [`../README.md`](../README.md).

## Compilation

A built PDF is committed as `Yvo-Hu-MSc-thesis.pdf`, and `main.tex` reports a
clean `tectonic`/XeLaTeX build. Changes made without a local TeX toolchain are
gated only by a brace/`\begin`–`\end` environment-pairing check, so **re-compile
on Overleaf after editing** and treat the first compile as a review pass. Expect
minor cosmetics (overfull boxes in the wide provenance table, BibTeX
warnings for the handful of author-less `@misc` entries, and similar
cosmetics). Treat the first compile as a review pass.

## Importing into Overleaf

1. Zip **both** directories, preserving the tree structure:

   ```bash
   cd <repo-root>
   zip -r thesis-latex.zip thesis/latex thesis/figures
   ```

2. In Overleaf: *New Project → Upload Project* and select the zip.
3. Set the main document to `thesis/latex/main.tex`.
4. Compiler: pdfLaTeX (default). Overleaf runs the
   `pdflatex → bibtex → pdflatex × 2` cycle automatically; locally that
   sequence must be run by hand.

The figures are **not** copied into this directory: `main.tex` sets
`\graphicspath{{../figures/}}`, so the project must contain
`thesis/figures/*.png` alongside `thesis/latex/`. The figures use descriptive
names (`fig-01-workflow`, `fig-h1-dose-response` … `fig-h5-scorecard-icc`,
`fig-hotel-external-validity`) wired to the LaTeX by the `\label{fig:...}` map in
`thesis/figures/MANIFEST.md`, which is the single source of truth for the figure
set and its regeneration commands.

## Layout

| File | Contents |
|---|---|
| `main.tex` | report class (11pt, a4paper); title page (supervisor/date are `\newcommand` TODO placeholders); abstract; TOC; chapter inputs; bibliography |
| `chapters/01-introduction.tex` … `chapters/08-conclusion.tex` | one file per chapter |
| `appendix/a-provenance.tex` | run provenance for the primary study: the C1/C2/C3 campaign table, the claims→campaigns map, and integrity anchors |
| `references.bib` | derived from [`references.md`](../../references.md); papers as `@article`/`@inproceedings` with DOI, web/issue/KEP/blog sources as `@misc` with `howpublished` + `url` + a per-entry access date in the `note` field (most 2026-06-11) |

## Porting conventions

- Dependencies are deliberately minimal: `inputenc`/`fontenc`/`lmodern`,
  `amsmath` (for `\text{}` in math mode), `geometry`, `graphicx`, `booktabs`,
  `natbib` (+ `plainnat`), `hyperref`. No `siunitx`, no `tabularx`, no listings.
- Markdown `§x.y` / "Chapter N" / "Appendix A" / "Figure x.y" cross-references
  became `\S\ref{sec:…}` / `Chapter~\ref{ch:…}` / `Appendix~\ref{app:…}` /
  `Figure~\ref{fig:…}` with stable labels, so numbering stays correct if
  sections move.
- Inline code spans → `\texttt{…}` (with `_`, `#`, `%`, `&` escaped); bold →
  `\textbf{…}`; italics → `\emph{…}`; pipe tables → `booktabs` tables
  (non-floating, in `center` blocks, to preserve the manuscript's reading
  order); blockquotes → `quote` environments.
- Unicode math/symbols in the prose (×, ≈, ≤, ρ, σ², →, ↔, ⟂, …) were
  rewritten as LaTeX math (`$\times$`, `$\approx$`, `$\rho$`, …) for safe
  pdfLaTeX compilation; the Chapter 4 model formulas became display math.
- Citation rule: a Markdown link whose text is an author-year (e.g.
  "[Maricq et al. 2018](…)") became `\citep`/`\citet`; a link whose text is an
  identifier (an arXiv id, DOI, issue number, or file name) became `\href`.
  `\nocite{*}` in `main.tex` prints the full `references.bib` (the annotated
  bibliography source), so identifier-only works still appear in the
  bibliography.
- The thesis title is set in `main.tex` (`\thesistitle`); supervisor, examiner,
  programme, and date remain `\newcommand` TODO placeholders to fill before
  submission.
