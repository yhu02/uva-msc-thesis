"""Tests for the EndpointSlice snapshot summarizer."""

from unittest.mock import MagicMock

from chaosprobe.metrics.endpointslices import summarize_endpoint_slices


def _endpoint(ready=None, terminating=None, has_conditions=True):
    ep = MagicMock()
    if has_conditions:
        cond = MagicMock()
        cond.ready = ready
        cond.terminating = terminating
        ep.conditions = cond
    else:
        ep.conditions = None
    return ep


def _slice(service_name, endpoints, labels=None):
    sl = MagicMock()
    sl.metadata = MagicMock()
    if labels is not None:
        sl.metadata.labels = labels
    else:
        sl.metadata.labels = {"kubernetes.io/service-name": service_name} if service_name else {}
    sl.endpoints = endpoints
    return sl


class TestSummarizeEndpointSlices:
    def test_empty_input(self):
        assert summarize_endpoint_slices([]) == {"services": {}}

    def test_classifies_ready_terminating_not_ready(self):
        slices = [
            _slice(
                "frontend",
                [
                    _endpoint(ready=True, terminating=False),
                    _endpoint(ready=True, terminating=False),
                    _endpoint(ready=False, terminating=True),
                    _endpoint(ready=False, terminating=False),
                ],
            )
        ]
        out = summarize_endpoint_slices(slices)
        assert out["services"]["frontend"] == {
            "ready": 2,
            "terminating": 1,
            "notReady": 1,
            "total": 4,
        }

    def test_terminating_takes_precedence_over_ready(self):
        # A pod can be both ready and terminating during graceful shutdown;
        # it must count as terminating, the kill-cycle's transient state.
        out = summarize_endpoint_slices([_slice("cart", [_endpoint(ready=True, terminating=True)])])
        assert out["services"]["cart"] == {
            "ready": 0,
            "terminating": 1,
            "notReady": 0,
            "total": 1,
        }

    def test_merges_multiple_slices_for_same_service(self):
        slices = [
            _slice("checkout", [_endpoint(ready=True, terminating=False)]),
            _slice("checkout", [_endpoint(ready=True, terminating=False)]),
        ]
        out = summarize_endpoint_slices(slices)
        assert out["services"]["checkout"]["ready"] == 2
        assert out["services"]["checkout"]["total"] == 2

    def test_slice_without_service_label_skipped(self):
        out = summarize_endpoint_slices(
            [_slice(None, [_endpoint(ready=True, terminating=False)], labels={})]
        )
        assert out == {"services": {}}

    def test_missing_conditions_counts_as_not_ready(self):
        out = summarize_endpoint_slices([_slice("ad", [_endpoint(has_conditions=False)])])
        assert out["services"]["ad"] == {
            "ready": 0,
            "terminating": 0,
            "notReady": 1,
            "total": 1,
        }

    def test_slice_with_no_endpoints(self):
        out = summarize_endpoint_slices([_slice("empty", [])])
        assert out["services"]["empty"] == {
            "ready": 0,
            "terminating": 0,
            "notReady": 0,
            "total": 0,
        }

    def test_services_sorted_by_name(self):
        slices = [
            _slice("zeta", [_endpoint(ready=True, terminating=False)]),
            _slice("alpha", [_endpoint(ready=True, terminating=False)]),
        ]
        out = summarize_endpoint_slices(slices)
        assert list(out["services"].keys()) == ["alpha", "zeta"]
