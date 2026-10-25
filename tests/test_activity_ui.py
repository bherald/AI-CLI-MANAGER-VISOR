"""Isolated activity-board interaction tests; no broker or provider calls."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QApplication

from visor.activity import ActivityWindow, LocalTextDocument, safe_html
from visor.activity_document import list_replies


class ActivityUICase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.state = self.root / "private-state"
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.watch.stop()
            window.layout_timer.stop()
            window.close()
            window.deleteLater()
        self.app.processEvents()
        self.folder.cleanup()

    def document(self, identity="topic", revision=1):
        return {
            "schema_version": 1, "id": identity, "title": "Fixture activity",
            "revision": revision,
            "session_link": {"id": "codex:fixture", "label": "Fixture session"},
            "cards": [
                {"id": "text", "kind": "text", "title": "Explanation",
                 "geometry": [15, 15, 300, 250], "html": "<h2>Original explanation</h2>"},
                {"id": "question", "kind": "question", "title": "Question",
                 "geometry": [340, 15, 350, 350], "prompt": "Which next step?",
                 "choices": ["Investigate", "Wait"]},
            ],
        }

    def write(self, source, document):
        previous = source.stat().st_mtime_ns if source.exists() else 0
        source.write_text(json.dumps(document))
        stamp = max(source.stat().st_mtime_ns, previous + 1_000_000)
        os.utime(source, ns=(stamp, stamp))

    def open(self, document=None, source=None):
        document = document or self.document()
        source = source or self.root / (document["id"] + ".json")
        if not source.exists():
            self.write(source, document)
        window = ActivityWindow(source, self.state)
        self.windows.append(window)
        window.show()
        self.app.processEvents()
        window.watch.stop()
        window.layout_timer.stop()
        return window

    def update(self, window, document):
        self.write(window.source, document)
        window.poll_document()
        window.layout_timer.stop()

    def test_watched_update_preserves_geometry_draft_and_choice(self):
        window = self.open()
        card = window.cards["question"]
        card.setGeometry(80, 90, 420, 360)
        geometry = card.geometry()
        card.reply.setPlainText("Keep this unfinished answer")
        card.choices.setCurrentIndex(1)
        document = self.document(revision=2)
        document["cards"][0]["html"] = "<p>New explanation</p>"
        document["cards"][1]["geometry"] = [5, 5, 220, 150]
        self.update(window, document)
        self.assertEqual(window.document["revision"], 2)
        self.assertEqual(card.geometry(), geometry)
        self.assertEqual(card.reply.toPlainText(), "Keep this unfinished answer")
        self.assertEqual(card.choices.currentText(), "Investigate")
        self.assertTrue(card.send.isEnabled())
        self.assertIn("New explanation", window.cards["text"].content.toPlainText())

    def test_changed_question_preserves_draft_and_requires_review(self):
        window = self.open()
        card = window.cards["question"]
        card.reply.setPlainText("Draft for the original prompt")
        document = self.document(revision=2)
        document["cards"][1]["prompt"] = "Which different next step?"
        self.update(window, document)
        self.assertTrue(card.question_changed)
        self.assertFalse(card.send.isEnabled())
        with patch("visor.activity.save_reply") as save:
            card.save_answer()
            save.assert_not_called()
        self.assertEqual(card.reply.toPlainText(), "Draft for the original prompt")
        card.review_question()
        self.assertTrue(card.send.isEnabled())

    def test_unreviewed_draft_remains_blocked_after_close_and_reopen(self):
        window = self.open()
        window.cards["question"].reply.setPlainText("Old answer")
        document = self.document(revision=2)
        document["cards"][1]["prompt"] = "A changed question?"
        self.update(window, document)
        window.close()
        reopened = self.open(document, window.source)
        card = reopened.cards["question"]
        self.assertEqual(card.reply.toPlainText(), "Old answer")
        self.assertTrue(card.question_changed)
        self.assertFalse(card.send.isEnabled())

    def test_choice_only_draft_is_reviewed_if_choices_change_while_closed(self):
        window = self.open()
        window.cards["question"].choices.setCurrentIndex(1)
        window.close()
        document = self.document(revision=2)
        document["cards"][1]["choices"].append("Do something else")
        self.write(window.source, document)
        reopened = self.open(document, window.source)
        card = reopened.cards["question"]
        self.assertEqual(card.choices.currentText(), "Investigate")
        self.assertTrue(card.question_changed)
        self.assertFalse(card.send.isEnabled())

    def test_clear_close_and_restore_preserve_source_and_draft(self):
        window = self.open()
        content = window.source.read_bytes()
        card = window.cards["question"]
        card.reply.setPlainText("Private draft")
        window.clear_view()
        self.assertTrue(all(c.isHidden() for c in window.cards.values()))
        self.assertEqual(window.source.read_bytes(), content)
        window.close()
        reopened = self.open(source=window.source)
        self.assertTrue(all(c.isHidden() for c in reopened.cards.values()))
        self.assertEqual(reopened.cards["question"].reply.toPlainText(), "Private draft")
        reopened.show_cards()
        self.assertTrue(all(not c.isHidden() for c in reopened.cards.values()))
        self.assertEqual(window.source.read_bytes(), content)

    def test_stale_source_identity_and_kind_changes_preserve_accepted_view(self):
        window = self.open()
        original = copy.deepcopy(window.document)
        candidates = []
        stale = copy.deepcopy(original)
        stale["cards"][0]["html"] = "<p>Stale replacement</p>"
        candidates.append(stale)
        other = self.document(identity="foreign", revision=2)
        candidates.append(other)
        kind = self.document(revision=2)
        kind["cards"][0] = {"id": "text", "kind": "question", "title": "Changed kind", "prompt": "New?"}
        candidates.append(kind)
        for candidate in candidates:
            with self.subTest(candidate=candidate["id"], kind=candidate["cards"][0]["kind"]):
                self.update(window, candidate)
                self.assertEqual(window.document, original)
                self.assertIn("Original explanation", window.cards["text"].content.toPlainText())
                self.assertIn("last accepted", window.status.text())

    def test_invalid_source_geometry_is_rejected_before_accepting_revision(self):
        window = self.open()
        document = self.document(revision=2)
        document["cards"].append({"id": "new", "kind": "text", "title": "Bad geometry",
                                  "html": "<p>Unsafe bounds</p>", "geometry": [1e300, 0, 400, 300]})
        self.update(window, document)
        self.assertEqual(window.document["revision"], 1)
        self.assertNotIn("new", window.cards)

    def test_corrupt_layout_shapes_do_not_block_document(self):
        self.state.mkdir()
        for layout in ([], {"board_id": "topic", "cards": []},
                       {"board_id": "topic", "cards": {"question": "invalid"}},
                       {"board_id": "topic", "geometry": [True, 0, 400, 300]}):
            with self.subTest(layout=layout):
                (self.state / "topic.layout.json").write_text(json.dumps(layout))
                window = self.open()
                self.assertEqual(window.document["id"], "topic")
                self.assertEqual(len(window.cards), 2)
                window.close()

    def test_independent_topics_do_not_share_layout_or_answers(self):
        first = self.open(self.document("first"))
        second = self.open(self.document("second"))
        first.cards["question"].reply.setPlainText("Only for first")
        first.cards["question"].save_answer()
        first.save_layout()
        second.save_layout()
        self.assertNotEqual(first.layout_path, second.layout_path)
        self.assertEqual(second.cards["question"].reply.toPlainText(), "")
        self.assertEqual(len(list_replies(self.state, "first")), 1)
        self.assertEqual(list_replies(self.state, "second"), [])
        self.assertIn("locally", first.cards["question"].response_status.text())
        self.assertNotIn("delivered", first.cards["question"].response_status.text().lower())

    def test_rich_text_resources_are_stripped_and_loader_never_reads_them(self):
        value = "<h2>Keep text</h2><script>DROP</script><img src='file:///private.png'><a href='https://example.invalid'>Label</a><p style='color:red;background-color:url(file:///x)'>More</p>"
        clean = safe_html(value)
        self.assertIn("Keep text", clean)
        for forbidden in ("DROP", "src=", "href=", "url(", "<img", "<script"):
            self.assertNotIn(forbidden, clean)
        doc = LocalTextDocument()
        for url in ("file:///private.png", "https://example.invalid/image.png"):
            self.assertTrue(doc.loadResource(2, QUrl(url)).isNull())

    def test_untrusted_non_html_labels_are_plain_text(self):
        document = self.document()
        document["title"] = "<b>Title</b>"
        document["session_link"]["label"] = "<i>Session</i>"
        document["cards"][1]["prompt"] = "<img src='file:///private.png'>Prompt"
        document["cards"][1]["caption"] = "<img src='file:///private.png'>Caption"
        window = self.open(document)
        for label in (window.heading, window.metadata, window.cards["question"].content,
                      window.cards["question"].caption):
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)

    def test_invalid_colspan_does_not_interrupt_content_update(self):
        for value in ("9" * 5000, "²"):
            with self.subTest(value=value[:20]):
                clean = safe_html(f'<table><tr><td colspan="{value}">Readable</td></tr></table>')
                self.assertIn("Readable", clean)
                self.assertNotIn(value, clean)

    def test_choice_only_draft_schedules_persistence(self):
        window = self.open()
        self.assertFalse(window.layout_timer.isActive())
        window.cards["question"].choices.setCurrentIndex(1)
        self.assertTrue(window.layout_timer.isActive())
