import os
import shutil
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path

from visor.process import hidden_options


class InstanceCase(unittest.TestCase):
    def setUp(self):
        self.parent = Path(__file__).resolve().parents[1] / ".cache" / "tests"
        self.root = self.parent / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.processes = []

    def command(self, root=None):
        return [sys.executable, str(Path(__file__).parent / "fixtures" / "instance_host.py"), str(root or self.root)]

    def start(self, root=None):
        root = root or self.root
        process = subprocess.Popen(self.command(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, **hidden_options())
        self.processes.append(process)
        for _ in range(100):
            if (root / "ready").exists():
                return process
            if process.poll() is not None:
                self.fail(process.communicate()[1].decode())
            time.sleep(0.05)
        self.fail("Instance did not start")

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)
        self.assertTrue(self.root.resolve().is_relative_to(self.parent.resolve()))
        shutil.rmtree(self.root)

    def test_second_launch_activates_owner_then_exits(self):
        owner = self.start()
        second = subprocess.run(self.command(), check=False, capture_output=True, timeout=10, **hidden_options())
        self.assertEqual(second.returncode, 0, second.stderr.decode())
        self.assertIn(b"forwarded", second.stdout)
        self.assertTrue((self.root / "activated").exists())
        self.assertIsNone(owner.poll())

    def test_crashed_owner_can_be_restarted(self):
        owner = self.start()
        owner.kill()
        owner.communicate(timeout=5)
        (self.root / "ready").unlink()
        replacement = self.start()
        self.assertIsNone(replacement.poll())

    def test_separate_data_directories_are_independent(self):
        first = self.start()
        other = self.root / "other"
        other.mkdir()
        second = self.start(other)
        self.assertIsNone(first.poll())
        self.assertIsNone(second.poll())
