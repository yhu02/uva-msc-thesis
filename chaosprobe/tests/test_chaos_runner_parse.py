"""Unit tests for chaos.runner._parse_execution_data.

This pure parser turns ChaosCenter's ``executionData`` JSON blob into
probe verdicts and raw probe-status dicts.  It is load-bearing for the
iteration outcome but was previously untested.
"""

import json

from chaosprobe.chaos.runner import (
    _extract_probe_verdicts_from_execution_data,
    _parse_execution_data,
)


def _execution_data_with(probe_statuses):
    """Wrap a list of probe-status dicts in the ChaosCenter executionData shape."""
    return {
        "nodes": {
            "node-1": {
                "chaosData": {
                    "chaosResult": {"status": {"probeStatuses": probe_statuses}},
                }
            }
        }
    }


class TestParseExecutionData:
    def test_none_returns_empty(self):
        result = _parse_execution_data(None)
        assert result == {"verdicts": {}, "rawProbeStatuses": {}}

    def test_empty_string_returns_empty(self):
        # Empty string is falsy → same empty default
        result = _parse_execution_data("")
        assert result == {"verdicts": {}, "rawProbeStatuses": {}}

    def test_passed_verdict_recognised(self):
        data = _execution_data_with([{"name": "http-frontend", "status": {"verdict": "Passed"}}])
        result = _parse_execution_data(json.dumps(data))
        assert result["verdicts"] == {"http-frontend": "Pass"}
        assert "http-frontend" in result["rawProbeStatuses"]

    def test_failed_verdict_recognised(self):
        data = _execution_data_with([{"name": "http-cart", "status": {"verdict": "Failed 👎"}}])
        result = _parse_execution_data(json.dumps(data))
        assert result["verdicts"] == {"http-cart": "Fail"}

    def test_unknown_verdict_is_unknown(self):
        data = _execution_data_with([{"name": "p1", "status": {"verdict": "InProgress"}}])
        result = _parse_execution_data(json.dumps(data))
        assert result["verdicts"] == {"p1": "Unknown"}

    def test_string_status_form(self):
        # Some ChaosCenter responses have status as a plain string rather
        # than a dict — the parser must still classify.
        data = _execution_data_with(
            [
                {"name": "p-pass", "status": "Passed"},
                {"name": "p-fail", "status": "Failed"},
            ]
        )
        result = _parse_execution_data(json.dumps(data))
        assert result["verdicts"]["p-pass"] == "Pass"
        assert result["verdicts"]["p-fail"] == "Fail"

    def test_accepts_dict_input_not_just_string(self):
        data = _execution_data_with([{"name": "p1", "status": {"verdict": "Passed"}}])
        result = _parse_execution_data(data)
        assert result["verdicts"] == {"p1": "Pass"}

    def test_multiple_nodes_merged(self):
        data = {
            "nodes": {
                "n1": {
                    "chaosData": {
                        "chaosResult": {
                            "status": {
                                "probeStatuses": [{"name": "a", "status": {"verdict": "Passed"}}]
                            }
                        }
                    }
                },
                "n2": {
                    "chaosData": {
                        "chaosResult": {
                            "status": {
                                "probeStatuses": [{"name": "b", "status": {"verdict": "Failed"}}]
                            }
                        }
                    }
                },
            }
        }
        result = _parse_execution_data(json.dumps(data))
        assert result["verdicts"] == {"a": "Pass", "b": "Fail"}

    def test_nodes_without_chaos_data_skipped(self):
        data = {
            "nodes": {
                "n1": {"phase": "Succeeded"},  # no chaosData
                "n2": {
                    "chaosData": {
                        "chaosResult": {
                            "status": {
                                "probeStatuses": [{"name": "p1", "status": {"verdict": "Passed"}}]
                            }
                        }
                    }
                },
            }
        }
        result = _parse_execution_data(json.dumps(data))
        assert result["verdicts"] == {"p1": "Pass"}

    def test_probe_without_name_skipped(self):
        data = _execution_data_with(
            [
                {"status": {"verdict": "Passed"}},  # no name
                {"name": "named", "status": {"verdict": "Passed"}},
            ]
        )
        result = _parse_execution_data(json.dumps(data))
        assert result["verdicts"] == {"named": "Pass"}

    def test_malformed_json_returns_empty(self):
        result = _parse_execution_data("{not valid json")
        assert result == {"verdicts": {}, "rawProbeStatuses": {}}

    def test_nodes_not_a_dict_returns_empty(self):
        data = {"nodes": ["unexpected", "list"]}
        result = _parse_execution_data(json.dumps(data))
        assert result == {"verdicts": {}, "rawProbeStatuses": {}}

    def test_first_classification_wins_via_setdefault(self):
        # If the same probe is reported on multiple nodes with different
        # outcomes, the **first** Pass/Fail/Unknown decision is locked
        # in via setdefault (parser is forgiving rather than strict).
        # Pass on one node + Unknown on another should remain Pass.
        data = {
            "nodes": {
                "n1": {
                    "chaosData": {
                        "chaosResult": {
                            "status": {
                                "probeStatuses": [{"name": "dup", "status": {"verdict": "Passed"}}]
                            }
                        }
                    }
                },
                "n2": {
                    "chaosData": {
                        "chaosResult": {
                            "status": {
                                "probeStatuses": [
                                    {"name": "dup", "status": {"verdict": "InProgress"}}
                                ]
                            }
                        }
                    }
                },
            }
        }
        # Pass takes precedence over the later Unknown
        result = _parse_execution_data(json.dumps(data))
        assert result["verdicts"]["dup"] == "Pass"


class TestExtractProbeVerdictsBackcompat:
    """Verify the back-compat verdicts-only accessor still works."""

    def test_returns_verdicts_dict_only(self):
        data = _execution_data_with([{"name": "p1", "status": {"verdict": "Passed"}}])
        result = _extract_probe_verdicts_from_execution_data(json.dumps(data))
        assert result == {"p1": "Pass"}

    def test_none_returns_empty_dict(self):
        assert _extract_probe_verdicts_from_execution_data(None) == {}
