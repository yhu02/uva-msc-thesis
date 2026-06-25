"""Tests for ResultCollector verdict/probe helpers and CRD readers.

The pure helpers are exercised on a bare instance (``__new__``) so no
kubeconfig / cluster is required; the CRD readers get a mocked custom API.
"""

from unittest.mock import MagicMock

from chaosprobe.collector.result_collector import ResultCollector


def _collector():
    rc = ResultCollector.__new__(ResultCollector)
    rc.namespace = "test-ns"
    rc.custom_api = MagicMock()
    return rc


class TestDetermineVerdict:
    def test_chaos_result_pass(self):
        assert _collector()._determine_verdict({"chaosResult": {"verdict": "Pass"}}) == "Pass"

    def test_chaos_result_fail(self):
        assert _collector()._determine_verdict({"chaosResult": {"verdict": "Fail"}}) == "Fail"

    def test_engine_status_fallback(self):
        result = {"chaosResult": {}, "engineStatus": {"experiments": [{"verdict": "Pass"}]}}
        assert _collector()._determine_verdict(result) == "Pass"

    def test_awaited_default(self):
        assert _collector()._determine_verdict({}) == "Awaited"


class TestCalculateProbeSuccess:
    def test_returns_percentage(self):
        assert (
            _collector()._calculate_probe_success({"chaosResult": {"probeSuccessPercentage": 87.5}})
            == 87.5
        )

    def test_zero_without_chaos_result(self):
        assert _collector()._calculate_probe_success({}) == 0.0


class TestGetEngineStatus:
    def test_returns_status_dict(self):
        rc = _collector()
        rc.custom_api.get_namespaced_custom_object.return_value = {
            "status": {"engineStatus": "completed"}
        }
        assert rc._get_engine_status("eng")["engineStatus"] == "completed"


class TestGetChaosResult:
    def test_returns_exact_match(self):
        rc = _collector()
        rc.custom_api.get_namespaced_custom_object.return_value = {
            "metadata": {"name": "eng-pod-delete"}
        }
        assert rc._get_chaos_result("eng", "pod-delete") == {"metadata": {"name": "eng-pod-delete"}}
