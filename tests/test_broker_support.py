import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from broker_support import wait_for_broker


class BrokerReadinessCase(unittest.TestCase):
    def test_cold_start_can_take_more_than_the_old_four_second_budget(self):
        clock = [0]
        process = Mock()
        process.poll.return_value = None
        client = Mock()

        def ping(action):
            if clock[0] < 6:
                raise FileNotFoundError("Not initialized yet")
            return {"running": True}

        client.call.side_effect = ping
        with patch("broker_support.Client", return_value=client), \
                patch("broker_support.time.monotonic", side_effect=lambda: clock[0]), \
                patch("broker_support.time.sleep", side_effect=lambda _: clock.__setitem__(0, clock[0] + 1)):
            self.assertIs(wait_for_broker(process, Path("unused")), client)
        self.assertEqual(clock[0], 6)
        process.terminate.assert_not_called()

    def test_crash_reports_captured_stderr_without_waiting_out_deadline(self):
        process = Mock(returncode=7)
        process.poll.return_value = 7
        process.communicate.return_value = (b"", b"Synthetic import failure")
        with self.assertRaisesRegex(RuntimeError, "status 7.*|Synthetic import failure") as error:
            wait_for_broker(process, Path("unused"))
        self.assertIn("Synthetic import failure", str(error.exception))
        process.terminate.assert_not_called()

    def test_timeout_reaps_owned_process_and_keeps_diagnostics(self):
        process = Mock(returncode=-9)
        process.poll.return_value = None
        process.communicate.side_effect = [subprocess.TimeoutExpired("fixture", 5),
                                           (b"", b"Synthetic startup progress")]
        with self.assertRaisesRegex(RuntimeError, "timed out") as error:
            wait_for_broker(process, Path("unused"), timeout=0)
        self.assertIn("Synthetic startup progress", str(error.exception))
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
