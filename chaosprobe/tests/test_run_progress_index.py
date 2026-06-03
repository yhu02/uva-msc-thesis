"""Unit tests for the multi-fault strategy progress index.

The ``[idx/total]`` progress counter in the ~440-line ``run`` Click command
used a per-fault ``enumerate(strategy_list, 1)`` index against a *global*
``total = len(strategy_list) * len(fault_scenarios)``. The second fault then
reprinted ``[1/16]`` .. ``[8/16]`` instead of ``[9/16]`` .. ``[16/16]``, which
looks like the run restarted. ``_global_strategy_index`` makes the index
continuous across faults; this pins that behaviour.
"""

from chaosprobe.commands.run_cmd import _global_strategy_index


class TestGlobalStrategyIndex:
    def test_first_fault_matches_strategy_position(self):
        # fault_pos 0 -> index equals the 1-based strategy position.
        assert _global_strategy_index(0, 1, 8) == 1
        assert _global_strategy_index(0, 8, 8) == 8

    def test_second_fault_continues_not_restarts(self):
        # The regression: second fault must continue at 9..16, not 1..8.
        assert _global_strategy_index(1, 1, 8) == 9
        assert _global_strategy_index(1, 8, 8) == 16

    def test_third_fault_offsets_by_two_blocks(self):
        assert _global_strategy_index(2, 1, 8) == 17
        assert _global_strategy_index(2, 5, 8) == 21

    def test_full_matrix_is_a_contiguous_sequence(self):
        n_faults, n_strategies = 2, 8
        produced = [
            _global_strategy_index(fault_pos, strat_pos, n_strategies)
            for fault_pos in range(n_faults)
            for strat_pos in range(1, n_strategies + 1)
        ]
        assert produced == list(range(1, n_faults * n_strategies + 1))

    def test_single_fault_is_unchanged(self):
        # Single-fault runs already counted correctly; keep it that way.
        assert [_global_strategy_index(0, s, 4) for s in range(1, 5)] == [1, 2, 3, 4]
