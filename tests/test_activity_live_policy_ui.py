"""Live policy and compact question-card acceptance against an isolated real broker."""
import base64
import copy
import os
import tempfile
import unittest
from dataclasses import asdict, replace
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from visor.activity import ActivityManager, ActivityWindow
from visor.activity_document import ActivityPolicy
from visor.broker import Broker


class LivePolicyUICase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.broker = Broker(self.root)
        self.owner = self.broker.store.upsert_session("codex", "policy-ui", "Policy fixture", self.temp.name)
        picture = QImage(6, 4, QImage.Format.Format_RGB32)
        picture.fill(0xff225577)
        picture.save(str(self.root / "picture.png"), "PNG")
        self.image = (self.root / "picture.png").read_bytes()
        asset = self.broker.activities.asset(self.owner, "picture.png", base64.b64encode(self.image).decode())
        self.doc = {"schema_version": 1, "id": "live-policy", "title": "Live policy fixture", "revision": 1,
                    "cards": [
                        {"id": "q", "title": "Operator question", "kind": "question", "prompt": "Which part should continue? " * 6,
                         "choices": ["Review", "Discuss"], "geometry": [10, 10, 540, 220]},
                        {"id": "text", "title": "Notes", "kind": "text", "html": "<p>Original</p>"},
                        {"id": "draw", "title": "Drawing", "kind": "sketch", "canvas": [200, 100], "strokes": []},
                        {"id": "pic", "title": "Picture", "kind": "image", "path": asset["path"]}]}
        self.broker.activities.publish(self.owner, self.doc, "publish")
        self.calls, self.windows = [], []
        self.client = SimpleNamespace(call=self.call)
        self.supervisor = SimpleNamespace(root=self.root, client=self.client, status=QLabel(),
                                          prism_button=QPushButton(), job=self.job)
        self.manager = ActivityManager(self.supervisor)
        self.policy = ActivityPolicy()
        self.change_policy(self.policy)
        self.window = self.manager.windows["live-policy"]
        self.app.processEvents()
        self.window.watch.stop()
        self.window.receipt_timer.stop()

    def tearDown(self):
        self.manager.close()
        self.supervisor.status.deleteLater()
        self.supervisor.prism_button.deleteLater()
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
        return self.broker.dispatch(operation, params, "operator", "operator")

    def job(self, function, callback):
        try:
            value = function()
        except (OSError, ValueError) as exc:
            callback(None, str(exc))
        else:
            callback(value, None)

    def submit(self, operation, params, callback):
        self.job(lambda: self.call(operation, **params), callback)

    def change_policy(self, policy):
        self.policy = policy
        self.broker.store.set_setting("activity_policy", asdict(policy))
        self.manager.update(self.broker.activities.list(None), asdict(policy))
        self.app.processEvents()

    @staticmethod
    def drawing(points=6):
        return [{"points": [[10 + i * 10, 30] for i in range(points)], "color": "#087d80", "width": 3}]

    def draft_drawing(self, points=6):
        card = self.window.cards["draw"]
        card.content.set_card({**card.data, "strokes": self.drawing(points)})
        card.content._notify()
        return card

    def test_open_answer_raise_and_lower_match_broker_without_losing_text(self):
        card = self.window.cards["q"]
        answer = "Keep every word in this answer"
        card.reply.setPlainText(answer)
        self.change_policy(replace(self.policy, max_answer_chars=10))
        self.assertEqual(self.window.policy.max_answer_chars, 10)
        self.assertFalse(card.send.isEnabled())
        self.assertIn("On hold", card.policy_notice.text())
        before = len(self.calls)
        card.save_answer()
        self.assertEqual(len(self.calls), before)
        with self.assertRaises(ValueError):
            self.broker.activities.reply("live-policy", "q", 1, answer, "too-large-answer")
        self.assertEqual(card.reply.toPlainText(), answer)
        self.change_policy(replace(self.policy, max_answer_chars=100))
        self.assertTrue(card.send.isEnabled())
        card.save_answer()
        self.assertEqual(self.broker.activities.replies(self.owner)[0]["answer"], answer)
        self.assertEqual(card.reply.toPlainText(), answer)

    def test_open_image_byte_and_pixel_limits_revalidate_and_recover(self):
        visual = self.window.cards["pic"].content
        path = Path(self.window.source).parent / visual.card["path"]
        original = path.read_bytes()
        for lowered in (replace(self.policy, max_image_bytes=len(self.image) - 1),
                        replace(self.policy, max_image_pixels=23)):
            self.change_policy(lowered)
            self.assertTrue(visual.error)
            self.assertTrue(visual.image.isNull())
            with self.assertRaises(ValueError):
                self.broker.activities.asset(self.owner, "picture.png", base64.b64encode(self.image).decode())
            self.assertEqual(path.read_bytes(), original)
            self.change_policy(ActivityPolicy())
            self.assertFalse(visual.error)
            self.assertFalse(visual.image.isNull())
            self.assertEqual(visual.policy, ActivityPolicy())

    def test_open_drawing_limits_preserve_strokes_and_match_broker_on_save(self):
        card = self.draft_drawing()
        original = card.content.strokes
        self.change_policy(replace(self.policy, max_drawing_points=3))
        self.assertEqual(card.content.strokes, original)
        self.assertEqual(card.content.policy.max_drawing_points, 3)
        self.assertFalse(card.save_edit_button.isEnabled())
        with self.assertRaises(ValueError):
            self.broker.activities.edit("live-policy", "draw", 1, original, "too-many-points")
        event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(card.content.rect().center()), QPointF(card.content.mapToGlobal(card.content.rect().center())),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        card.content.mousePressEvent(event)
        self.assertEqual(card.content.strokes, original)
        self.change_policy(replace(self.policy, max_drawing_points=12))
        self.assertTrue(card.save_edit_button.isEnabled())
        card.save_edit()
        stored = self.broker.activities.get(self.owner, "live-policy")["document"]
        self.assertEqual(next(c for c in stored["cards"] if c["id"] == "draw")["strokes"], original)

    def test_restore_retains_complete_over_limit_answer_text_drawing_and_retry_identity(self):
        question, text = self.window.cards["q"], self.window.cards["text"]
        answer, content = "A full answer that must not be shortened", "Full local note " * 200
        question.reply.setPlainText(answer)
        failed = []
        original_submit = self.window.submit
        self.window.submit = lambda operation, params, callback: (failed.append((operation, params)), callback(None, "Connection lost"))
        question.save_answer()
        self.window.submit = original_submit
        pending = copy.deepcopy(question.pending_reply)
        text.editor.setPlainText(content)
        drawing = self.draft_drawing().content.strokes
        lowered = replace(self.policy, max_answer_chars=10, max_text_chars=1000, max_drawing_points=3)
        self.change_policy(lowered)
        self.window.save_layout()
        restored = ActivityWindow(self.window.source, self.window.state_dir, lowered, submit=self.submit)
        self.windows.append(restored)
        self.assertEqual(restored.cards["q"].reply.toPlainText(), answer)
        self.assertEqual(restored.cards["q"].pending_reply, pending)
        self.assertEqual(restored.cards["text"].editor.toPlainText(), content)
        self.assertEqual(restored.cards["draw"].content.strokes, drawing)
        self.assertFalse(restored.cards["q"].send.isEnabled())
        self.assertFalse(restored.cards["text"].save_edit_button.isEnabled())
        self.assertFalse(restored.cards["draw"].save_edit_button.isEnabled())
        restored.apply_policy(ActivityPolicy())
        self.assertTrue(restored.cards["q"].send.isEnabled())
        self.assertTrue(restored.cards["text"].save_edit_button.isEnabled())
        self.assertTrue(restored.cards["draw"].save_edit_button.isEnabled())

    def test_invalid_policy_explicitly_holds_operations_but_keeps_navigation_and_drafts(self):
        question = self.window.cards["q"]
        question.reply.setPlainText("Preserved draft")
        drawing = self.draft_drawing().content.strokes
        for policy in (None, {}, {**asdict(self.policy), "max_answer_chars": False}, {**asdict(self.policy), "unexpected": 1}):
            self.manager.update([], policy)
            self.assertTrue(self.window.policy_error)
            self.assertIn("live-policy", self.manager.windows)
            self.assertFalse(question.send.isEnabled())
            self.assertFalse(self.window.cards["draw"].save_edit_button.isEnabled())
            self.assertTrue(self.window.cards["draw"].content.policy_error)
            before = len(self.calls)
            question.save_answer()
            self.window.cards["draw"].save_edit()
            self.assertEqual(len(self.calls), before)
            self.window.clear_view()
            self.window.show_cards()
            self.window.save_layout()
            self.assertTrue(self.window.layout_path.exists())
            self.assertEqual(question.reply.toPlainText(), "Preserved draft")
            self.assertEqual(self.window.cards["draw"].content.strokes, drawing)
        self.change_policy(ActivityPolicy())
        self.assertFalse(self.window.policy_error)
        self.assertTrue(question.send.isEnabled())

    def test_request_boundary_rechecks_current_policy_for_queued_reply_and_edit(self):
        self.change_policy(replace(self.policy, max_answer_chars=2, max_drawing_points=1))
        replies = []
        before = len(self.calls)
        self.window.request("activity_reply", lambda value, error: replies.append(error), id="live-policy", card_id="q",
                            revision=1, answer="too long", request_id="recheck-reply")
        self.window.request("activity_edit", lambda value, error: replies.append(error), id="live-policy", card_id="draw",
                            revision=1, content=self.drawing(), request_id="recheck-drawing")
        self.assertEqual(len(self.calls), before)
        self.assertEqual(len(replies), 2)
        self.assertTrue(all("policy holds" in error for error in replies))

    def test_restored_small_question_card_scrolls_prompt_reply_controls_and_full_read_status(self):
        card = self.window.cards["q"]
        answer = "Keep this exact draft while restoring a small card"
        recipient = "codex:" + "abcdef0123456789-" * 6
        status = "Read by " + recipient + " · acknowledgment is not task completion"
        card.reply.setPlainText(answer)
        card.setGeometry(10, 10, 300, 180)
        self.window.save_layout()
        restored = ActivityWindow(self.window.source, self.window.state_dir, self.policy, submit=self.submit)
        self.windows.append(restored)
        restored.resize(850, 600)
        restored.show()
        card = restored.cards["q"]
        card.response_status.setText(status)
        card.details_toggle.setChecked(True)
        self.app.processEvents()
        self.assertEqual(card.reply.toPlainText(), answer)
        self.assertEqual(card.response_status.text(), status)
        self.assertLessEqual(card.height(), 220)
        self.assertGreater(card.question_scroll.verticalScrollBar().maximum(), 0)
        ordered = [card.content, card.choices, card.reply, card.send, card.caption, card.response_status]
        rects = [(widget.mapTo(card.inner, QPoint(0, 0)).y(), widget.height()) for widget in ordered if widget.isVisible()]
        for (top, height), (next_top, _) in pairwise(rects):
            self.assertLessEqual(top + height, next_top, "Question fields overlap")
        for label in (card.content, card.caption, card.response_status):
            self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
        for widget in (card.content, card.reply, card.send, card.response_status):
            card.question_scroll.ensureWidgetVisible(widget)
            self.app.processEvents()
            position = widget.mapTo(card.question_scroll.viewport(), QPoint(0, 0))
            self.assertLess(position.y(), card.question_scroll.viewport().height())
            self.assertGreater(position.y() + widget.height(), 0)
        card.reply.setFocus()
        self.assertTrue(card.reply.hasFocus())

    def test_open_stroke_count_limit_retains_all_strokes_and_recovers(self):
        card = self.window.cards["draw"]
        strokes = [{"points": [[10 + index * 20, 20]], "color": "#087d80", "width": 3} for index in range(3)]
        card.content.set_card({**card.data, "strokes": strokes})
        card.content._notify()
        self.change_policy(replace(self.policy, max_items_per_card=2))
        self.assertEqual(card.content.strokes, strokes)
        self.assertFalse(card.save_edit_button.isEnabled())
        with self.assertRaises(ValueError):
            self.broker.activities.edit("live-policy", "draw", 1, strokes, "too-many-strokes")
        self.change_policy(replace(self.policy, max_items_per_card=3))
        self.assertTrue(card.save_edit_button.isEnabled())
        card.save_edit()
        stored = self.broker.activities.get(self.owner, "live-policy")["document"]
        self.assertEqual(next(c for c in stored["cards"] if c["id"] == "draw")["strokes"], strokes)
