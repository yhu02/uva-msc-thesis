"""Tests for the multi-seed strategy-name parsing helper.

``chaosprobe run --seeds 42,137`` expands the ``random`` strategy into
``random:42``, ``random:137``, ... so downstream tooling sees them as
distinguishable "strategies".  This helper splits ``random:42`` back
into ``("random", 42)`` for the enum lookup and the apply_strategy seed.
"""

from chaosprobe.orchestrator.strategy_runner import _parse_strategy_name


class TestParseStrategyName:
    def test_plain_name_returns_none_seed(self):
        assert _parse_strategy_name("random") == ("random", None)
        assert _parse_strategy_name("baseline") == ("baseline", None)
        assert _parse_strategy_name("colocate") == ("colocate", None)

    def test_random_with_seed_extracts_int(self):
        assert _parse_strategy_name("random:42") == ("random", 42)
        assert _parse_strategy_name("random:137") == ("random", 137)
        assert _parse_strategy_name("random:0") == ("random", 0)

    def test_negative_seed(self):
        assert _parse_strategy_name("random:-1") == ("random", -1)

    def test_non_integer_suffix_treated_as_plain_name(self):
        # When the suffix isn't a clean int, fall back to the literal
        # name so downstream code can fail with a clear error rather
        # than silently using a wrong seed.
        assert _parse_strategy_name("random:abc") == ("random:abc", None)

    def test_other_strategies_with_colon(self):
        # The convention is only documented for random, but the parser
        # is generic — it doesn't reject other strategies.  This keeps
        # the helper composable if we ever multi-seed something else.
        assert _parse_strategy_name("colocate:1") == ("colocate", 1)
