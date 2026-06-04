"""The default run-strategy set has a single source of truth.

``DEFAULT_RUN_STRATEGIES`` is the one canonical, ordered list; ``run``'s
``--strategies`` default derives from it, and it is pinned to the
``PlacementStrategy`` enum and the reproduction doc so the ~10 places that used
to restate it can no longer silently drift (REVIEW.md W9).
"""

from pathlib import Path

from chaosprobe.placement.strategy import (
    CONTROL_STRATEGIES,
    DEFAULT_RUN_STRATEGIES,
    PlacementStrategy,
)


def test_canonical_set_equals_controls_plus_enum():
    expected = set(CONTROL_STRATEGIES) | {s.value for s in PlacementStrategy}
    assert set(DEFAULT_RUN_STRATEGIES) == expected
    # No duplicates / strays — a new enum member must be added here too.
    assert len(DEFAULT_RUN_STRATEGIES) == len(expected)


def test_controls_come_first_and_in_order():
    assert DEFAULT_RUN_STRATEGIES[: len(CONTROL_STRATEGIES)] == CONTROL_STRATEGIES


def test_run_cmd_default_derives_from_canonical():
    from chaosprobe.commands.run_cmd import run

    opt = next(p for p in run.params if p.name == "strategies")
    assert opt.default == ",".join(DEFAULT_RUN_STRATEGIES)


def test_reproduction_doc_command_matches_canonical():
    doc = (
        Path(__file__).parent.parent / "docs" / "how-to" / "reproducing-thesis-results.md"
    ).read_text()
    # The copy-paste reproduction command embeds the exact comma-joined list.
    assert ",".join(DEFAULT_RUN_STRATEGIES) in doc
