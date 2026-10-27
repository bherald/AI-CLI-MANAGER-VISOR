"""Provider discovery must not preserve stale names or unproven liveness."""
import sqlite3
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from visor.broker import Broker
from visor.store import Store


class DiscoveryReconciliationCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.broker = Broker(self.root)
        self.store = self.broker.store
        self.sid = str(uuid.uuid4())

    def test_provider_renames_follow_refresh_and_explicit_alias_survives(self):
        key = self.store.upsert_session("codex", self.sid, "Old provider title", str(self.root))
        self.store.upsert_session("codex", self.sid, "Current provider title", str(self.root))
        self.assertEqual(self.store.session(key)["name"], "Current provider title")
        self.broker.dispatch("rename", {"session": key, "name": "My alias"}, "operator")
        self.store.upsert_session("codex", self.sid, "New provider title", str(self.root))
        session = self.store.session(key)
        self.assertEqual(session["name"], "My alias")
        self.assertEqual(session["provider_name"], "New provider title")
        self.assertTrue(session["name_is_alias"])
        self.assertEqual(Store(self.root).session(key)["provider_name"], "New provider title")
        self.broker.dispatch("rename", {"session": key, "name": self.sid[:12]}, "operator")
        self.store.upsert_session("codex", self.sid, "Another provider title", str(self.root))
        self.assertEqual(self.store.session(key)["name"], self.sid[:12], "An explicit alias may resemble a placeholder")

    def test_reregister_applies_requested_alias_without_losing_provider_title(self):
        key = self.store.upsert_session("codex", self.sid, "Provider title", str(self.root))
        result = self.broker.dispatch("register", {"provider": "codex", "native_id": self.sid,
            "name": "Requested title", "cwd": str(self.root)}, "operator")
        self.assertEqual(result, key)
        self.assertEqual(self.store.session(key)["name"], "Requested title")
        self.assertEqual(self.store.session(key)["provider_name"], "Provider title")

    def test_legacy_names_preserved_as_possible_aliases_during_migration(self):
        # Create an actual prior schema by dropping only the new additive columns.
        key = self.store.upsert_session("codex", self.sid, "Legacy custom name", str(self.root))
        with sqlite3.connect(self.store.path) as db:
            db.execute("ALTER TABLE sessions DROP COLUMN provider_name")
            db.execute("ALTER TABLE sessions DROP COLUMN name_is_alias")
        migrated = Store(self.root)
        migrated.upsert_session("codex", self.sid, "Current provider title", str(self.root))
        session = migrated.session(key)
        self.assertEqual(session["name"], "Legacy custom name")
        self.assertEqual(session["provider_name"], "Current provider title")
        self.assertTrue(session["name_is_alias"])

    def refresh(self, sessions=(), warning="", error=None):
        with patch("visor.broker.discover_codex", return_value=[]), \
                patch("visor.broker.discover_claude", return_value=(list(sessions), warning), side_effect=error), \
                patch("visor.broker.create_session") as create, \
                patch("visor.broker.terminal_launch") as launch:
            self.broker.refresh_providers()
            create.assert_not_called()
            launch.assert_not_called()

    def stale(self):
        return self.store.upsert_session("claude", self.sid, "Saved Claude", str(self.root),
            status="waiting", latest="Claude agent view: dialog open")

    def test_successful_empty_discovery_expires_waiting_without_removing_history(self):
        key = self.stale()
        self.refresh()
        row = self.store.session(key)
        self.assertEqual(row["status"], "disconnected")
        self.assertIn("Absent from fresh Claude discovery", row["latest"])
        self.assertIn("checked", row["latest"])
        self.assertEqual(row["name"], "Saved Claude")

    def test_unavailable_roster_and_failed_discovery_do_not_claim_disconnection(self):
        for warning, error in (("Roster failed", None), ("", RuntimeError("Provider unavailable"))):
            with self.subTest(warning=warning, error=error):
                key = self.stale()
                self.refresh(warning=warning, error=error)
                row = self.store.session(key)
                self.assertEqual(row["status"], "unknown")
                self.assertIn("Live discovery unavailable", row["latest"])

    def test_saved_history_fallback_is_unknown_and_fresh_managed_hook_wins(self):
        key = self.stale()
        saved = {"provider": "claude", "native_id": self.sid, "name": "Saved Claude",
                 "cwd": str(self.root), "status": "unknown", "latest": "Saved history only"}
        self.refresh([saved], "Roster unavailable")
        self.assertEqual(self.store.session(key)["status"], "unknown")
        self.store.update_session(key, managed=1, heartbeat=time.time(), status="waiting")
        self.store.set_setting("claude_runtime:" + key, {"observed": time.time(), "status": "waiting"})
        self.refresh()
        self.assertEqual(self.store.session(key)["status"], "waiting")
        self.refresh([saved], "Roster unavailable")
        self.assertEqual(self.store.session(key)["status"], "waiting")
        self.store.update_session(key, heartbeat=time.time() - 60)
        self.refresh()
        self.assertEqual(self.store.session(key)["status"], "disconnected")

    def test_fresh_hook_replaces_an_older_roster_status_instead_of_preserving_it(self):
        key = self.stale()
        self.store.update_session(key, managed=1, heartbeat=time.time())
        self.store.set_setting("claude_runtime:" + key, {"observed": time.time(), "status": "idle", "latest": "Claude hook: Stop"})
        self.refresh()
        self.assertEqual(self.store.session(key)["status"], "idle")
        self.assertEqual(self.store.session(key)["latest"], "Claude hook: Stop")
