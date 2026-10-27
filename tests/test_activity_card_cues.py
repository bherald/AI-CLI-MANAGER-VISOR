"""Native help and honest data-state cues preserve complete local drafts."""
import copy
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QFrame, QMessageBox

from visor.activity import ActivityWindow
from visor.activity_document import ActivityPolicy, _validate


class ActivityCardCueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.watch.stop()
            window.receipt_timer.stop()
            window.layout_timer.stop()
            window.close()
            window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def document(self, **properties):
        return {"schema_version": 1, "id": "board", "title": "Board", "revision": 1,
                "cards": [{"id": "note", "title": "Notes", "kind": "text", "html": "<p>Accepted content</p>", **properties}]}

    def open(self, document):
        source = self.root / (str(len(self.windows)) + ".json")
        source.write_text(json.dumps(document), encoding="utf-8")
        window = ActivityWindow(source, self.root / ("layout-" + str(len(self.windows))))
        self.windows.append(window)
        window.show()
        self.app.processEvents()
        self.app.processEvents()
        window.watch.stop()
        window.layout_timer.stop()
        return window

    def update(self, window, **properties):
        document = copy.deepcopy(window.document)
        document["revision"] += 1
        document["cards"][0].update(properties)
        _validate(document, window.policy)
        window.source.write_text(json.dumps(document), encoding="utf-8")
        window.poll_document()
        self.app.processEvents()
        self.app.processEvents()
        window.layout_timer.stop()

    def test_help_button_is_conditional_and_help_dialog_is_literal_plaintext(self):
        help_text = '<b>Literal help</b>\n<img src="file:///private/example"> stays text.'
        window = self.open(self.document())
        card = window.cards["note"]
        self.assertTrue(card.help_button.isHidden())
        with patch("visor.activity.QMessageBox.exec", autospec=True) as run:
            card.show_help()
            run.assert_not_called()
        self.update(window, help=help_text)
        self.assertTrue(card.help_button.isVisible())
        self.assertEqual(card.help_button.accessibleName(), "Help for Notes")
        observed = []

        def inspect_dialog(*args):
            box = args[0] if args else card.findChildren(QMessageBox)[-1]
            observed.append(box)
            self.assertEqual(box.textFormat(), Qt.TextFormat.PlainText)
            self.assertEqual(box.text(), help_text)
            self.assertEqual(box.windowTitle(), "About Notes")
            self.assertEqual(box.standardButtons(), QMessageBox.StandardButton.Close)
            return int(QMessageBox.StandardButton.Close)

        with patch("visor.activity.QMessageBox.exec", autospec=True, side_effect=inspect_dialog) as run:
            QTest.mouseClick(card.help_button, Qt.MouseButton.LeftButton)
            run.assert_called_once()
        self.assertEqual(len(observed), 1)
        self.update(window, help="")
        self.assertTrue(card.help_button.isHidden())

    def test_nonready_cues_remain_visible_and_metadata_updates_keep_complete_text_draft(self):
        window = self.open(self.document(help="Concise guidance"))
        card = window.cards["note"]
        card.editor.setPlainText("Keep this complete unsaved local review ✓")
        self.assertTrue(card.edit_dirty)
        draft = card.editor.toPlainText()
        original_revision = card.edit_revision
        pending = {"request_id": "existing-retry", "params": {"id": "board", "card_id": "note", "revision": 1,
                                                              "content": "<p>Unconfirmed local review</p>"}}
        card.pending_edit = copy.deepcopy(pending)
        signal = QSignalSpy(card.editor.textChanged)
        for state in ["loading", "empty", "error", "stale", "ready"]:
            with self.subTest(state=state):
                self.update(window, data_status={"state": state, "message": "<b>Literal status</b>",
                                               "source": "Synthetic evidence", "observed_at": "2026-10-27T17:30:00+00:00"})
                self.assertEqual(card.editor.toPlainText(), draft)
                self.assertTrue(card.edit_dirty)
                self.assertEqual(card.edit_revision, original_revision)
                self.assertEqual(card.pending_edit, pending)
                self.assertEqual(card.data_notice.textFormat(), Qt.TextFormat.PlainText)
                self.assertEqual(card.data_notice.text(), state.capitalize() + ": <b>Literal status</b>")
                self.assertEqual(card.data_notice.isVisible(), state != "ready")
                self.assertTrue(card.details.isHidden())
                self.assertFalse(card.caption.isVisible())
                self.assertNotIn("Synthetic evidence", card.data_notice.text())
                self.assertIn("Source: Synthetic evidence", card.caption.text())
                observed = datetime.fromisoformat("2026-10-27T17:30:00+00:00").astimezone()
                self.assertIn("Data as of: " + observed.strftime("%m/%d/%Y %I:%M %p").lower(), card.caption.text())
        self.assertEqual(signal.count(), 0, "Metadata refresh must not simulate user draft input")
        card.details_toggle.setChecked(True)
        self.app.processEvents()
        self.assertTrue(card.caption.isVisible())
        self.assertEqual(card.caption.textFormat(), Qt.TextFormat.PlainText)

    def test_status_objects_and_timestamps_fail_with_clean_value_errors(self):
        invalid = [None, [], "loading", 1, {}, {"state": []}, {"state": {}}, {"state": True},
                   {"state": "unknown"}, {"state": "ready", "command": "something"},
                   {"state": "loading", "message": []}, {"state": "loading", "source": {}},
                   {"state": "ready", "observed_at": 123},
                   {"state": "ready", "observed_at": "2026-10-27T12:30:00"},
                   {"state": "ready", "observed_at": "2026-10-27"},
                   {"state": "ready", "observed_at": "2026-10-27T12:30:00+24:00"},
                   {"state": "ready", "message": "bad\x00text"}]
        for status in invalid:
            with self.subTest(status=status), self.assertRaises(ValueError):
                _validate(self.document(data_status=status), ActivityPolicy())
        for observed_at in ["2026-10-27T17:30:00Z", "2026-10-27T13:30:00-04:00"]:
            with self.subTest(observed_at=observed_at):
                _validate(self.document(data_status={"state": "ready", "observed_at": observed_at}), ActivityPolicy())

    def test_extreme_timezone_offsets_are_cleanly_rejected_or_rendered_without_overflow(self):
        for observed_at in ["0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00"]:
            with self.subTest(observed_at=observed_at):
                document = self.document(data_status={"state": "stale", "observed_at": observed_at})
                try:
                    _validate(document, ActivityPolicy())
                except ValueError:
                    continue
                window = self.open(document)
                self.assertTrue(window.cards["note"].data_notice.isVisible())
                self.assertIn("Data as of:", window.cards["note"].caption.text())

    def test_help_and_status_use_the_smaller_of_field_and_operator_limits(self):
        for maximum in [8, 2000]:
            policy = replace(ActivityPolicy(), max_text_chars=maximum)
            for name, limit in [("help", 1000), ("message", 400), ("source", 160)]:
                boundary = min(maximum, limit)
                for length, valid in [(boundary, True), (boundary + 1, False)]:
                    with self.subTest(maximum=maximum, name=name, length=length):
                        properties = {"html": ""}
                        if name == "help":
                            properties["help"] = "x" * length
                        else:
                            properties["data_status"] = {"state": "stale", name: "x" * length}
                        document = self.document(**properties)
                        if valid:
                            _validate(document, policy)
                        else:
                            with self.assertRaises(ValueError):
                                _validate(document, policy)
        for length, valid in [(64, True), (65, False)]:
            observed = "2026-10-27T17:30:00." + "0" * (length - 26) + "+00:00"
            self.assertEqual(len(observed), length)
            document = self.document(data_status={"state": "ready", "observed_at": observed})
            if valid:
                _validate(document, ActivityPolicy())
            else:
                with self.assertRaises(ValueError):
                    _validate(document, ActivityPolicy())
        small = replace(ActivityPolicy(), max_text_chars=24)
        _validate(self.document(html="", data_status={"state": "ready", "observed_at": "2026-10-27T17:30:00Z"}), small)
        with self.assertRaises(ValueError):
            _validate(self.document(html="", data_status={"state": "ready", "observed_at": "2026-10-27T17:30:00+00:00"}), small)

    def test_small_cards_reserve_space_for_gadgets_actions_status_and_help(self):
        for kind, extra in [("gauge", {"value": 80, "min": 0, "max": 100, "detail": "Checks complete"}),
                            ("table", {"columns": ["Check", "Status"], "rows": [["Package", "Ready"]],
                                       "table_style": {"filterable": True, "copyable": True}})]:
            document = self.document(kind=kind, help="About this project card", geometry=[10, 10, 210, 145],
                                     data_status={"state": "stale", "message": "Review current evidence"},
                                     actions=[{"id": "inspect", "label": "Inspect evidence"}], **extra)
            document["cards"][0].pop("html")
            window = self.open(document)
            card = window.cards["note"]
            self.assertIs(card.card_scroll.widget(), card.inner)
            self.assertTrue(card.card_scroll.widgetResizable())
            self.assertEqual(card.card_scroll.frameShape(), QFrame.Shape.NoFrame)
            for width, height in [(210, 145), (260, 220), (340, 280)]:
                with self.subTest(kind=kind, width=width, height=height):
                    card.resize(width, height)
                    self.app.processEvents()
                    self.app.processEvents()
                    self.assertEqual((card.width(), card.height()), (width, height))
                    gadget = card.content
                    leaves = [("display", gadget.content), ("actions", gadget.actions), ("cue", card.data_notice),
                              ("help", card.help_button), ("details toggle", card.details_toggle)]
                    if kind == "table":
                        leaves.append(("table toolbar", gadget.content.toolbar))
                    rectangles = []
                    for name, leaf in leaves:
                        rectangle = leaf.rect().translated(leaf.mapTo(card.inner, QPoint(0, 0)))
                        self.assertTrue(leaf.isVisible())
                        self.assertTrue(card.inner.rect().contains(rectangle), name + " escapes card body")
                        for other_name, other in rectangles:
                            self.assertFalse(rectangle.intersects(other), name + " overlaps " + other_name)
                        rectangles.append((name, rectangle))
                        for target in [QPoint(2, 2), QPoint(max(2, leaf.width() - 3), max(2, leaf.height() - 3))]:
                            point = leaf.mapTo(card.inner, target)
                            card.card_scroll.ensureVisible(point.x(), point.y(), 2, 2)
                            self.app.processEvents()
                            viewport_point = leaf.mapTo(card.card_scroll.viewport(), target)
                            self.assertTrue(card.card_scroll.viewport().rect().contains(viewport_point),
                                            name + " cannot be reached by scrolling")
                    self.assertGreaterEqual(card.data_notice.height(), card.data_notice.heightForWidth(card.data_notice.width()))


if __name__ == "__main__":
    unittest.main()
