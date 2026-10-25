import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from visor.ui import STYLE, Window


class ConnectedPairsCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.window = Window(Path.cwd() / ".cache" / "ui-tests")
        self.window.timer.stop()
        self.window.resize(1080, 760)
        self.window.show()
        QTest.qWait(20)

    def tearDown(self):
        self.window.draft_timer.stop()
        self.window.allow_close = True
        self.window.close()
        self.window.pool.waitForDone(1000)
        self.window.deleteLater()
        self.app.processEvents()

    def fixture(self, count=30):
        self.window.snapshot["sessions"] = [
            {
                "id": f"codex:session-{i:04}",
                "native_id": f"session-{i:04}",
                "provider": "codex",
                "name": "Coordinator" if i < 2 else f"Worker {i}",
                "cwd": "/fixture",
                "status": "idle",
                "latest": "",
                "participates": True,
                "heartbeat": None,
            }
            for i in range(count)
        ]
        self.window.snapshot["pairs"] = [
            ["codex:session-0000", f"codex:session-{i:04}"] for i in range(1, count)
        ]
        self.window.render_sessions()
        self.window.populate_combos()
        QTest.qWait(30)

    def test_many_pairs_scroll_and_duplicate_coordinators_have_exact_ids(self):
        self.fixture()
        self.assertEqual(self.window.pairs.rowCount(), 29)
        self.assertGreater(self.window.pairs.verticalScrollBar().maximum(), 0)
        self.assertTrue(self.window.pairs.verticalScrollBar().isVisible())
        self.assertIn("codex:ion-0000", self.window.pairs.item(0, 0).text())
        self.assertIn("codex:session-0000", self.window.pairs.item(0, 0).toolTip())
        self.assertIn("codex:ion-0001", self.window.pairs.item(0, 1).text())
        self.window.pairs.scrollToBottom()
        QTest.qWait(20)
        self.assertTrue(
            self.window.pairs.viewport()
            .rect()
            .intersects(self.window.pairs.visualItemRect(self.window.pairs.item(28, 0)))
        )

    def test_refresh_preserves_pair_selection_and_scroll_and_removal_target(self):
        self.fixture()
        self.window.pairs.selectRow(20)
        self.window.pairs.scrollToBottom()
        QTest.qWait(20)
        scroll = self.window.pairs.verticalScrollBar().value()
        chosen = self.window.pairs.item(20, 0).data(Qt.ItemDataRole.UserRole)
        self.window.snapshot["pairs"].insert(
            0, ["codex:session-0001", "codex:session-0002"]
        )
        self.window.populate_combos()
        self.assertEqual(self.window.pairs.verticalScrollBar().value(), scroll)
        selected = self.window.pairs.selectionModel().selectedRows()
        self.assertEqual(len(selected), 1)
        self.assertEqual(
            self.window.pairs.item(selected[0].row(), 0).data(Qt.ItemDataRole.UserRole),
            chosen,
        )
        with (
            patch(
                "visor.ui.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch.object(self.window, "rpc") as rpc,
        ):
            self.window.remove_pair()
        rpc.assert_called_once_with("pair", a=chosen[0], b=chosen[1], enabled=False)

    def test_poll_labels_preserve_exact_recipient_despite_duplicate_names(self):
        self.fixture(3)
        self.window.recipient.setCurrentIndex(
            self.window.recipient.findData("codex:session-0001")
        )
        self.window.snapshot["sessions"].reverse()
        self.window.populate_combos()
        self.assertEqual(self.window.recipient.currentData(), "codex:session-0001")
        self.assertIn("codex:ion-0001", self.window.recipient.currentText())
        self.assertNotEqual(
            self.window.source.itemText(1), self.window.source.itemText(2)
        )

    def test_unselected_or_removed_pair_never_removes_another_pair(self):
        self.fixture(4)
        with patch.object(self.window, "rpc") as rpc:
            self.window.remove_pair()
            rpc.assert_not_called()
        self.window.pairs.selectRow(1)
        self.window.snapshot["pairs"].pop(1)
        self.window.populate_combos()
        with patch.object(self.window, "rpc") as rpc:
            self.window.remove_pair()
            rpc.assert_not_called()

    def test_minimum_window_scrolls_communication_without_overlapping_controls(self):
        self.fixture()
        self.window.resize(840, 460)
        QTest.qWait(50)
        self.assertGreater(self.window.comm_body.verticalScrollBar().maximum(), 0)
        self.assertLessEqual(
            self.window.connected_section.geometry().bottom(),
            self.window.messages_section.y(),
        )
        self.window.comm_body.ensureWidgetVisible(self.window.composer)
        QTest.qWait(20)
        point = self.window.composer.mapTo(
            self.window.comm_body.viewport(), self.window.composer.rect().center()
        )
        self.assertTrue(self.window.comm_body.viewport().rect().contains(point))


if __name__ == "__main__":
    unittest.main()
