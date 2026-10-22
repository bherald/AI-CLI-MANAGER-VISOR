import gc
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt, QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from visor.providers import CodexRPC
from visor.ui import STYLE, Window


class TransportCase(unittest.TestCase):
    def test_control_socket_handshake_and_fragmented_rpc(self):
        real_popen = subprocess.Popen
        fixture = Path(__file__).parent / "fixtures" / "codex_proxy.py"

        def launch(_args, **options):
            return real_popen([sys.executable, str(fixture)], **options)

        with patch("visor.providers.executable", return_value="fake-codex"), patch("visor.providers.subprocess.Popen", side_effect=launch):
            with CodexRPC() as rpc:
                result = rpc.call("thread/list", {})
                self.assertEqual(result["data"][0]["name"], "Unicode café")
                self.assertEqual(result["data"][0]["id"], "fixture-id")
            self.assertIsNotNone(rpc.process.poll())


class UICase(unittest.TestCase):
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

    def test_message_selection_follows_identity_when_new_messages_arrive(self):
        def message(key):
            return {"id": key, "sender": "operator", "recipient": "fixture", "created": 0,
                    "body": key, "state": "queued", "detail": ""}
        self.window.snapshot["messages"] = [message("A"), message("B")]
        self.window.render_messages()
        self.window.messages.selectRow(1)
        self.window.snapshot["messages"] = [message("NEW"), message("A"), message("B")]
        self.window.render_messages()
        with patch("visor.ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), patch.object(self.window, "rpc") as rpc:
            self.window.delete_messages()
            rpc.assert_called_once_with("delete_messages", messages=["B"])
        self.window.snapshot["messages"] = [message("NEW"), message("A")]
        self.window.render_messages()
        self.assertEqual(self.window.messages.selectionModel().selectedRows(), [])

    def test_disconnect_uses_backoff_and_intentional_stop_disables_reconnect(self):
        self.window.client = object()
        with patch.object(self.window, "job", side_effect=lambda function, callback: callback(None, "connection refused")):
            self.window.poll()
        self.assertIsNone(self.window.client)
        with patch.object(self.window, "connect_broker") as connect:
            self.window.poll()
            connect.assert_not_called()
            self.window.next_reconnect = 0
            self.window.poll()
            connect.assert_called_once_with(automatic=True)
        self.window.broker_stopped = True
        with patch.object(self.window, "connect_broker") as connect:
            self.window.poll()
            connect.assert_not_called()
            self.window.refresh()
            connect.assert_called_once_with()

    def test_stop_broker_remains_stopped_after_next_poll(self):
        self.window.client = object()
        def rpc(action, callback=None, **params):
            self.assertEqual(action, "stop")
            callback(True)
        with patch("visor.ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), patch.object(self.window, "rpc", side_effect=rpc):
            self.window.stop_broker()
        self.assertTrue(self.window.broker_stopped)
        self.assertIsNone(self.window.client)
        with patch.object(self.window, "connect_broker") as connect:
            self.window.poll()
            connect.assert_not_called()

    def test_draft_writes_are_serialized_and_new_text_wins(self):
        saved, pending = {}, []
        class Client:
            def call(self, action, **params):
                self_action = action
                assert self_action == "message_draft"
                saved.update(params["value"])
                return dict(saved)
        self.window.client = Client()
        self.window.recipient.addItem("Fixture", "codex:fixture")
        with patch.object(self.window, "job", side_effect=lambda function, callback: pending.append((function, callback))):
            self.window.composer.setText("old draft")
            self.window.save_draft()
            self.window.composer.setText("latest draft")
            self.window.save_draft()
            self.assertEqual(len(pending), 1)
            function, callback = pending.pop(0)
            callback(function(), None)
            self.assertEqual(len(pending), 1)
            function, callback = pending.pop(0)
            callback(function(), None)
        self.assertEqual(saved, {"text": "latest draft", "recipient": "codex:fixture"})
        self.assertFalse(self.window.draft_dirty)

    def test_saved_draft_restores_recipient_and_does_not_overwrite_user_typing(self):
        self.window.recipient.addItem("A", "codex:a")
        self.window.recipient.addItem("B", "codex:b")
        self.window.draft_dirty = False
        value = {"text": "restored draft", "recipient": "codex:b"}
        with patch.object(self.window, "rpc", side_effect=lambda action, callback: callback(value)):
            self.window.load_draft()
        self.assertEqual(self.window.composer.text(), "restored draft")
        self.assertEqual(self.window.recipient.currentData(), "codex:b")
        self.window.composer.setText("new typing")
        with patch.object(self.window, "rpc", side_effect=lambda action, callback: callback(value)), patch.object(self.window, "save_draft") as save:
            self.window.load_draft()
            save.assert_called_once()
        self.assertEqual(self.window.composer.text(), "new typing")

    def test_missing_draft_recipient_does_not_silently_select_another_session(self):
        self.window.snapshot["sessions"] = [{"id": "codex:other", "name": "Other"}]
        value = {"text": "Private draft for a removed session", "recipient": "codex:missing"}
        with patch.object(self.window, "rpc", side_effect=lambda action, callback: callback(value)):
            self.window.load_draft()
        self.window.populate_combos()
        self.assertIsNone(self.window.recipient.currentData())
        self.assertEqual(self.window.composer.text(), value["text"])

    def test_delete_selected_messages_regardless_of_state(self):
        self.window.snapshot["messages"] = [
            {"id": state, "sender": "operator", "recipient": "test", "created": 0,
             "body": "Test", "detail": "", "state": state}
            for state in ("queued", "delivery_unknown", "completed")]
        self.window.render_messages()
        for row, state in enumerate(("queued", "delivery_unknown", "completed")):
            self.window.messages.selectRow(row)
            with patch("visor.ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
                    patch.object(self.window, "rpc") as rpc:
                self.window.delete_messages()
                rpc.assert_called_once_with("delete_messages", messages=[state])
        self.window.messages.selectAll()
        with patch("visor.ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
                patch.object(self.window, "rpc") as rpc:
            self.window.delete_messages()
            rpc.assert_called_once_with("delete_messages", messages=["queued", "delivery_unknown", "completed"])
        with patch("visor.ui.QMessageBox.question", return_value=QMessageBox.StandardButton.No), \
                patch.object(self.window, "rpc") as rpc:
            self.window.delete_messages(["delivery_unknown"])
            rpc.assert_not_called()

    def test_session_sorting_dates_selection_and_actions_survive_refresh(self):
        self.window.snapshot["sessions"] = [
            {"id": key, "native_id": key, "name": name, "cwd": "fixture", "provider": "codex",
             "status": "idle", "latest": "fixture", "participates": False, "heartbeat": None,
             "last_used": used}
            for key, name, used in [("old", "Zulu", 10), ("new", "Alpha", 200), ("unknown", "Middle", None)]]
        self.window.render_sessions()
        table = self.window.sessions
        self.assertEqual(table.horizontalHeaderItem(4).text(), "Last used")
        self.assertEqual([table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(3)], ["new", "old", "unknown"])
        table.sortItems(0, Qt.SortOrder.DescendingOrder)
        table.selectRow(0)
        self.window.render_sessions()
        self.assertEqual(table.item(0, 0).text(), "Zulu")
        self.assertEqual(table.item(table.selectionModel().selectedRows()[0].row(), 0).data(Qt.ItemDataRole.UserRole), "old")
        with patch.object(self.window, "focus_or_resume") as launch:
            next(b for b in table.cellWidget(0, 5).findChildren(QPushButton) if b.text() == "Resume").click()
            launch.assert_called_once_with("old", False)
        table.sortItems(4, Qt.SortOrder.AscendingOrder)
        self.assertEqual([table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(3)], ["unknown", "old", "new"])
        self.window.restore_ui({"columns": [230, 75, 95, 250, 165], "sort_column": 4, "sort_descending": True})
        self.assertEqual(table.columnWidth(5), 165)
        self.assertEqual(table.horizontalHeader().sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)

    def test_resize_cursors_cover_edges_corners_and_restore_text_cursor(self):
        base = self.window.centralWidget()
        base.setCursor(Qt.CursorShape.IBeamCursor)
        w, h = self.window.width(), self.window.height()
        for point, shape in [(QPoint(1, 100), Qt.CursorShape.SizeHorCursor),
                             (QPoint(w - 1, 100), Qt.CursorShape.SizeHorCursor),
                             (QPoint(100, 1), Qt.CursorShape.SizeVerCursor),
                             (QPoint(100, h - 1), Qt.CursorShape.SizeVerCursor),
                             (QPoint(1, 1), Qt.CursorShape.SizeFDiagCursor),
                             (QPoint(w - 1, h - 1), Qt.CursorShape.SizeFDiagCursor),
                             (QPoint(w - 1, 1), Qt.CursorShape.SizeBDiagCursor),
                             (QPoint(1, h - 1), Qt.CursorShape.SizeBDiagCursor)]:
            self.window.update_resize_cursor(base, point)
            self.assertEqual(base.cursor().shape(), shape)
        self.window.update_resize_cursor(base, QPoint(100, 100))
        self.assertEqual(base.cursor().shape(), Qt.CursorShape.IBeamCursor)
        QTest.mouseMove(base, QPoint(1, 200))
        self.assertEqual(base.cursor().shape(), Qt.CursorShape.SizeHorCursor)
        QTest.mouseMove(base, QPoint(100, 200))
        self.assertEqual(base.cursor().shape(), Qt.CursorShape.IBeamCursor)

    def test_compact_session_and_message_rows(self):
        self.assertEqual(self.window.sessions.verticalHeader().defaultSectionSize(), 25)
        self.assertEqual(self.window.messages.verticalHeader().defaultSectionSize(), 25)
        controls = {b.text(): b for b in self.window.arrange_box.findChildren(QPushButton)}
        with patch.object(self.window, "arrange") as arrange:
            controls["Cascade"].click()
            arrange.assert_called_once_with("arrange", mode="Cascade")

    def test_title_controls_and_operator_composer(self):
        buttons = self.window.findChildren(QPushButton)
        reset = next(b for b in buttons if b.accessibleName() == "Restore default size")
        self.assertFalse(reset.icon().isNull())
        self.window.resize(1200, 800)
        reset.click()
        self.assertLess(self.window.width(), 1200)
        self.window.agent_toggle.setChecked(False)
        self.assertTrue(self.window.composer.isEnabled())

    def test_edges_and_corners_resize_when_click_hits_child_widget(self):
        edges = self.window.resize_edges(QPoint(1, 1))
        self.assertEqual(edges, Qt.Edge.LeftEdge | Qt.Edge.TopEdge)
        self.assertEqual(self.window.resize_edges(QPoint(self.window.width() - 1, 200)), Qt.Edge.RightEdge)
        self.assertFalse(self.window.resize_edges(QPoint(100, 100)))
        with patch.object(self.window, "windowHandle") as handle:
            handle.return_value.startSystemResize.return_value = True
            QTest.mousePress(self.window.centralWidget(), Qt.MouseButton.LeftButton, pos=QPoint(1, 200))
            QTest.mouseRelease(self.window.centralWidget(), Qt.MouseButton.LeftButton, pos=QPoint(1, 200))
            handle.return_value.startSystemResize.assert_called_once_with(Qt.Edge.LeftEdge)

    @unittest.skipUnless(sys.platform == "win32", "Windows native border contract")
    def test_windows_native_hit_test_handles_all_edges_and_corners(self):
        import ctypes
        from ctypes import wintypes
        message = wintypes.MSG()
        message.message = 0x84
        w, h = self.window.width(), self.window.height()
        for point, expected in ((QPoint(1, 100), 10), (QPoint(w - 1, 100), 11),
                                (QPoint(100, 1), 12), (QPoint(100, h - 1), 15),
                                (QPoint(1, 1), 13), (QPoint(w - 1, 1), 14),
                                (QPoint(1, h - 1), 16), (QPoint(w - 1, h - 1), 17)):
            with patch("visor.ui.QCursor.pos", return_value=self.window.mapToGlobal(point)):
                result = self.window.nativeEvent(b"windows_generic_MSG", ctypes.addressof(message))
            self.assertEqual(result, (True, expected))

    def test_filters_single_rows_and_collapsible_communication(self):
        self.window.snapshot = {"pairs": [], "messages": [], "sessions": [
            {"id": "codex:a", "native_id": "a", "provider": "codex", "name": "Alpha", "cwd": "same/path",
             "status": "idle", "latest": "One line\ncontinued", "participates": False, "heartbeat": None},
            {"id": "claude:b", "native_id": "b", "provider": "claude", "name": "Beta", "cwd": "same/path",
             "status": "unknown", "latest": "", "participates": False, "heartbeat": None},
        ]}
        self.window.render_sessions()
        self.assertEqual(self.window.sessions.rowCount(), 2)
        self.assertNotIn("\n", self.window.sessions.item(0, 3).text())
        self.window.filter.setCurrentText("claude")
        self.assertEqual(self.window.sessions.rowCount(), 1)
        self.assertEqual(self.window.sessions.item(0, 0).text(), "Beta")
        self.window.open_only.setChecked(True)
        self.window.search.setText("no match")
        self.assertEqual(self.window.sessions.rowCount(), 0)
        self.window.clear_session_filters()
        self.assertEqual(self.window.sessions.rowCount(), 2)
        self.assertIn("2 of 2 sessions shown", self.window.counts.text())
        self.window.comm_toggle.click()
        self.assertFalse(self.window.comm_body.isVisible())
        self.window.comm_toggle.click()
        self.assertTrue(self.window.comm_body.isVisible())

    def test_worker_callback_runs_on_ui_thread(self):
        results = []
        self.window.job(lambda: 42, lambda result, error: results.append((result, error, QThread.currentThread())))
        for _ in range(100):
            if results:
                break
            QTest.qWait(10)
        self.assertTrue(results)
        self.assertEqual(results[0][:2], (42, None))
        self.assertEqual(results[0][2], self.app.thread())

    def test_double_click_focuses_clicked_session_after_sort_without_resuming(self):
        self.window.snapshot["sessions"] = [
            {"id": key, "native_id": key, "provider": "codex", "name": name, "cwd": "project",
             "status": "unknown", "latest": "", "participates": False, "heartbeat": None}
            for key, name in (("codex:a", "Alpha"), ("codex:z", "Zulu"))]
        self.window.snapshot["external_focus"] = {"codex:z": {"identity": {"pid": 42}}}
        self.window.render_sessions()
        self.window.sessions.sortItems(0, Qt.SortOrder.DescendingOrder)
        for row, column, key in ((0, 0, "codex:z"), (1, 1, "codex:a")):
            self.window.sessions.selectRow(1 - row)
            item = self.window.sessions.item(row, column)
            point = self.window.sessions.visualItemRect(item).center()
            with patch.object(self.window, "rpc") as rpc, patch("visor.ui.QMessageBox.question") as question:
                QTest.mouseClick(self.window.sessions.viewport(), Qt.MouseButton.LeftButton, pos=point)
                QTest.mouseDClick(self.window.sessions.viewport(), Qt.MouseButton.LeftButton, pos=point)
                rpc.assert_called_once_with("focus_target", session=key, callback=self.window.activate_terminal)
                question.assert_not_called()

    def test_focus_verifies_in_worker_and_activates_on_ui_thread(self):
        calls, activations = [], []
        target = {"token": "terminal", "identity": {"hwnd": 123}}

        class Client:
            def call(self, action, **params):
                calls.append((action, params, QThread.currentThread()))
                return target

        self.window.client = Client()
        with patch.object(self.window, "poll"), patch("visor.ui.focus_verified", side_effect=lambda value: activations.append((value, QThread.currentThread()))):
            self.window.focus_or_resume("codex:a", True)
            for _ in range(100):
                QTest.qWait(10)
                if activations:
                    break
        self.assertEqual(calls[0][:2], ("focus_target", {"session": "codex:a"}))
        self.assertNotEqual(calls[0][2], self.app.thread())
        self.assertEqual(activations, [(target, self.app.thread())])

    def test_external_focus_link_requires_confirmation_and_defaults_to_cancel(self):
        item = {"title": "External terminal", "identity": {"pid": 42, "created": 1, "handle": "x"}}
        calls = []
        def rpc(action, **params):
            calls.append((action, params))
            if action == "external_window_candidates":
                params["callback"]([item])
        with patch.object(self.window, "rpc", side_effect=rpc), \
                patch("visor.ui.QInputDialog.getItem", side_effect=lambda *args: (args[3][0], True)), \
                patch("visor.ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel) as question:
            self.window.link_external("codex:a")
        self.assertEqual(question.call_args.args[-1], QMessageBox.StandardButton.Cancel)
        self.assertEqual([c[0] for c in calls], ["external_window_candidates"])
        calls.clear()
        with patch.object(self.window, "rpc", side_effect=rpc), \
                patch("visor.ui.QInputDialog.getItem", side_effect=lambda *args: (args[3][0], True)), \
                patch("visor.ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.window.link_external("codex:a")
        self.assertEqual(calls[-1], ("external_focus_link", {"session": "codex:a", "identity": item["identity"], "confirmed": True}))

    def test_external_link_shows_focus_without_claiming_managed_open(self):
        session = {"id": "codex:a", "native_id": "a", "provider": "codex", "name": "External",
                   "cwd": "project", "status": "idle", "latest": "", "participates": False, "heartbeat": None}
        self.window.snapshot = {"sessions": [session], "pairs": [], "messages": [],
                                "external_focus": {"codex:a": {"identity": {"pid": 42}}}}
        self.window.render_sessions()
        buttons = self.window.sessions.cellWidget(0, 5).findChildren(QPushButton)
        self.assertIn("Focus", [b.text() for b in buttons])
        self.assertIn("0 VISOR terminals", self.window.counts.text())
        self.window.open_only.setChecked(True)
        self.assertEqual(self.window.sessions.rowCount(), 0)

    def test_independent_working_sessions_explain_empty_terminal_filter(self):
        self.window.snapshot["sessions"] = [
            {"id": key, "native_id": key, "provider": "codex", "name": key, "cwd": "project",
             "status": "working", "latest": "", "participates": False, "heartbeat": None}
            for key in ("first", "second")]
        self.window.render_sessions()
        self.assertIn("2 working shown", self.window.counts.text())
        self.window.open_only.setChecked(True)
        self.assertEqual(self.window.sessions.rowCount(), 0)
        self.assertIn("0 working shown", self.window.counts.text())
        self.assertTrue(self.window.session_hint.isVisible())
        self.assertIn("independent terminals", self.window.session_hint.text())
        self.window.clear_session_filters()
        self.assertEqual(self.window.sessions.rowCount(), 2)
        self.assertFalse(self.window.session_hint.isVisible())

    def test_connect_discovers_sessions_from_empty_registry(self):
        session = {"id": "codex:a", "native_id": "a", "provider": "codex", "name": "Discovered",
                   "cwd": "project", "status": "idle", "latest": "", "participates": False,
                   "heartbeat": None}

        class Client:
            def __init__(self):
                self.sessions = []
                self.refreshes = 0

            def call(self, action, **params):
                if action == "refresh":
                    self.refreshes += 1
                    self.sessions = [session]
                    return 1
                if action == "snapshot":
                    return {"sessions": self.sessions, "messages": [], "pairs": [], "agent_enabled": False}
                return {}

        client = Client()
        with patch("visor.ui.ensure_broker", return_value=client):
            self.window.connect_broker()
            for _ in range(200):
                QTest.qWait(10)
                if self.window.sessions.rowCount():
                    break
        self.assertEqual(client.refreshes, 1)
        self.assertEqual(self.window.sessions.rowCount(), 1)
        self.assertEqual(self.window.recipient.count(), 1)

    def test_refresh_poll_during_inflight_snapshot_is_not_dropped(self):
        callbacks = []
        self.window.client = object()
        snapshot = {"sessions": [], "pairs": [], "messages": [], "agent_enabled": False}
        with patch.object(self.window, "job", side_effect=lambda fn, cb: callbacks.append(cb)), \
                patch("visor.ui.QTimer.singleShot") as schedule:
            self.window.poll()
            self.window.poll()
            self.assertEqual(len(callbacks), 1)
            callbacks[0](snapshot, None)
            schedule.assert_called_once_with(0, self.window.poll)
            self.assertFalse(self.window.polling)
            self.assertFalse(self.window.poll_pending)

    def test_worker_results_survive_until_ui_processes_events(self):
        results = []
        for i in range(20):
            self.window.job(lambda n=i: n, lambda result, error: results.append(result))
        self.assertTrue(self.window.pool.waitForDone(2000))
        gc.collect()
        self.assertEqual(len(self.window.jobs), 20)
        QTest.qWait(30)
        self.assertEqual(sorted(results), list(range(20)))
        self.assertEqual(len(self.window.jobs), 0)

    def test_delete_cancel_does_not_send_rpc_and_defaults_to_cancel(self):
        self.window.snapshot = {"sessions": [{"id": "claude:chosen", "provider": "claude", "name": "Chosen"}]}
        dialogs = []
        def cancel(dialog):
            dialogs.append(dialog)
            self.assertEqual(dialog.defaultButton().text(), "Cancel")
            self.assertIn("Running work may be lost", dialog.informativeText())
        with patch.object(QMessageBox, "exec", cancel), patch.object(self.window, "rpc") as rpc:
            self.window.delete_session("claude:chosen")
            rpc.assert_not_called()
        self.assertEqual(len(dialogs), 1)

    def test_poll_preserves_open_session_menu_and_wrap_up_target(self):
        session = {"id": "codex:chosen", "native_id": "chosen", "provider": "codex", "name": "Chosen",
                   "cwd": "project", "status": "idle", "latest": "", "participates": False,
                   "heartbeat": None}
        snapshot = {"sessions": [session], "pairs": [], "messages": [], "agent_enabled": False}
        self.window.snapshot = snapshot
        self.window.render_sessions()
        calls = []

        class Client:
            def call(self, action, **params):
                calls.append((action, params))
                return snapshot if action == "snapshot" else []

        self.window.client = Client()
        more = next(b for b in self.window.sessions.cellWidget(0, 5).findChildren(QPushButton) if b.menu())
        menu = more.menu()
        # Snapshot may already be in flight when the operator opens the menu.
        self.window.poll()
        self.window.pool.waitForDone(2000)
        menu.popup(more.mapToGlobal(more.rect().bottomLeft()))
        QTest.qWait(50)
        self.assertTrue(menu.isVisible())
        action = next(a for a in menu.actions() if a.text() == "Wrap up")
        action.trigger()
        menu.hide()
        for _ in range(100):
            QTest.qWait(10)
            if any(a == "wrap_up" for a, _ in calls):
                break
        requests = [p for a, p in calls if a == "wrap_up"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["sessions"], ["codex:chosen"])
