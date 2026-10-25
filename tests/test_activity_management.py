"""Isolated Qt management checks; all broker work uses a manually driven fake queue."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from visor.activity_management import RETENTION, PrismManagementDialog


class Supervisor(QWidget):
    def __init__(self, root):
        super().__init__()
        self.root = root
        self.client = Mock()
        self.queued = []
        self.poll = Mock()
        self.prism = Mock()

    def job(self, function, callback):
        self.queued.append((function, callback))

    def finish(self):
        function, callback = self.queued.pop(0)
        try:
            value = function()
        except (OSError, ValueError) as exc:
            callback(None, str(exc))
        else:
            callback(value, None)


class PrismManagementCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.supervisor = Supervisor(self.root)
        self.boards = [{"id": "board-a", "title": "<b>Exact title</b>", "owner": "claude:one",
                        "revision": 3, "updated": "2026-10-25T18:00:00Z", "view_action": "close"},
                       {"id": "board-b", "title": "Second board", "owner": "codex:two",
                        "revision": 1, "updated": "2026-10-25T17:00:00Z", "view_action": "open"}]
        self.supervisor.client.call.return_value = self.boards
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
        self.supervisor.close()
        self.supervisor.queued.clear()
        self.supervisor.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.temp.cleanup()

    def dialog(self):
        dialog = PrismManagementDialog(self.supervisor)
        self.dialogs.append(dialog)
        return dialog

    def loaded(self):
        dialog = self.dialog()
        self.supervisor.finish()
        return dialog

    def select(self, dialog, row=0):
        dialog.table.selectRow(row)

    def start_delete(self, dialog):
        self.select(dialog)
        with patch.object(dialog, "confirm_delete", return_value=True):
            dialog.delete_selected()
        return json.loads(dialog.pending_path.read_text())["pending"]["board-a"]

    def test_board_list_is_async_unselected_and_displays_metadata(self):
        dialog = self.dialog()
        self.supervisor.client.call.assert_not_called()
        self.assertFalse(dialog.delete_button.isEnabled())
        self.supervisor.finish()
        self.supervisor.client.call.assert_called_once_with("activity_list")
        self.assertIsNone(dialog.selected_id())
        self.assertFalse(dialog.delete_button.isEnabled())
        self.assertEqual([dialog.table.item(0, i).text() for i in range(5)],
                         ["<b>Exact title</b>\nboard-a", "claude:one", "2026-10-25T18:00:00Z", "3", "close"])
        self.select(dialog, 1)
        self.assertEqual(dialog.selected_id(), "board-b")
        self.assertTrue(dialog.delete_button.isEnabled())

    def test_confirmation_is_exact_plain_text_default_no_and_retention_explicit(self):
        dialog = self.loaded()
        with patch("visor.activity_management.QMessageBox") as box:
            box.Icon.Warning = QMessageBox.Icon.Warning
            box.StandardButton.Yes = QMessageBox.StandardButton.Yes
            box.StandardButton.No = QMessageBox.StandardButton.No
            box.return_value.exec.return_value = QMessageBox.StandardButton.No
            self.assertFalse(dialog.confirm_delete(self.boards[0]))
            prompt = box.return_value
            self.assertIn("<b>Exact title</b>", prompt.setText.call_args.args[0])
            self.assertIn("board-a", prompt.setText.call_args.args[0])
            self.assertIn("Revision: 3", prompt.setText.call_args.args[0])
            prompt.setDefaultButton.assert_called_once_with(QMessageBox.StandardButton.No)
            prompt.setInformativeText.assert_called_once_with(RETENTION)
        self.assertIn("backups are retained", RETENTION)
        self.assertIn("Provider sessions", RETENTION)

    def test_cancel_sends_nothing_and_creates_no_retry_record(self):
        dialog = self.loaded()
        self.select(dialog)
        self.supervisor.client.call.reset_mock()
        with patch.object(dialog, "confirm_delete", return_value=False):
            dialog.delete_selected()
        self.assertEqual(self.supervisor.queued, [])
        self.assertFalse(dialog.pending_path.exists())
        self.supervisor.client.call.assert_not_called()

    def test_delete_persists_exact_request_before_async_dispatch_and_refreshes_on_confirmation(self):
        dialog = self.loaded()
        self.supervisor.client.call.reset_mock()
        request = self.start_delete(dialog)
        self.supervisor.client.call.assert_not_called()
        self.assertTrue(dialog.busy)
        self.assertFalse(dialog.delete_button.isEnabled())
        self.assertEqual(request["expected_revision"], 3)
        self.supervisor.client.call.return_value = {"id": "board-a", "revision": 3, "deleted": True,
                                                   "retained_assets": True, "retained_backups": True}
        self.supervisor.finish()
        self.supervisor.client.call.assert_called_once_with("activity_delete", id="board-a", expected_revision=3,
                                                            request_id=request["request_id"], confirmed=True)
        self.assertEqual(json.loads(dialog.pending_path.read_text()), {"pending": {}})
        self.assertEqual(dialog.table.rowCount(), 1)
        self.supervisor.poll.assert_called_once_with()
        self.supervisor.prism.deleted.assert_called_once_with("board-a")
        self.assertNotIn("board-a", dialog.boards)

    def test_unknown_result_survives_reopen_missing_board_and_reuses_exact_current_client(self):
        dialog = self.loaded()
        request = self.start_delete(dialog)
        self.supervisor.client.call.side_effect = OSError("Connection lost")
        self.supervisor.finish()
        self.assertIn("unconfirmed", dialog.status.text())
        dialog.close()
        self.supervisor.client = Mock()
        self.supervisor.client.call.return_value = []
        reopened = self.loaded()
        self.assertEqual(reopened.table.rowCount(), 1)
        self.select(reopened)
        self.assertEqual(reopened.delete_button.text(), "Retry same deletion")
        with patch.object(reopened, "confirm_delete") as confirm:
            reopened.delete_selected()
            confirm.assert_not_called()
        self.supervisor.client.call.return_value = {"id": "board-a", "revision": 3, "deleted": True}
        self.supervisor.finish()
        self.supervisor.client.call.assert_called_with("activity_delete", id="board-a", expected_revision=3,
                                                       request_id=request["request_id"], confirmed=True)
        self.assertFalse(reopened.pending)

    def test_revision_conflict_requires_refresh_and_fresh_confirmation(self):
        dialog = self.loaded()
        original = self.start_delete(dialog)
        self.supervisor.client.call.side_effect = ValueError("Activity delete revision conflict; current revision is 4")
        self.supervisor.finish()
        self.supervisor.client.call.side_effect = None
        self.supervisor.client.call.return_value = [{**self.boards[0], "revision": 4}]
        dialog.reload()
        self.supervisor.finish()
        self.select(dialog)
        with patch.object(dialog, "confirm_delete", return_value=False):
            dialog.delete_selected()
        self.assertEqual(dialog.pending["board-a"], original)
        with patch.object(dialog, "confirm_delete", return_value=True) as confirm:
            dialog.delete_selected()
            self.assertEqual(confirm.call_args.args[0]["revision"], 4)
        newer = dialog.pending["board-a"]
        self.assertNotEqual(newer["request_id"], original["request_id"])
        self.assertEqual(newer["expected_revision"], 4)

    def test_failed_retry_persistence_prevents_rpc(self):
        dialog = self.loaded()
        self.select(dialog)
        with patch.object(dialog, "confirm_delete", return_value=True), \
                patch("visor.activity_management.write_private", side_effect=OSError("Disk full")):
            dialog.delete_selected()
        self.assertEqual(self.supervisor.queued, [])
        self.assertIn("not sent", dialog.status.text())

    def test_malformed_success_remains_uncertain_and_preserves_request(self):
        dialog = self.loaded()
        request = self.start_delete(dialog)
        self.supervisor.client.call.return_value = {"id": "wrong-board", "revision": 3, "deleted": True}
        self.supervisor.finish()
        self.assertEqual(dialog.pending["board-a"], request)
        self.assertEqual(dialog.table.rowCount(), 2)
        self.supervisor.poll.assert_not_called()

    def test_corrupt_retry_file_disables_deletion_without_overwriting(self):
        file = self.root / "prism-management.json"
        file.write_text("broken")
        dialog = self.loaded()
        self.select(dialog)
        self.assertFalse(dialog.delete_button.isEnabled())
        dialog.delete_selected()
        self.assertEqual(file.read_text(), "broken")
        self.assertEqual(self.supervisor.queued, [])

    def test_disconnected_and_failed_listing_disable_delete(self):
        self.supervisor.client = None
        dialog = self.dialog()
        self.assertFalse(dialog.delete_button.isEnabled())
        self.assertIn("disconnected", dialog.status.text())
        self.assertEqual(self.supervisor.queued, [])
        self.supervisor.client = Mock()
        self.supervisor.client.call.side_effect = OSError("Unavailable")
        dialog.reload()
        self.supervisor.finish()
        self.assertFalse(dialog.loaded)
        self.assertFalse(dialog.delete_button.isEnabled())

    def test_confirmed_delete_keeps_retry_until_local_cleanup_succeeds(self):
        dialog = self.loaded()
        request = self.start_delete(dialog)
        self.supervisor.client.call.return_value = {"id": "board-a", "revision": 3, "deleted": True}
        self.supervisor.prism.deleted.side_effect = OSError("Draft file busy")
        self.supervisor.finish()
        self.assertEqual(dialog.pending["board-a"], request)
        self.assertEqual(json.loads(dialog.pending_path.read_text())["pending"]["board-a"], request)
        self.assertIn("local cleanup pending", dialog.status.text())
        self.supervisor.prism.deleted.side_effect = None
        dialog.delete_selected()
        self.supervisor.finish()
        self.assertEqual(dialog.pending, {})

    def test_view_cleanup_precedes_retry_record_cleanup(self):
        dialog = self.loaded()
        request = self.start_delete(dialog)
        self.supervisor.client.call.return_value = {"id": "board-a", "revision": 3, "deleted": True}
        with patch("visor.activity_management.write_private", side_effect=OSError("Disk full")):
            self.supervisor.finish()
        self.supervisor.prism.deleted.assert_called_once_with("board-a")
        self.assertEqual(json.loads(dialog.pending_path.read_text())["pending"]["board-a"], request)
        self.assertIn("retry record cleanup failed", dialog.status.text())
