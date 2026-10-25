"""Canonical accepted edits survive failed projections without claiming presentation."""
import copy
import json
import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from visor.activity import ActivityManager, ActivityWindow
from visor.activity_document import ActivityPolicy
from visor.broker import Broker


class AcceptedProjectionUICase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.broker = Broker(self.root)
        self.owner = self.broker.store.upsert_session("codex", "accepted-ui", "Fixture", self.temp.name)
        self.broker.activities.publish(self.owner, {"schema_version": 1, "id": "accepted", "title": "Accepted edit",
            "revision": 1, "cards": [{"id": "text", "kind": "text", "title": "Note", "html": "Original"},
                                     {"id": "draw", "kind": "sketch", "title": "Sketch", "canvas": [100, 100], "strokes": []}]}, "publish")
        self.metadata = self.broker.activities.list(None)[0]
        self.source = Path(self.metadata["path"])
        self.calls, self.results, self.windows, self.managers = [], [], [], []
        self.window = self.open()

    def tearDown(self):
        for manager in self.managers:
            manager.close()
            manager.supervisor.status.deleteLater()
            manager.supervisor.prism_button.deleteLater()
        for window in self.windows:
            window.close()
            window.watch.stop()
            window.receipt_timer.stop()
            window.layout_timer.stop()
            window.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.temp.cleanup()

    def call(self, operation, **params):
        self.calls.append((operation, copy.deepcopy(params)))
        result = self.broker.dispatch(operation, params, "operator", "operator")
        self.results.append(result)
        return result

    @staticmethod
    def job(function, callback):
        try:
            result = function()
        except (OSError, ValueError) as exc:
            callback(None, str(exc))
        else:
            callback(result, None)

    def submit(self, operation, params, callback):
        self.job(lambda: self.call(operation, **params), callback)

    def open(self):
        window = ActivityWindow(self.source, self.root / "layouts", submit=self.submit)
        self.windows.append(window)
        window.show()
        self.app.processEvents()
        window.watch.stop()
        window.receipt_timer.stop()
        return window

    def test_accepted_text_stays_visible_and_durable_until_matching_projection_arrives(self):
        card = self.window.cards["text"]
        card.editor.setPlainText("This text was durably saved")
        with patch.object(self.broker.activities, "_stage", side_effect=OSError("Projection unavailable")):
            card.save_edit()
            result = self.results[-1]
            self.assertTrue(result["accepted"])
            self.assertEqual(result["projection"]["state"], "pending")
            self.assertEqual(card.accepted_edit["revision"], 2)
            self.assertFalse(card.edit_dirty)
            self.assertIn("display update pending", card.edit_status.text())
            self.assertEqual(card.editor.toPlainText(), "This text was durably saved")
            self.assertTrue(card.editor.isVisible())
            self.assertFalse(card.save_edit_button.isEnabled())
            self.assertEqual(self.window.document["revision"], 1)
            self.assertEqual(json.loads(self.source.read_text())["revision"], 1)
            self.window.save_layout()
            restored = self.open()
            self.assertEqual(restored.cards["text"].editor.toPlainText(), "This text was durably saved")
            self.assertEqual(restored.cards["text"].accepted_edit, card.accepted_edit)
            self.assertFalse(restored.cards["text"].save_edit_button.isEnabled())
            with self.broker.store.connect() as db:
                self.assertEqual(db.execute("SELECT revision FROM activity_boards WHERE id='accepted'").fetchone()[0], 2)
        self.broker.activities.get(None, "accepted")
        restored.poll_document()
        self.assertEqual(restored.document["revision"], 2)
        self.assertIsNone(restored.cards["text"].accepted_edit)
        self.assertIn("This text was durably saved", restored.cards["text"].content.toPlainText())
        self.assertIn("current document loaded", restored.cards["text"].edit_status.text())
        self.assertEqual(sum(operation == "activity_edit" for operation, _ in self.calls), 1)

    def test_accepted_sketch_is_not_replaced_by_old_projection(self):
        card = self.window.cards["draw"]
        strokes = [{"points": [[10, 10], [60, 70]], "color": "#087d80", "width": 3}]
        card.content.set_card({**card.data, "strokes": strokes})
        card.content._notify()
        with patch.object(self.broker.activities, "_stage", side_effect=OSError("Projection unavailable")):
            card.save_edit()
            self.window.signature = None
            self.window.poll_document()
            self.assertEqual(card.content.strokes, strokes)
            self.assertIsNotNone(card.accepted_edit)
            self.assertEqual(self.window.document["revision"], 1)
            self.window.save_layout()
            restored = self.open()
            self.assertEqual(restored.cards["draw"].content.strokes, strokes)
        self.broker.activities.get(None, "accepted")
        restored.poll_document()
        self.assertIsNone(restored.cards["draw"].accepted_edit)
        self.assertEqual(restored.cards["draw"].content.strokes, strokes)

    def test_newer_typing_survives_pending_accepted_edit_and_projection_repair(self):
        card = self.window.cards["text"]
        card.editor.setPlainText("Accepted version")
        with patch.object(self.broker.activities, "_stage", side_effect=OSError("Projection unavailable")):
            card.save_edit()
            card.editor.setPlainText("Accepted version with a newer unsent thought")
            card.save_edit()
            self.assertEqual(sum(operation == "activity_edit" for operation, _ in self.calls), 1)
            self.assertTrue(card.edit_dirty)
            self.assertEqual(card.edit_revision, 2)
        self.broker.activities.get(None, "accepted")
        self.window.poll_document()
        self.assertIsNone(card.accepted_edit)
        self.assertEqual(card.editor.toPlainText(), "Accepted version with a newer unsent thought")
        self.assertTrue(card.edit_dirty)
        card.save_edit()
        self.assertEqual(self.window.document["revision"], 3)
        self.assertFalse(card.edit_dirty)

    def test_pending_open_never_uses_cached_path_and_same_request_repairs(self):
        supervisor = SimpleNamespace(root=self.root, client=SimpleNamespace(call=self.call), job=self.job,
                                      status=QLabel(), prism_button=QPushButton())
        manager = ActivityManager(supervisor)
        self.managers.append(manager)
        manager.policy = ActivityPolicy()
        manager.boards = [self.metadata]
        self.source.unlink()
        with patch.object(self.broker.activities, "_stage", side_effect=OSError("Projection unavailable")):
            manager.operator_open(self.metadata)
            self.assertFalse(manager.windows)
            self.assertIn("display update pending", supervisor.status.text())
            pending_id = manager.reopen_requests["accepted"]["request_id"]
            boards = self.broker.activities.list(None)
            manager.update(boards, asdict(ActivityPolicy()))
            self.assertEqual(len(manager.boards), 1)
            self.assertIn("Display pending", manager.markers(manager.boards[0]))
            self.assertFalse(manager.windows)
        manager.operator_open(manager.boards[0])
        self.app.processEvents()
        self.assertIn("accepted", manager.windows)
        controls = [params for operation, params in self.calls if operation == "activity_control"]
        self.assertEqual([params["request_id"] for params in controls], [pending_id, pending_id])
        self.assertNotIn("accepted", manager.reopen_requests)
        self.assertFalse(manager.windows["accepted"].isHidden())
