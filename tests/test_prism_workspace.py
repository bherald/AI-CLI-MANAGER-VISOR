import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from visor.activity import ActivityManager, ActivityWindow
from visor.activity_components import template
from visor.paths import write_private
from visor.prism_workspace import PrismWorkspace


class PrismWorkspaceCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = PrismWorkspace(self.root)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.on_view = None
            window.watch.stop()
            window.receipt_timer.stop()
            window.layout_timer.stop()
        self.workspace.close()
        self.workspace.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def board(self, identity):
        document = template("project-overview", identity, identity.title())
        path = self.root / (identity + ".json")
        write_private(path, document)
        board = ActivityWindow(path, self.root / "layouts")
        board.session_names = {"private-id": "Release coordinator"}
        self.windows.append(board)
        self.workspace.add_board(board)
        self.app.processEvents()
        return board

    def test_boards_share_one_window_and_reopen_after_hide(self):
        one, two = self.board("one"), self.board("two")
        self.assertFalse(one.isWindow())
        self.assertFalse(two.isWindow())
        self.assertEqual(self.workspace.selector.count(), 2)
        self.assertEqual(self.workspace.sessions.text(), "Connected: Release coordinator")
        self.assertNotIn("private-id", self.workspace.sessions.text())
        self.assertFalse(one.isVisible())
        self.assertTrue(two.isVisible())
        self.assertGreaterEqual(self.workspace.previous.width(), 100)
        self.assertTrue(self.workspace.actions.isHidden())
        self.workspace.advance(-1)
        self.assertEqual(self.workspace.stack.currentWidget(), one)
        self.workspace.hide_current()
        self.assertIn("one", self.workspace.hidden_boards)
        self.workspace.add_board(one)
        self.assertTrue(one.isVisible())
        self.assertNotIn("one", self.workspace.hidden_boards)

    def test_card_actions_default_closed_and_layout_undo_preserves_document(self):
        board = self.board("layout")
        card = next(iter(board.cards.values()))
        self.assertTrue(card.details.isHidden())
        before = copy.deepcopy(board.document)
        original = card.geometry()
        card.move(card.x() + 90, card.y() + 60)
        board.save_layout()
        moved = card.geometry()
        board.undo_layout()
        self.assertEqual(card.geometry(), original)
        board.redo_layout()
        self.assertEqual(card.geometry(), moved)
        self.assertEqual(board.document, before)
        persisted = json.loads(board.layout_path.read_text())
        self.assertEqual(persisted["board_id"], "layout")

    def test_hiding_workspace_does_not_delete_boards_or_drafts(self):
        one = self.board("one")
        path = one.source
        self.workspace.close()
        self.assertTrue(path.exists())
        self.assertIn("one", self.workspace.boards)
        self.workspace.add_board(one)
        self.assertTrue(self.workspace.isVisible())
        self.assertTrue(one.isVisible())

    def test_same_named_boards_remain_individually_selectable_without_ids(self):
        manager = ActivityManager(SimpleNamespace(root=self.root))
        manager.boards = [{"id": "private-one", "title": "Release"}, {"id": "private-two", "title": "Release"}]
        manager.operator_open = Mock()
        def choose(parent, title, prompt, labels, index, editable):
            self.assertEqual(labels, ["1. Release", "2. Release"])
            return labels[1], True
        with patch("visor.activity.QInputDialog.getItem", side_effect=choose):
            manager.choose()
        manager.operator_open.assert_called_once_with(manager.boards[1])


if __name__ == "__main__":
    unittest.main()
