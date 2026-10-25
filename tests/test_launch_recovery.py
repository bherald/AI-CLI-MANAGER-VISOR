import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from visor.broker import Broker
from visor.platforms import WindowUnavailable, _desktop_terminal_environment, _start_linux_terminal


class LaunchRecoveryCase(unittest.TestCase):
    def test_nested_frozen_library_path_does_not_reach_system_terminal(self):
        for original, expected in (("/bundle/_internal", None),
                                   ("/bundle/_internal:/system/lib:/bundle/_internal/Qt/lib", "/system/lib")):
            env = {"LD_LIBRARY_PATH": "/bundle/_internal:/bundle/_internal",
                   "LD_LIBRARY_PATH_ORIG": original, "QT_PLUGIN_PATH": "/bundle/plugins",
                   "DISPLAY": ":10", "PATH": "/usr/bin"}
            with self.subTest(original=original), patch.dict(os.environ, env, clear=True), \
                    patch("visor.platforms.sys.frozen", True, create=True), \
                    patch("visor.platforms.sys._MEIPASS", "/bundle/_internal", create=True):
                clean = _desktop_terminal_environment()
            self.assertEqual(clean.get("LD_LIBRARY_PATH"), expected)
            self.assertNotIn("LD_LIBRARY_PATH_ORIG", clean)
            self.assertNotIn("QT_PLUGIN_PATH", clean)
            self.assertEqual(clean["DISPLAY"], ":10")

    def test_native_loader_failure_is_reported_before_launch_success(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(WindowUnavailable, "exit 7"):
            _start_linux_terminal([sys.executable, "-c",
                "import sys;sys.stderr.write('GLIBCXX fixture');sys.exit(7)"], folder)

    def test_live_terminal_is_retained_after_startup_probe(self):
        with tempfile.TemporaryDirectory() as folder:
            child = _start_linux_terminal([sys.executable, "-c", "import time;time.sleep(10)"], folder)
            try:
                self.assertIsNone(child.poll())
            finally:
                child.terminate()
                child.wait(timeout=5)

    def test_failed_unconsumed_launch_can_be_retried_without_stale_window(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            broker = Broker(root)
            key = broker.store.upsert_session("codex", "fixture", "Fixture", folder,
                                             status="disconnected")
            with patch("visor.broker.ensure_codex_daemon"), \
                    patch("visor.broker.session_command", return_value=["fixture"]), \
                    patch("visor.broker.terminal_launch", side_effect=WindowUnavailable("loader failed")):
                for _ in range(2):
                    with self.assertRaisesRegex(WindowUnavailable, "loader failed"):
                        broker.launch(key)
                    self.assertIsNone(broker.store.session(key)["window_token"])
                    self.assertEqual(list(root.glob("launch-*.json")), [])
                    self.assertEqual(list(root.glob("session-*.json")), [])

    def test_consumed_ticket_preserves_uncertain_launch_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            broker = Broker(root)
            key = broker.store.upsert_session("codex", "fixture", "Fixture", folder,
                                             status="disconnected")
            def uncertain(*args, **kwargs):
                next(root.glob("launch-*.json")).unlink()
                raise WindowUnavailable("unknown outcome")
            with patch("visor.broker.ensure_codex_daemon"), \
                    patch("visor.broker.session_command", return_value=["fixture"]), \
                    patch("visor.broker.terminal_launch", side_effect=uncertain), \
                    self.assertRaisesRegex(WindowUnavailable, "unknown outcome"):
                broker.launch(key)
            self.assertTrue(broker.store.session(key)["window_token"])
            self.assertIn("uncertain", broker.store.session(key)["latest"])
