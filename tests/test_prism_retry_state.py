"""Uncertain native intents and workspace preferences survive restart safely."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from visor.activity import ActivityWindow
from visor.paths import write_private
from visor.prism_workspace import PrismWorkspace


class PrismRetryStateCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "board.json"
        self.document = {"schema_version": 1, "id": "board", "title": "Release review", "revision": 1,
                         "cards": [{"id": "preview", "kind": "toggle", "title": "Preview",
                                    "action_id": "old-preview", "value": False}]}
        write_private(self.source, self.document)
        self.windows, self.workspaces, self.requests = [], [], []

    def tearDown(self):
        for window in self.windows:
            window.on_view = None
            window.watch.stop()
            window.receipt_timer.stop()
            window.layout_timer.stop()
            window.close()
            window.deleteLater()
        for workspace in self.workspaces:
            workspace.shutdown()
            workspace.close()
            workspace.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def submit(self, operation, params, callback):
        self.requests.append((operation, copy.deepcopy(params), callback))

    def window(self):
        window = ActivityWindow(self.source, self.root / "layouts", submit=self.submit)
        self.windows.append(window)
        return window

    def uncertain(self):
        original = self.window()
        card = original.cards["preview"]
        card.send_event({"action_id": "old-preview", "value": True})
        operation, params, callback = self.requests.pop(0)
        self.assertEqual(operation, "activity_event")
        callback(None, "Accepted response was lost")
        original.save_layout()
        pending = copy.deepcopy(card.pending_event)
        original.close()
        original.watch.stop()
        return pending, params

    def reopen_with_changed_action(self):
        pending, params = self.uncertain()
        self.document["revision"] = 2
        self.document["cards"][0].update(action_id="new-preview", value=True)
        write_private(self.source, self.document)
        restored = self.window()
        return restored, pending, params

    def test_historical_pending_action_restores_despite_new_revision_and_declaration(self):
        restored, pending, _params = self.reopen_with_changed_action()
        card = restored.cards["preview"]
        self.assertEqual(restored.document["revision"], 2)
        self.assertEqual(card.data["action_id"], "new-preview")
        self.assertEqual(card.pending_event, pending)
        self.assertEqual(card.pending_event["params"]["revision"], 1)
        self.assertEqual(card.pending_event["params"]["action_id"], "old-preview")
        self.assertFalse(card.retry_event_button.isHidden())
        self.assertFalse(card.clear_event_button.isHidden())
        self.assertEqual(self.requests, [])

    def test_restored_retry_sends_exact_original_request_and_blocks_replacement(self):
        restored, pending, original_params = self.reopen_with_changed_action()
        card = restored.cards["preview"]
        card.send_event({"action_id": "new-preview", "value": False})
        self.assertEqual(self.requests, [])
        self.assertEqual(card.pending_event, pending)
        card.retry_event()
        operation, retry_params, callback = self.requests.pop(0)
        self.assertEqual(operation, "activity_event")
        self.assertEqual(retry_params, original_params)
        self.assertEqual(retry_params["request_id"], pending["request_id"])
        callback(None, "Connection is still uncertain")
        self.assertEqual(card.pending_event, pending)
        restored.save_layout()
        saved = json.loads(restored.layout_path.read_text())
        self.assertEqual(saved["cards"]["preview"]["pending_event"], pending)

    def test_clear_saved_retry_defaults_no_and_only_explicit_yes_removes_it(self):
        restored, pending, _params = self.reopen_with_changed_action()
        card = restored.cards["preview"]
        with patch("visor.activity.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as question:
            card.clear_pending_event()
        self.assertEqual(question.call_args.args[-1], QMessageBox.StandardButton.No)
        self.assertIn("already have reached", question.call_args.args[2])
        self.assertEqual(card.pending_event, pending)
        with patch("visor.activity.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            card.clear_pending_event()
        self.assertIsNone(card.pending_event)
        self.assertTrue(card.retry_event_button.isHidden())
        self.assertTrue(card.clear_event_button.isHidden())
        restored.save_layout()
        self.assertIsNone(json.loads(restored.layout_path.read_text())["cards"]["preview"]["pending_event"])
        self.assertEqual(self.requests, [])

    def test_corrupt_workspace_list_uses_defaults_without_startup_failure(self):
        preference = self.root / "prism-workspace.json"
        preference.write_text("[]")
        workspace = PrismWorkspace(self.root)
        self.workspaces.append(workspace)
        self.assertEqual(workspace.theme.currentData(), "calm-dark")
        self.assertEqual(workspace.property("prismTheme"), "calm-dark")
        self.assertEqual(workspace.selector.count(), 0)
        self.assertEqual(preference.read_text(), "[]")

    def test_shutdown_saves_geometry_and_theme_without_window_close(self):
        workspace = PrismWorkspace(self.root)
        self.workspaces.append(workspace)
        workspace.theme.setCurrentIndex(workspace.theme.findData("soft-light"))
        workspace.web_port = 47123
        workspace.setGeometry(40, 50, 700, 500)
        expected = [workspace.x(), workspace.y(), workspace.width(), workspace.height()]
        workspace.shutdown()
        saved = json.loads((self.root / "prism-workspace.json").read_text())
        self.assertEqual(saved, {"geometry": expected, "theme": "soft-light", "web_port": 47123,
                                 "web_host": "127.0.0.1", "web_was_active": False})
        reopened = PrismWorkspace(self.root)
        self.workspaces.append(reopened)
        self.assertEqual(reopened.web_port, 47123)
        self.assertEqual(reopened.theme.currentData(), "soft-light")
        self.assertEqual(reopened.property("prismTheme"), "soft-light")
        self.assertEqual([reopened.width(), reopened.height()], expected[2:])
