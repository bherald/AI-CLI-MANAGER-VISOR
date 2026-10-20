import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from visor.broker import Broker
from visor.providers import ProviderUnavailable, create_session, ensure_codex_daemon


class CodexStartupCase(unittest.TestCase):
    @patch("visor.providers.executable", return_value="codex")
    @patch("visor.providers.run")
    @patch("visor.providers.CodexRPC")
    def test_start_if_absent_then_verify_socket(self, rpc, run, _):
        order = Mock()
        order.attach_mock(run, "start")
        order.attach_mock(rpc, "socket")
        run.return_value.returncode = 0
        ensure_codex_daemon()
        run.assert_called_once_with(["codex", "app-server", "daemon", "start"], timeout=20)
        self.assertEqual([c[0] for c in order.mock_calls[:2]], ["start", "socket"])
        rpc.return_value.__enter__.assert_called_once()

    @patch("visor.providers.executable", return_value="codex")
    @patch("visor.providers.CodexRPC")
    @patch("visor.providers.run")
    def test_start_failure_timeout_and_refused_socket_are_reported(self, run, rpc, _):
        for outcome in (Mock(returncode=1), subprocess.TimeoutExpired("codex", 20)):
            with self.subTest(outcome=outcome):
                run.side_effect = outcome if isinstance(outcome, Exception) else None
                run.return_value = outcome
                with self.assertRaisesRegex(ProviderUnavailable, "no session was opened"):
                    ensure_codex_daemon()
                rpc.assert_not_called()
        run.side_effect = None
        run.return_value = Mock(returncode=0)
        rpc.side_effect = ProviderUnavailable("connection refused")
        with self.assertRaisesRegex(ProviderUnavailable, "connection refused"):
            ensure_codex_daemon()

    @patch("visor.providers.executable", return_value="codex")
    @patch("visor.providers.ensure_codex_daemon")
    @patch("visor.providers.CodexRPC")
    def test_new_thread_starts_server_before_rpc(self, rpc, ensure, _):
        with tempfile.TemporaryDirectory() as directory:
            rpc.return_value.__enter__.return_value.call.return_value = {"thread": {"id": "exact"}}
            self.assertEqual(create_session("codex", directory), "exact")
        ensure.assert_called_once()

    def test_resume_failure_does_not_create_ticket_or_claim_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            broker = Broker(root)
            key = broker.store.upsert_session("codex", "exact", "Fixture", directory)
            with patch("visor.broker.ensure_codex_daemon", side_effect=ProviderUnavailable("socket refused")), \
                    patch("visor.broker.terminal_launch") as launch:
                with self.assertRaisesRegex(ProviderUnavailable, "socket refused"):
                    broker.launch(key)
                launch.assert_not_called()
            self.assertEqual(list(root.glob("launch-*.json")), [])
            self.assertEqual(list(root.glob("session-*.json")), [])
            self.assertIsNone(broker.store.session(key)["window_token"])


if __name__ == "__main__":
    unittest.main()
