# LaTeX port of the thesis (Overleaf-ready scaffold)

This directory is a hand-written LaTeX port of the canonical Markdown
manuscript in [`thesis/`](../) (`00-abstract.md` … `09-appendix-provenance.md`;
`10-defense.md` is a viva sheet, not a chapter, and is not ported). The
Markdown remains the source of truth for wording: **no content changes** were
made in the port.

## ⚠️ COMPILE-UNVERIFIED

This port was produced on a machine with **no TeX toolchain and no pandoc** —
it has **never been compiled**. The only mechanical gate it passed is a
brace/`\begin`–`\end` environment-pairing check. Expect minor fixups on the
first Overleaf compile (overfull boxes in the wide provenance table, BibTeX
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
`thesis/figures/*.png` alongside `thesis/latex/`. The figure files map to
in-text numbers via `thesis/figures/MANIFEST.md` (fig-01 = Fig 3.1,
fig-02…fig-09 = Figs 5.1…5.8); the chapter files embed them in that order so
the report-class auto-numbering reproduces the manuscript's figure numbers.

## Layout

| File | Contents |
|---|---|
| `main.tex` | report class (11pt, a4paper); title page (supervisor/date are `\newcommand` TODO placeholders); abstract; TOC; chapter inputs; bibliography |
| `chapters/01-introduction.tex` … `chapters/08-conclusion.tex` | one file per chapter, ported 1:1 from `thesis/01-…md` … `thesis/08-…md` |
| `appendix/a-provenance.tex` | run provenance for the single pre-registered study: the C1/C2/C3 campaign table, the claims→campaigns map, and integrity anchors |
| `references.bib` | derived from [`references.md`](../../references.md); papers as `@article`/`@inproceedings` with DOI, web/issue/KEP/blog sources as `@misc` with `howpublished` + `url` + access date 2026-06-11 |

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
- The thesis title in `main.tex` is taken from the defense script
  (`presentation_script.md`), since `thesis/README.md` carries no title line;
  supervisor, examiner, programme, and date are TODO placeholders.
