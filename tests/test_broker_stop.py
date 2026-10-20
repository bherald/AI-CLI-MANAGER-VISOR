import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from visor.broker import Client
from visor.process import hidden_options


class BrokerStopCase(unittest.TestCase):
    def test_stop_response_finishes_before_broker_exits(self):
        fixture = Path(__file__).parent / "fixtures/slow_stop_broker.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = subprocess.Popen(
                [sys.executable, str(fixture), str(root)], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env={**os.environ, "VISOR_SHELL_PATH": "0"},
                **hidden_options())
            try:
                deadline = time.monotonic() + 5
                while not (root / "broker.json").exists():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        self.fail("Test broker failed to start")
                    time.sleep(0.05)
                self.assertTrue(Client(root).call("stop"))
                process.wait(timeout=10)
                self.assertEqual(process.returncode, 0)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                process.communicate()
