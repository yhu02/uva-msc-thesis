"""Tests for ``summarise_placement_match_rates`` — per-strategy intent-
vs-actual placement diff roll-up.

The thesis's per-strategy ranking only holds if the *intended* placement
actually applied.  The mutator records ``intendedActualDiff`` per
strategy after the rollout settles; this helper surfaces the matchRate
in the run-level summary so a defender can see at a glance whether the
scheduler overrode the nodeSelector for any strategy.
"""

from chaosprobe.orchestrator.run_phases import summarise_placement_match_rates


def _strategy_with_diff(matched=None, mismatched=None, match_rate=1.0):
    return {
        "placement": {
            "metadata": {
                "intendedActualDiff": {
                    "matched": matched or [],
                    "mismatched": mismatched or [],
                    "matchRate": match_rate,
                }
            }
        }
    }


class TestSummarisePlacementMatchRates:
    def test_extracts_match_rate_per_strategy(self):
        strategies = {
            "spread": _strategy_with_diff(
                matched=[{"deployment": "a", "node": "w1"}],
                mismatched=[],
                match_rate=1.0,
            ),
            "adversarial": _strategy_with_diff(
                matched=[{"deployment": "a", "node": "w1"}],
                mismatched=[{"deployment": "b", "intendedNode": "w1", "actualNodes": ["w2"]}],
                match_rate=0.5,
            ),
        }
        out = summarise_placement_match_rates(strategies)
        assert out["spread"] == {"matchRate": 1.0, "matched": 1, "mismatched": 0}
        assert out["adversarial"] == {"matchRate": 0.5, "matched": 1, "mismatched": 1}

    def test_strategy_without_placement_omitted(self):
        """Baseline / default strategies often have no `intendedActualDiff` —
        they leave placement to the scheduler and should not appear in the
        per-strategy match-rate summary."""
        strategies = {
            "baseline": {"placement": {"strategy": "baseline"}},  # no metadata
            "default": {"placement": {}},  # no metadata at all
            "spread": _strategy_with_diff(match_rate=1.0),
        }
        out = summarise_placement_match_rates(strategies)
        assert set(out.keys()) == {"spread"}

    def test_missing_match_rate_skipped(self):
        strategies = {
            "x": {
                "placement": {
                    "metadata": {
                        "intendedActualDiff": {
                            "matched": [],
                            "mismatched": [],
                            # No matchRate key
                        }
                    }
                }
            }
        }
        assert summarise_placement_match_rates(strategies) == {}

    def test_diff_not_dict_skipped(self):
        strategies = {"x": {"placement": {"metadata": {"intendedActualDiff": "not-a-dict"}}}}
        assert summarise_placement_match_rates(strategies) == {}

    def test_none_and_empty_inputs(self):
        assert summarise_placement_match_rates({}) == {}
        assert summarise_placement_match_rates(None) == {}

    def test_strategy_value_can_be_none(self):
        """A strategy entry left as None (e.g. partial-results save before
        the strategy ran) must not crash."""
        strategies = {"x": None, "spread": _strategy_with_diff(match_rate=0.9)}
        out = summarise_placement_match_rates(strategies)
        assert "x" not in out
        assert out["spread"]["matchRate"] == 0.9
