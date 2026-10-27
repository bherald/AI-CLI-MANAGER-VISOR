"""Exercise new-thread handoff through real proxy subprocesses, without a provider."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from visor.providers import ProviderUnavailable, create_session


class CodexHandoffCase(unittest.TestCase):
    def test_fresh_zero_turn_thread_is_named_and_resumed_after_creator_exits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "provider-state.json"
            state.write_text("{}")
            fixture = Path(__file__).parent / "fixtures/codex_handoff_proxy.py"
            with patch("visor.providers.provider_command", return_value=[sys.executable, str(fixture), str(state)]), \
                    patch("visor.providers.ensure_codex_daemon"):
                self.assertEqual(create_session("codex", str(root), name="Fixture title"), "exact-thread")
            stored = json.loads(state.read_text())
            self.assertEqual(stored["calls"], ["initialize", "thread/start", "thread/name/set",
                                               "initialize", "thread/resume"])
            self.assertEqual(stored["turns"], [])
            self.assertEqual(stored["name"], "Fixture title")
            state.write_text(json.dumps({"drop_persistence": True}))
            with patch("visor.providers.provider_command", return_value=[sys.executable, str(fixture), str(state)]), \
                    patch("visor.providers.ensure_codex_daemon"), \
                    self.assertRaisesRegex(ProviderUnavailable, "not confirmed resumable"):
                create_session("codex", str(root))
