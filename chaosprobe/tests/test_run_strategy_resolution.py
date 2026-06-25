"""Unit tests for the strategy-list resolution helpers extracted from ``run``.

These were previously inline in the ~440-line ``run`` Click command (a nested
``_sort_key`` and the ``--seeds`` expansion block), so the logic couldn't be
unit-tested. Extracting them to module scope makes them directly testable.
"""

import click
import pytest

from chaosprobe.commands.run_cmd import (
    _expand_random_seeds,
    _strategy_execution_order,
)
from chaosprobe.placement.strategy import PlacementStrategy


class TestStrategyExecutionOrder:
    def test_baseline_sorts_first(self):
        assert _strategy_execution_order("baseline") == -1

    def test_default_sorts_after_baseline(self):
        assert _strategy_execution_order("default") == 0

    def test_real_strategy_uses_enum_order(self):
        expected = PlacementStrategy("colocate").execution_order
        assert _strategy_execution_order("colocate") == expected

    def test_seed_suffix_stripped(self):
        assert _strategy_execution_order("random:42") == _strategy_execution_order("random")

    def test_unknown_name_sorts_with_default(self):
        assert _strategy_execution_order("totally-unknown") == 0


class TestExpandRandomSeeds:
    def test_no_seeds_returns_unchanged(self):
        assert _expand_random_seeds(["a", "random", "b"], None) == ["a", "random", "b"]

    def test_random_not_selected_returns_unchanged(self):
        assert _expand_random_seeds(["default", "spread"], "1,2") == ["default", "spread"]

    def test_expands_random_per_seed_preserving_others(self):
        assert _expand_random_seeds(["default", "random", "spread"], "1,2") == [
            "default",
            "random:1",
            "random:2",
            "spread",
        ]

    def test_tolerates_blank_and_whitespace_tokens(self):
        assert _expand_random_seeds(["random"], "1, ,2 ") == ["random:1", "random:2"]

    def test_non_integer_seed_raises(self):
        with pytest.raises(click.ClickException):
            _expand_random_seeds(["random"], "1,x")

    def test_all_blank_seeds_raises(self):
        with pytest.raises(click.ClickException):
            _expand_random_seeds(["random"], " , ")
