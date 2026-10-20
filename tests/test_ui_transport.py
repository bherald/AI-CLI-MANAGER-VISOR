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
        self.window.allow_close = True
        self.window.close()
        self.window.pool.waitForDone(1000)
        self.window.deleteLater()
        self.app.processEvents()

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
        with patch("visor.ui.QMessageBox.question", return_value=QMessageBox.StandardButton.No), \
                patch.object(self.window, "rpc") as rpc:
            self.window.delete_messages(["delivery_unknown"])
            rpc.assert_not_called()

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
        buttons = self.window.sessions.cellWidget(0, 4).findChildren(QPushButton)
        self.assertIn("Focus", [b.text() for b in buttons])
        self.assertIn("0 managed open", self.window.counts.text())
        self.window.open_only.setChecked(True)
        self.assertEqual(self.window.sessions.rowCount(), 0)

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
        more = next(b for b in self.window.sessions.cellWidget(0, 4).findChildren(QPushButton) if b.menu())
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
