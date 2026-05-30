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
