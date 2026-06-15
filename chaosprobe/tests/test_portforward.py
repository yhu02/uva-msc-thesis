"""Tests for orchestrator.portforward helpers."""

import signal
from unittest.mock import MagicMock, patch

from chaosprobe.orchestrator import portforward


class TestCheckPort:
    @patch("socket.socket")
    def test_returns_true_when_connect_succeeds(self, mock_socket):
        sock = MagicMock()
        mock_socket.return_value.__enter__.return_value = sock

        assert portforward.check_port("localhost", 8080) is True
        sock.connect.assert_called_once_with(("localhost", 8080))

    @patch("socket.socket")
    def test_returns_false_when_connect_refused(self, mock_socket):
        sock = MagicMock()
        sock.connect.side_effect = ConnectionRefusedError()
        mock_socket.return_value.__enter__.return_value = sock

        assert portforward.check_port("localhost", 9) is False

    @patch("socket.socket")
    def test_returns_false_on_oserror(self, mock_socket):
        sock = MagicMock()
        sock.connect.side_effect = OSError("network unreachable")
        mock_socket.return_value.__enter__.return_value = sock

        assert portforward.check_port("10.0.0.1", 1234) is False


class TestHttpReachable:
    @patch("urllib.request.urlopen")
    def test_ok_response_is_reachable(self, mock_open):
        mock_open.return_value.__enter__.return_value = MagicMock()
        assert portforward.http_reachable("http://localhost:8089/") is True

    @patch("urllib.request.urlopen")
    def test_http_error_status_still_reachable(self, mock_open):
        import urllib.error

        mock_open.side_effect = urllib.error.HTTPError("u", 500, "err", {}, None)
        assert portforward.http_reachable("http://localhost:8089/") is True

    @patch("urllib.request.urlopen")
    def test_connection_reset_is_unreachable(self, mock_open):
        mock_open.side_effect = ConnectionResetError(104, "reset")
        assert portforward.http_reachable("http://localhost:8089/") is False


class TestFreeLocalPort:
    @patch("chaosprobe.orchestrator.portforward.time.sleep", lambda *_: None)
    @patch("chaosprobe.orchestrator.portforward.os.kill")
    @patch("chaosprobe.orchestrator.portforward._orphan_pids")
    def test_sigterm_kills_orphans_confirmed_gone(self, mock_pids, mock_kill):
        # found [111,222]; both gone after SIGTERM -> 2 confirmed killed, no SIGKILL.
        mock_pids.side_effect = [[111, 222], [], []]
        assert portforward.free_local_port(8089) == 2
        assert mock_kill.call_count == 2  # SIGTERM x2, no SIGKILL

    @patch("chaosprobe.orchestrator.portforward._orphan_pids", return_value=[])
    def test_no_orphans_returns_zero(self, _mock_pids):
        assert portforward.free_local_port(8089) == 0

    @patch("chaosprobe.orchestrator.portforward.time.sleep", lambda *_: None)
    @patch("chaosprobe.orchestrator.portforward.os.kill")
    @patch("chaosprobe.orchestrator.portforward._orphan_pids")
    def test_escalates_to_sigkill_when_sigterm_survives(self, mock_pids, mock_kill):
        # found [111]; survives SIGTERM (still alive), then gone after SIGKILL.
        mock_pids.side_effect = [[111], [111], []]
        assert portforward.free_local_port(8089) == 1
        # SIGTERM then SIGKILL on the survivor.
        assert [c.args[1] for c in mock_kill.call_args_list] == [signal.SIGTERM, signal.SIGKILL]

    @patch("chaosprobe.orchestrator.portforward.time.sleep", lambda *_: None)
    @patch("chaosprobe.orchestrator.portforward.os.kill", side_effect=ProcessLookupError())
    @patch("chaosprobe.orchestrator.portforward._orphan_pids")
    def test_kill_errors_tolerated_and_survivor_not_counted(self, mock_pids, _mock_kill):
        # kill raises but the orphan is still present at the end -> 0 confirmed gone.
        mock_pids.side_effect = [[111], [111], [111]]
        assert portforward.free_local_port(8089) == 0


class TestEnsureLoadTarget:
    @patch("chaosprobe.orchestrator.portforward.http_reachable")
    @patch("chaosprobe.orchestrator.portforward.free_local_port")
    @patch("chaosprobe.orchestrator.portforward.ensure")
    def test_reachable_skips_heal(self, mock_ensure, mock_free, mock_http):
        # Live tunnel: probe passes -> no kill/restart.
        mock_http.return_value = True
        assert (
            portforward.ensure_load_target("frontend", "ns", 8089, "http://localhost:8089/") is True
        )
        mock_free.assert_not_called()
        mock_ensure.assert_not_called()

    @patch("chaosprobe.orchestrator.portforward.http_reachable")
    @patch("chaosprobe.orchestrator.portforward.free_local_port")
    @patch("chaosprobe.orchestrator.portforward.ensure")
    def test_stale_tunnel_is_healed(self, mock_ensure, mock_free, mock_http):
        # Stale on first probe (the bug case), reachable after one heal.
        mock_http.side_effect = [False, True]
        assert (
            portforward.ensure_load_target("frontend", "ns", 8089, "http://localhost:8089/") is True
        )
        mock_free.assert_called_once_with(8089)
        mock_ensure.assert_called_once()

    @patch("chaosprobe.orchestrator.portforward.http_reachable")
    @patch("chaosprobe.orchestrator.portforward.free_local_port")
    @patch("chaosprobe.orchestrator.portforward.ensure")
    def test_never_reachable_returns_false_after_two_heals(self, mock_ensure, mock_free, mock_http):
        mock_http.return_value = False
        assert (
            portforward.ensure_load_target("frontend", "ns", 8089, "http://localhost:8089/")
            is False
        )
        assert mock_free.call_count == 2  # two heal attempts


class TestOrphanPids:
    @patch("subprocess.run")
    def test_parses_pgrep_pids(self, mock_run):
        mock_run.return_value = MagicMock(stdout="111\n222\nbad\n")
        assert portforward._orphan_pids(8089) == [111, 222]

    @patch("subprocess.run", side_effect=OSError("pgrep missing"))
    def test_pgrep_failure_returns_empty(self, _mock_run):
        assert portforward._orphan_pids(8089) == []
