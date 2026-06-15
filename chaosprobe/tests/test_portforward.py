"""Tests for orchestrator.portforward helpers."""

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
    @patch("subprocess.run")
    def test_kills_orphans_on_port(self, mock_run):
        # pgrep returns two pids; both get killed.
        pgrep = MagicMock(stdout="111\n222\n")
        mock_run.side_effect = [pgrep, MagicMock(), MagicMock()]
        assert portforward.free_local_port(8089) == 2
        assert mock_run.call_count == 3  # 1 pgrep + 2 kills

    @patch("subprocess.run")
    def test_no_orphans_returns_zero(self, mock_run):
        mock_run.return_value = MagicMock(stdout="")
        assert portforward.free_local_port(8089) == 0
        assert mock_run.call_count == 1  # only the pgrep

    @patch("subprocess.run")
    def test_pgrep_failure_returns_zero(self, mock_run):
        mock_run.side_effect = OSError("pgrep missing")
        assert portforward.free_local_port(8089) == 0

    @patch("chaosprobe.orchestrator.portforward.time.sleep", lambda *_: None)
    @patch("subprocess.run")
    def test_kill_failure_is_tolerated(self, mock_run):
        pgrep = MagicMock(stdout="111\n")
        mock_run.side_effect = [pgrep, OSError("kill failed")]
        assert portforward.free_local_port(8089) == 0  # kill raised -> not counted
