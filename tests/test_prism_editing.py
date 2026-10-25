"""Prism editing, conflict recovery and view-only controls at the Qt boundary."""
import copy
import json
import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel

from visor.activity import ActivityManager, ActivityWindow
from visor.activity_document import ActivityPolicy


class PrismEditingCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "board.json"
        self.state = self.root / "layouts"
        self.windows = []
        self.requests = []
        self.document = {
            "schema_version": 1, "id": "shared-topic", "title": "Prism fixture", "revision": 1,
            "cards": [
                {"id": "notes", "kind": "text", "title": "Notes", "html": "<p>Original text</p>",
                 "geometry": [5, 5, 330, 320]},
                {"id": "drawing", "kind": "sketch", "title": "Drawing", "canvas": [200, 100],
                 "strokes": [], "geometry": [345, 5, 540, 350]},
                {"id": "question", "kind": "question", "title": "Question", "prompt": "Next step?",
                 "geometry": [5, 370, 420, 300]},
            ],
        }
        self.write(self.document)

    def tearDown(self):
        for window in self.windows:
            window.watch.stop()
            window.layout_timer.stop()
            window.close()
            window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def write(self, document):
        old = self.source.stat().st_mtime_ns if self.source.exists() else 0
        self.source.write_text(json.dumps(document), encoding="utf-8")
        stamp = max(self.source.stat().st_mtime_ns, old + 1_000_000)
        os.utime(self.source, ns=(stamp, stamp))

    def open(self, submit=None):
        window = ActivityWindow(self.source, self.state, submit=submit)
        self.windows.append(window)
        window.show()
        self.app.processEvents()
        window.watch.stop()
        window.layout_timer.stop()
        return window

    def capture(self, action, params, callback):
        self.requests.append((action, copy.deepcopy(params), callback))

    def draw_dot(self, pad):
        point = QPointF(pad.width() / 2, pad.height() / 2)
        for kind, buttons in [(QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
                              (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)]:
            event = QMouseEvent(kind, point, point, Qt.MouseButton.LeftButton, buttons,
                                Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(pad, event)

    def test_sketch_pointer_save_and_reopen_preserve_drawing(self):
        window = self.open()
        drawing = window.cards["drawing"]
        self.draw_dot(drawing.content)
        self.assertTrue(drawing.edit_dirty)
        snapshot = drawing.content.strokes
        self.assertEqual(len(snapshot), 1)
        drawing.save_edit()
        accepted = json.loads(self.source.read_text())
        self.assertEqual(accepted["revision"], 2)
        self.assertEqual(accepted["cards"][1]["strokes"], snapshot)
        self.assertEqual(accepted["last_editor"], "operator")
        self.assertFalse(drawing.edit_dirty)
        window.close()
        reopened = self.open()
        self.assertEqual(reopened.cards["drawing"].content.strokes, snapshot)
        self.assertEqual(reopened.document["revision"], 2)

    def test_direct_typing_save_and_reopen(self):
        window = self.open()
        notes = window.cards["notes"]
        notes.editor.setPlainText("A practical next step")
        notes.save_edit()
        self.assertFalse(notes.edit_dirty)
        self.assertEqual(window.document["revision"], 2)
        self.assertIn("A practical next step", notes.content.toPlainText())
        window.close()
        reopened = self.open()
        self.assertIn("A practical next step", reopened.cards["notes"].editor.toPlainText())
        self.assertFalse(reopened.cards["notes"].edit_dirty)

    def test_source_update_during_typing_preserves_draft_and_stale_save_rejects(self):
        window = self.open()
        notes = window.cards["notes"]
        notes.editor.setPlainText("Keep my unfinished text")
        newer = copy.deepcopy(self.document)
        newer["revision"] = 2
        newer["cards"][0]["html"] = "<p>Peer contribution arrived</p>"
        self.write(newer)
        window.poll_document()
        self.assertEqual(notes.editor.toPlainText(), "Keep my unfinished text")
        self.assertIn("Peer contribution arrived", notes.content.toPlainText())
        self.assertEqual(notes.edit_revision, 1)
        notes.save_edit()
        self.assertTrue(notes.edit_dirty)
        self.assertIn("Not confirmed", notes.edit_status.text())
        self.assertEqual(json.loads(self.source.read_text()), newer)
        window.close()
        reopened = self.open()
        self.assertEqual(reopened.cards["notes"].editor.toPlainText(), "Keep my unfinished text")
        self.assertEqual(reopened.cards["notes"].edit_revision, 1)

    def test_source_update_during_sketch_keeps_unsaved_strokes(self):
        window = self.open()
        drawing = window.cards["drawing"]
        self.draw_dot(drawing.content)
        draft = drawing.content.strokes
        newer = copy.deepcopy(self.document)
        newer["revision"] = 2
        newer["cards"][1]["strokes"] = [{"points": [[5, 5], [190, 90]], "color": "#bf503e", "width": 4}]
        self.write(newer)
        window.poll_document()
        self.assertEqual(drawing.content.strokes, draft)
        self.assertEqual(drawing.edit_revision, 1)
        drawing.save_edit()
        self.assertTrue(drawing.edit_dirty)
        self.assertEqual(json.loads(self.source.read_text()), newer)
        window.close()
        self.assertEqual(self.open().cards["drawing"].content.strokes, draft)

    def test_failed_edit_retry_keeps_exact_request_across_reopen(self):
        window = self.open(submit=self.capture)
        notes = window.cards["notes"]
        notes.editor.setPlainText("Retry this exact update")
        notes.save_edit()
        first = self.requests[-1]
        first[2](None, "Connection lost after request")
        self.assertTrue(notes.edit_dirty)
        notes.save_edit()
        second = self.requests[-1]
        self.assertEqual(first[:2], second[:2])
        second[2](None, "Still disconnected")
        window.close()
        reopened = self.open(submit=self.capture)
        reopened.cards["notes"].save_edit()
        self.assertEqual(first[:2], self.requests[-1][:2])

    def test_edit_success_does_not_discard_new_typing_while_request_in_flight(self):
        window = self.open(submit=self.capture)
        notes = window.cards["notes"]
        notes.editor.setPlainText("First version")
        notes.save_edit()
        request = self.requests[-1]
        notes.editor.setPlainText("First version plus a new thought")
        accepted = copy.deepcopy(self.document)
        accepted["revision"] = 2
        accepted["cards"][0]["html"] = request[1]["content"]
        self.write(accepted)
        request[2]({"revision": 2}, None)
        window.poll_document()
        self.assertTrue(notes.edit_dirty)
        self.assertEqual(notes.editor.toPlainText(), "First version plus a new thought")
        self.assertIn("unsaved", notes.edit_status.text().lower())
        self.assertEqual(notes.edit_revision, 2)
        notes.save_edit()
        following = self.requests[-1]
        self.assertEqual(following[1]["revision"], 2)
        self.assertNotEqual(following[1]["request_id"], request[1]["request_id"])
        accepted["revision"] = 3
        accepted["cards"][0]["html"] = following[1]["content"]
        self.write(accepted)
        following[2]({"revision": 3}, None)
        self.assertFalse(notes.edit_dirty)
        self.assertEqual(window.document["revision"], 3)
        self.assertIn("First version plus a new thought", notes.content.toPlainText())

    def test_unknown_reply_retry_keeps_payload_after_unrelated_revision(self):
        window = self.open(submit=self.capture)
        question = window.cards["question"]
        question.reply.setPlainText("Investigate the first option")
        question.save_answer()
        first = self.requests[-1]
        first[2](None, "Connection lost; outcome unknown")
        newer = copy.deepcopy(self.document)
        newer["revision"] = 2
        newer["cards"][0]["html"] = "<p>An unrelated update</p>"
        self.write(newer)
        window.poll_document()
        question.save_answer()
        self.assertEqual(first[:2], self.requests[-1][:2])

    def test_corrupt_saved_pending_request_is_ignored_without_losing_draft(self):
        window = self.open(submit=self.capture)
        window.cards["notes"].editor.setPlainText("Recover this draft")
        window.close()
        state = json.loads(window.layout_path.read_text())
        state["cards"]["notes"]["pending_edit"] = "not a request object"
        state["cards"]["question"]["pending_reply"] = ["invalid"]
        state["cards"]["question"]["draft"] = "Recover this answer"
        window.layout_path.write_text(json.dumps(state))
        reopened = self.open(submit=self.capture)
        reopened.cards["notes"].save_edit()
        reopened.cards["question"].save_answer()
        self.assertEqual(len(self.requests), 2)
        self.assertIn("Recover this draft", self.requests[0][1]["content"])
        self.assertEqual(self.requests[1][1]["answer"], "Recover this answer")

    def test_manager_clear_close_and_reopen_preserve_source_and_draft(self):
        supervisor = SimpleNamespace(root=self.root, client=None, status=QLabel())
        manager = ActivityManager(supervisor)
        metadata = {"id": "shared-topic", "title": "Prism fixture", "path": str(self.source),
                    "view_seq": 1, "view_action": "open"}
        before = self.source.read_bytes()
        manager.update([metadata], asdict(ActivityPolicy()))
        window = manager.windows[metadata["id"]]
        self.windows.append(window)
        window.watch.stop()
        window.cards["notes"].editor.setPlainText("Keep this through clear and close")
        manager.update([{**metadata, "view_seq": 2, "view_action": "clear"}], asdict(ActivityPolicy()))
        self.assertTrue(all(card.isHidden() for card in window.cards.values()))
        manager.update([{**metadata, "view_seq": 3, "view_action": "close"}], asdict(ActivityPolicy()))
        self.assertTrue(window.isHidden())
        manager.update([{**metadata, "view_seq": 4, "view_action": "focus"}], asdict(ActivityPolicy()))
        self.assertFalse(window.isHidden())
        window.show_cards()
        self.assertEqual(window.cards["notes"].editor.toPlainText(), "Keep this through clear and close")
        self.assertEqual(self.source.read_bytes(), before)
        manager.close()
