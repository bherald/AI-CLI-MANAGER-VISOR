"""A failed optional listener thread must release its bound socket promptly."""
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from visor.prism_web import PrismWebServer


class PrismWebStartFailureCase(unittest.TestCase):
    def test_thread_start_failure_closes_without_waiting_for_a_server_loop(self):
        with tempfile.TemporaryDirectory() as folder:
            server = PrismWebServer(Path(folder), client=object())
            address = server.httpd.server_address
            with (patch("visor.prism_web.threading.Thread.start", side_effect=RuntimeError("No worker available")),
                  self.assertRaisesRegex(RuntimeError, "No worker")):
                server.start()
            with patch.object(server.httpd, "shutdown") as shutdown:
                server.close()
                shutdown.assert_not_called()
            server.close()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as replacement:
                replacement.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                replacement.bind(address)


if __name__ == "__main__":
    unittest.main()
