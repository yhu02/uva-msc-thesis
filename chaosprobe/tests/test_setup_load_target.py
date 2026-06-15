"""Tests for run_phases._setup_load_target (port-forward stale-reuse fix)."""

from unittest.mock import patch

from chaosprobe.orchestrator.run_phases import LOAD_TARGET_LOCAL_PORT, _setup_load_target


def test_explicit_target_url_short_circuits():
    url, port = _setup_load_target("ns", "steady", "http://example:80")
    assert url == "http://example:80" and port == LOAD_TARGET_LOCAL_PORT


def test_no_load_profile_returns_none():
    url, port = _setup_load_target("ns", None, None)
    assert url is None and port == LOAD_TARGET_LOCAL_PORT


@patch("chaosprobe.orchestrator.run_phases.pf")
def test_load_profile_frees_port_then_verifies_http(mock_pf):
    # The fix: free the orphan, establish fresh, verify via HTTP probe.
    mock_pf.http_reachable.return_value = True
    url, port = _setup_load_target("online-boutique", "steady", None)
    assert url == f"http://localhost:{LOAD_TARGET_LOCAL_PORT}"
    mock_pf.free_local_port.assert_called_once_with(LOAD_TARGET_LOCAL_PORT)
    mock_pf.ensure.assert_called_once()
    mock_pf.http_reachable.assert_called_once_with(f"{url}/")


@patch("chaosprobe.orchestrator.run_phases.pf")
def test_load_profile_warns_when_http_unreachable(mock_pf, capsys):
    mock_pf.http_reachable.return_value = False
    url, _ = _setup_load_target("online-boutique", "steady", None)
    assert url == f"http://localhost:{LOAD_TARGET_LOCAL_PORT}"  # returned for --target-url advice
    assert "not reachable" in capsys.readouterr().err
