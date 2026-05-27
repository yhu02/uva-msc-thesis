"""Unit tests for strategy_runner helper functions."""

from chaosprobe.orchestrator.strategy_runner import (
    _compute_effective_timeout,
    _extract_chaos_duration,
    _parse_probe_timeout,
    _shell_escape,
)


class TestParseProbeTimeout:
    def test_seconds(self):
        assert _parse_probe_timeout("15s") == 15

    def test_milliseconds(self):
        assert _parse_probe_timeout("1500ms") == 1

    def test_milliseconds_rounds_to_min_1(self):
        assert _parse_probe_timeout("500ms") == 1

    def test_minutes(self):
        assert _parse_probe_timeout("2m") == 120

    def test_plain_integer(self):
        assert _parse_probe_timeout("10") == 10

    def test_whitespace(self):
        assert _parse_probe_timeout("  5s  ") == 5

    def test_empty_string(self):
        assert _parse_probe_timeout("") == 5

    def test_invalid_string(self):
        assert _parse_probe_timeout("abc") == 5

    def test_negative_seconds_clamped(self):
        # Negative values should clamp to 1
        assert _parse_probe_timeout("-5s") == 1

    def test_zero_seconds_clamped(self):
        assert _parse_probe_timeout("0s") == 1

    def test_zero_plain(self):
        assert _parse_probe_timeout("0") == 1


class TestExtractChaosDuration:
    def test_extracts_from_env(self):
        scenario = {
            "experiments": [
                {
                    "spec": {
                        "spec": {
                            "experiments": [
                                {
                                    "spec": {
                                        "components": {
                                            "env": [
                                                {"name": "TOTAL_CHAOS_DURATION", "value": "120"},
                                            ]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            ]
        }
        assert _extract_chaos_duration(scenario) == 120

    def test_fallback_to_60(self):
        assert _extract_chaos_duration({}) == 60
        assert _extract_chaos_duration({"experiments": []}) == 60

    def test_takes_max_across_experiments(self):
        scenario = {
            "experiments": [
                {
                    "spec": {
                        "spec": {
                            "experiments": [
                                {
                                    "spec": {
                                        "components": {
                                            "env": [
                                                {"name": "TOTAL_CHAOS_DURATION", "value": "30"},
                                            ]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                },
                {
                    "spec": {
                        "spec": {
                            "experiments": [
                                {
                                    "spec": {
                                        "components": {
                                            "env": [
                                                {"name": "TOTAL_CHAOS_DURATION", "value": "90"},
                                            ]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                },
            ]
        }
        # The floor is 60, so 30 is ignored; 90 > 60
        assert _extract_chaos_duration(scenario) == 90


class TestComputeEffectiveTimeout:
    def test_respects_user_timeout_when_larger(self):
        # No probes, chaos_duration=60, min = 60 + 0 + 120 = 180
        scenario = {"experiments": []}
        assert _compute_effective_timeout(scenario, 600) == 600

    def test_computes_minimum_with_probes(self):
        scenario = {
            "experiments": [
                {
                    "spec": {
                        "spec": {
                            "experiments": [
                                {
                                    "spec": {
                                        "components": {
                                            "env": [
                                                {"name": "TOTAL_CHAOS_DURATION", "value": "60"},
                                            ]
                                        },
                                        "probe": [
                                            {
                                                "runProperties": {
                                                    "probeTimeout": "10s",
                                                    "retry": "3",
                                                }
                                            }
                                        ],
                                    }
                                }
                            ]
                        }
                    }
                }
            ]
        }
        # chaos=60, probes: 10*(3+1)=40, min=60+2*40+120=260
        assert _compute_effective_timeout(scenario, 100) == 260

    def test_handles_malformed_retry(self):
        """Non-integer retry value should not crash — should default to 0."""
        scenario = {
            "experiments": [
                {
                    "spec": {
                        "spec": {
                            "experiments": [
                                {
                                    "spec": {
                                        "components": {
                                            "env": [
                                                {"name": "TOTAL_CHAOS_DURATION", "value": "60"},
                                            ]
                                        },
                                        "probe": [
                                            {
                                                "runProperties": {
                                                    "probeTimeout": "10s",
                                                    "retry": "invalid",
                                                }
                                            }
                                        ],
                                    }
                                }
                            ]
                        }
                    }
                }
            ]
        }
        # chaos=60, probes: 10*(0+1)=10 (retry defaults to 0), min=60+2*10+120=200
        assert _compute_effective_timeout(scenario, 100) == 200


class TestParseProbeTimeoutFloats:
    """Tests for float duration parsing (e.g. '1.5s')."""

    def test_float_seconds(self):
        assert _parse_probe_timeout("1.5s") == 1

    def test_float_seconds_rounds_down(self):
        assert _parse_probe_timeout("2.9s") == 2

    def test_float_minutes(self):
        assert _parse_probe_timeout("1.5m") == 90

    def test_float_milliseconds(self):
        assert _parse_probe_timeout("1500.0ms") == 1

    def test_float_plain(self):
        assert _parse_probe_timeout("2.5") == 2


class TestShellEscape:
    def test_plain_string(self):
        assert _shell_escape("hello") == "hello"

    def test_single_quote(self):
        assert _shell_escape("it's") == "it'\\''s"

    def test_multiple_quotes(self):
        assert _shell_escape("a'b'c") == "a'\\''b'\\''c"

    def test_empty_string(self):
        assert _shell_escape("") == ""

    def test_url_with_special_chars(self):
        url = "http://frontend.ns.svc.cluster.local/cart?user=test&qty=1"
        # No transformation needed — special chars are safe inside single quotes
        assert _shell_escape(url) == url
