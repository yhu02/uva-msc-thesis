"""Tests for the single-study figure CLI glue.

The figure-rendering functions read archived C1/C2/C3 campaign data (gitignored,
absent in CI) and are verified by regenerating + visually inspecting the PNGs and
by the thesis LaTeX compile. Here we cover the data-free logic: figure-spec
parsing and the figure registry.
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "scripts")

import single_study_figures as ssf  # noqa: E402


def test_all_figures_registry() -> None:
    # One schematic + one figure per confirmatory/descriptive hypothesis H1–H5.
    assert ssf.ALL_FIGURES == ("workflow", "h1", "h2", "h3", "h4", "h5")


def test_parse_figures_all() -> None:
    assert ssf.parse_figures("all") == list(ssf.ALL_FIGURES)
    assert ssf.parse_figures(" ALL ") == list(ssf.ALL_FIGURES)


def test_parse_figures_subset_and_whitespace() -> None:
    assert ssf.parse_figures("h1,h5") == ["h1", "h5"]
    assert ssf.parse_figures(" h2 , h3 ,") == ["h2", "h3"]


def test_parse_figures_rejects_unknown() -> None:
    with pytest.raises(SystemExit) as excinfo:
        ssf.parse_figures("h1,h9")
    assert "h9" in str(excinfo.value)
