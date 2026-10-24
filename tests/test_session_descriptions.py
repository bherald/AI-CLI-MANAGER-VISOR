import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from visor.providers import discover_codex
from visor.session_text import label, summary
from visor.store import Store


class DescriptionCase(unittest.TestCase):
    def test_provider_preview_labels_old_uuid_sessions_without_overwriting_custom_names(self):
        sid = "12345678-1234-1234-1234-123456789012"
        with patch("visor.providers.CodexRPC") as cls:
            cls.return_value.__enter__.return_value.call.return_value = {"data": [{"id": sid, "cwd": "/projects/fixture", "preview": "Fix the report export\nand preserve totals"}]}
            item = discover_codex()[0]
        self.assertEqual(item["name"], "Fix the report export and preserve totals")
        self.assertEqual(item["description"], item["name"])
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            key = store.upsert_session("codex", sid, sid[:12], "/projects/fixture")
            store.upsert_session(**item)
            self.assertEqual(store.session(key)["name"], item["name"])
            store.update_session(key, name="My deliberate title")
            store.upsert_session(**{**item, "name": "Different provider title", "description": ""})
            self.assertEqual(store.session(key)["name"], "My deliberate title")
            self.assertEqual(store.session(key)["description"], item["description"])
            self.assertEqual(Store(Path(tmp)).session(key)["description"], item["description"])

    def test_local_bounded_metadata_skips_coordination_and_retains_real_titles(self):
        self.assertEqual(summary("[VISOR message abc] internal envelope"), "")
        self.assertEqual(summary("<environment_context>internal"), "")
        self.assertLessEqual(len(summary("x" * 1000)), 240)
        self.assertEqual(label("My title", "Other text", "/project", "id"), "My title")
        self.assertIn("project", label(None, None, "/project", "12345678"))
