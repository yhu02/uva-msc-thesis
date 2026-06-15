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
def test_load_profile_uses_http_verified_helper(mock_pf):
    # The fix: route through the shared HTTP-verified heal helper.
    mock_pf.ensure_load_target.return_value = True
    url, port = _setup_load_target("online-boutique", "steady", None)
    assert url == f"http://localhost:{LOAD_TARGET_LOCAL_PORT}"
    mock_pf.ensure_load_target.assert_called_once_with(
        "frontend", "online-boutique", LOAD_TARGET_LOCAL_PORT, f"{url}/"
    )


@patch("chaosprobe.orchestrator.run_phases.pf")
def test_load_profile_warns_when_unreachable(mock_pf, capsys):
    mock_pf.ensure_load_target.return_value = False
    url, _ = _setup_load_target("online-boutique", "steady", None)
    assert url == f"http://localhost:{LOAD_TARGET_LOCAL_PORT}"  # returned for --target-url advice
    assert "not reachable" in capsys.readouterr().err
