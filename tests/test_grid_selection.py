"""Isolated keyboard, refresh and partial-batch checks for operator grids."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox, QTableWidgetItem

from tests.test_activity_management import Supervisor
from visor.activity_management import PrismManagementDialog
from visor.selection import RowTable, capture_selection, restore_selection, selected_ids
from visor.ui import Window


def session(identity, name=None):
    return {"id": identity, "native_id": identity, "provider": "codex", "name": name or identity,
            "cwd": "/fixture", "status": "disconnected", "latest": "", "participates": False,
            "heartbeat": None}


def message(identity):
    return {"id": identity, "sender": "operator", "recipient": "codex:a", "created": 0,
            "body": identity, "state": "queued", "detail": ""}


class GridSelectionCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = Window(Path(self.temp.name))
        self.window.timer.stop()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.draft_timer.stop()
        self.window.allow_close = True
        self.window.close()
        self.window.pool.waitForDone(1000)
        self.window.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def click(self, grid, row, modifiers=Qt.KeyboardModifier.NoModifier):
        grid.scrollToItem(grid.item(row, 0))
        QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, modifiers,
                         grid.visualItemRect(grid.item(row, 0)).center())
        # QtTest's synthetic mouse modifier remains application-wide until a
        # keyboard event releases it. Match a user letting go after the click.
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            QTest.keyRelease(grid, Qt.Key.Key_Shift)
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            QTest.keyRelease(grid, Qt.Key.Key_Control)

    def test_native_click_toggle_shift_and_select_all_visible(self):
        self.window.snapshot["messages"] = [message(str(i)) for i in range(5)]
        self.window.render_messages()
        grid = self.window.messages
        self.click(grid, 0)
        self.click(grid, 2, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(selected_ids(grid), ["0", "2"])
        self.click(grid, 2, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(selected_ids(grid), ["0"])
        self.click(grid, 1)
        self.click(grid, 3, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(selected_ids(grid), ["1", "2", "3"])
        self.click(grid, 4)
        self.assertEqual(selected_ids(grid), ["4"])
        QTest.keyClick(grid, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(selected_ids(grid), ["0", "1", "2", "3", "4"])
        self.assertIn("(5)", self.window.delete_messages_button.text())

    def test_all_grids_accept_select_all_and_retain_ids_across_reorder(self):
        self.window.snapshot.update(sessions=[session("codex:a"), session("codex:b"), session("codex:c")],
                                    pairs=[["codex:a", "codex:b"], ["codex:a", "codex:c"]],
                                    messages=[message("m1"), message("m2")])
        self.window.render_sessions()
        self.window.populate_combos()
        self.window.render_messages()
        for grid in (self.window.sessions, self.window.pairs, self.window.messages):
            grid.setFocus()
            QTest.keyClick(grid, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
            expected = set(selected_ids(grid))
            self.assertEqual(len(expected), grid.rowCount())
            grid.sortItems(0, Qt.SortOrder.DescendingOrder)
            self.assertEqual(set(selected_ids(grid)), expected)
        expected = [set(selected_ids(grid)) for grid in (self.window.sessions, self.window.pairs, self.window.messages)]
        self.window.snapshot["sessions"].reverse()
        self.window.snapshot["pairs"].reverse()
        self.window.snapshot["messages"].reverse()
        self.window.render_sessions()
        self.window.populate_combos()
        self.window.render_messages()
        self.assertEqual([set(selected_ids(grid)) for grid in (self.window.sessions, self.window.pairs, self.window.messages)], expected)

    def test_removed_filtered_or_ambiguous_ids_are_not_retargeted(self):
        self.window.snapshot["messages"] = [message("a"), message("b")]
        self.window.render_messages()
        self.window.messages.selectAll()
        self.window.snapshot["messages"] = [message("b"), message("b"), message("c")]
        self.window.render_messages()
        self.assertEqual(selected_ids(self.window.messages), [])
        with patch.object(self.window, "rpc") as rpc:
            self.window.delete_messages()
            rpc.assert_not_called()

    def test_shift_anchor_survives_refresh_by_identity(self):
        grid = RowTable(3, 1)
        try:
            for row, identity in enumerate(("a", "b", "c")):
                item = QTableWidgetItem(identity)
                item.setData(Qt.ItemDataRole.UserRole, identity)
                grid.setItem(row, 0, item)
            grid.show()
            self.click(grid, 1)
            state = capture_selection(grid)
            grid.sortItems(0, Qt.SortOrder.DescendingOrder)
            restore_selection(grid, state)
            self.assertEqual(grid.currentItem().data(Qt.ItemDataRole.UserRole), "b")
            self.assertEqual(selected_ids(grid), ["b"])
        finally:
            grid.deleteLater()

    def test_shift_click_after_new_rows_uses_the_same_session_anchor(self):
        self.window.snapshot["messages"] = [message(identity) for identity in ("a", "b", "c", "d")]
        self.window.render_messages()
        self.click(self.window.messages, 1)
        self.window.snapshot["messages"] = [message(identity) for identity in ("new", "a", "b", "c", "d")]
        self.window.render_messages()
        self.click(self.window.messages, 4, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(selected_ids(self.window.messages), ["b", "c", "d"])

    def test_delete_connections_uses_all_rows_and_default_no(self):
        self.window.snapshot["pairs"] = [["a", "b"], ["a", "c"]]
        self.window.populate_combos()
        grid = self.window.pairs
        grid.selectAll()
        grid.setFocus()
        with patch("visor.ui.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as question, \
                patch.object(self.window, "rpc_batch") as batch:
            QTest.keyClick(grid, Qt.Key.Key_Delete)
            self.assertIn("2 selected", question.call_args.args[2])
            self.assertEqual(question.call_args.args[-1], QMessageBox.StandardButton.No)
            batch.assert_not_called()

    def test_session_batch_continues_after_failure_and_keeps_surviving_selection(self):
        self.window.snapshot["sessions"] = [session("codex:a", "A"), session("codex:b", "B")]
        self.window.render_sessions()
        self.window.sessions.selectAll()
        client = Mock()
        client.call.side_effect = [ValueError("B is still running"), {"deleted": True}]
        self.window.client = client
        def run(function, callback):
            callback(function(), None)
        with patch.object(self.window, "job", side_effect=run), patch.object(self.window, "poll"), \
                patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Yes), \
                patch.object(QMessageBox, "warning") as warning:
            self.window.delete_selected_sessions()
        self.assertEqual(client.call.call_count, 2)
        self.assertIn("1 of 2", self.window.status.text())
        self.assertEqual(set(selected_ids(self.window.sessions)), {"codex:a", "codex:b"})
        warning.assert_called_once()
        self.window.snapshot["sessions"] = [session("codex:a", "A")]
        self.window.render_sessions()
        self.assertEqual(selected_ids(self.window.sessions), ["codex:a"])

    def test_editor_delete_and_select_all_never_dispatch_grid_actions(self):
        editor = QLineEdit("some text", self.window.messages)
        editor.show()
        editor.setFocus()
        with patch.object(self.window, "delete_messages") as remove:
            QTest.keyClick(editor, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(editor.selectedText(), "some text")
            QTest.keyClick(editor, Qt.Key.Key_Delete)
            self.assertEqual(editor.text(), "")
            remove.assert_not_called()

    def test_another_windows_editor_does_not_suppress_keys_delivered_to_grid(self):
        other_editor = QLineEdit("Another window's draft")
        other_editor.show()
        other_editor.activateWindow()
        other_editor.setFocus()
        self.app.processEvents()
        self.window.snapshot["messages"] = [message("selected")]
        self.window.render_messages()
        self.window.messages.selectRow(0)
        try:
            with patch.object(self.window, "delete_messages") as remove:
                QTest.keyClick(self.window.messages, Qt.Key.Key_Delete)
                remove.assert_called_once_with()
            self.assertEqual(other_editor.text(), "Another window's draft")
        finally:
            other_editor.close()
            other_editor.deleteLater()


class BoardBatchCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.supervisor = Supervisor(Path(self.temp.name))
        self.boards = [{"id": key, "title": name, "revision": 1, "owner": "codex:a"}
                       for key, name in (("a", "Alpha"), ("b", "Beta"), ("c", "Gamma"))]
        self.supervisor.client.call.return_value = self.boards
        self.dialog = PrismManagementDialog(self.supervisor)
        self.supervisor.finish()

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.supervisor.close()
        self.supervisor.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_board_selection_survives_sort_reload_and_missing_ids(self):
        self.dialog.table.selectAll()
        self.dialog.table.sortItems(0, Qt.SortOrder.DescendingOrder)
        self.supervisor.client.call.return_value = self.boards[1:]
        self.dialog.reload()
        self.supervisor.finish()
        self.assertEqual(set(selected_ids(self.dialog.table)), {"b", "c"})
        self.assertIn("(2)", self.dialog.delete_button.text())

    def test_batch_confirm_once_persists_all_and_keeps_failed_retry_identity(self):
        self.dialog.table.selectAll()
        with patch.object(self.dialog, "confirm_delete_many", return_value=True) as confirm:
            self.dialog.delete_selected()
        self.assertEqual(confirm.call_args.args[1], 3)
        pending = json.loads(self.dialog.pending_path.read_text())["pending"]
        self.assertEqual(set(pending), {"a", "b", "c"})
        self.supervisor.client.call.side_effect = [
            {"id": "a", "revision": 1, "deleted": True}, ValueError("revision conflict"),
            {"id": "c", "revision": 1, "deleted": True}]
        for _ in range(3):
            self.supervisor.finish()
        self.assertEqual(self.dialog.pending, {"b": pending["b"]})
        self.assertEqual(selected_ids(self.dialog.table), ["b"])
        self.assertIn("Deleted 2 of 3", self.dialog.status.text())
        self.assertIn("revision conflict", self.dialog.status.text())
        self.supervisor.client.call.side_effect = None
        self.supervisor.client.call.return_value = {"id": "b", "revision": 1, "deleted": True}
        with patch.object(self.dialog, "confirm_delete") as confirm:
            self.dialog.delete_selected()
            confirm.assert_not_called()
        self.supervisor.finish()
        self.supervisor.client.call.assert_called_with("activity_delete", id="b", expected_revision=1,
                                                        request_id=pending["b"]["request_id"], confirmed=True)

    def test_batch_default_no_and_cancel_sends_nothing(self):
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.No) as execute:
            self.assertFalse(self.dialog.confirm_delete_many(self.boards, 3))
        self.assertEqual(execute.call_count, 1)
        self.dialog.table.selectAll()
        with patch.object(self.dialog, "confirm_delete_many", return_value=False):
            self.dialog.delete_selected()
        self.assertFalse(self.dialog.pending_path.exists())
        self.assertEqual(self.supervisor.queued, [])

    def test_duplicate_board_ids_disable_actions_conservatively(self):
        self.dialog.table.selectAll()
        self.supervisor.client.call.return_value = [self.boards[0], self.boards[0]]
        self.dialog.reload()
        self.supervisor.finish()
        self.assertFalse(self.dialog.loaded)
        self.assertFalse(self.dialog.delete_button.isEnabled())
        self.assertIn("Ambiguous", self.dialog.status.text())
