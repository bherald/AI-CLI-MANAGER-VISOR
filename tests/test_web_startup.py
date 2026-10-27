"""Restart offers web access only after explicit Yes; listeners are always fake."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from tests.test_web_access import server
from visor.paths import write_private
from visor.prism_workspace import PrismWorkspace
from visor.ui import Window, launch
from visor.web_access import WebAccessDialog, start_web_access


class WebStartupCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.windows = []
        self.dialogs = []
        self.workspaces = []
        listener_patch = patch("visor.web_access.PrismWebServer")
        self.listener_type = listener_patch.start()
        self.addCleanup(listener_patch.stop)
        browser_patch = patch("visor.web_access.QDesktopServices.openUrl", return_value=True)
        self.browser = browser_patch.start()
        self.addCleanup(browser_patch.stop)
        question_patch = patch("visor.web_access.QMessageBox.question", return_value=QMessageBox.StandardButton.No)
        self.question = question_patch.start()
        self.addCleanup(question_patch.stop)

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        for window in self.windows:
            window.allow_close = True
            window.close()
            if window.prism.workspace:
                window.prism.workspace.deleteLater()
            window.deleteLater()
        for workspace in self.workspaces:
            workspace.shutdown()
            workspace.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def saved(self):
        return json.loads((self.root / "prism-workspace.json").read_text())

    def remember(self, **changes):
        write_private(self.root / "prism-workspace.json", {"web_was_active": True,
            "web_host": "192.0.2.10", "web_port": 34881, "theme": "calm-dark", **changes})

    def window(self):
        window = Window(self.root)
        window.timer.stop()
        window.draft_timer.stop()
        self.windows.append(window)
        return window

    def test_fresh_or_explicitly_off_session_never_asks_or_creates_listener(self):
        for prior in (None, False):
            with self.subTest(prior=prior):
                if prior is not None:
                    self.remember(web_was_active=False)
                window = self.window()
                window.restore_web_access()
                window.restore_web_access()
                self.assertIsNone(window.prism.workspace.web_server)
        self.question.assert_not_called()
        self.listener_type.assert_not_called()
        self.browser.assert_not_called()

    def test_constructor_and_show_do_not_prompt_or_start_a_saved_listener(self):
        self.remember()
        window = self.window()
        workspace = window.prism.ensure_workspace()
        self.assertTrue(workspace.web_was_active)
        self.assertIsNone(workspace.web_server)
        window.show()
        workspace.show()
        self.app.processEvents()
        self.question.assert_not_called()
        self.listener_type.assert_not_called()

    def test_saved_on_offer_shows_exact_address_defaults_no_and_persists_off_once(self):
        self.remember()
        window = self.window()
        def decline(*args):
            self.listener_type.assert_not_called()
            self.assertIsNone(window.prism.workspace.web_server)
            self.assertIn("192.0.2.10:34881", args[2])
            self.assertEqual(args[-1], QMessageBox.StandardButton.No)
            return QMessageBox.StandardButton.No
        self.question.side_effect = decline
        window.restore_web_access()
        window.restore_web_access()
        self.question.assert_called_once()
        self.assertFalse(self.saved()["web_was_active"])
        self.assertFalse(window.prism.workspace.web_was_active)
        self.listener_type.assert_not_called()
        self.browser.assert_not_called()

    def test_yes_reuses_offered_host_port_and_new_capability_without_opening_browser(self):
        self.remember()
        window = self.window()
        listener = server("http://192.0.2.10:34881", token="fresh-restart-capability")
        self.listener_type.return_value = listener
        def approve(*_args):
            self.listener_type.assert_not_called()
            return QMessageBox.StandardButton.Yes
        self.question.side_effect = approve
        window.restore_web_access()
        self.listener_type.assert_called_once_with(self.root, host="192.0.2.10", port=34881)
        listener.start.assert_called_once_with()
        self.assertIs(window.prism.workspace.web_server, listener)
        self.assertTrue(self.saved()["web_was_active"])
        self.assertNotIn(listener.token, json.dumps(self.saved()))
        self.browser.assert_not_called()
        self.assertIn("new private link", window.status.text())

    def test_restore_failure_closes_partial_listener_and_persists_off(self):
        self.remember()
        window = self.window()
        listener = server("http://192.0.2.10:34881")
        listener.start.side_effect = OSError("That interface is unavailable")
        self.listener_type.return_value = listener
        self.question.return_value = QMessageBox.StandardButton.Yes
        window.restore_web_access()
        listener.close.assert_called_once_with()
        self.assertIsNone(window.prism.workspace.web_server)
        self.assertFalse(self.saved()["web_was_active"])
        self.assertIn("Web access is off", window.status.text())
        self.assertIn("interface is unavailable", window.status.text())
        self.browser.assert_not_called()

    def test_binding_refusal_is_off_and_never_reprompts_after_reconnect(self):
        self.remember()
        window = self.window()
        self.listener_type.side_effect = OSError("Address already in use")
        self.question.return_value = QMessageBox.StandardButton.Yes
        window.restore_web_access()
        window.restore_web_access()
        self.question.assert_called_once()
        self.assertFalse(self.saved()["web_was_active"])
        self.assertIsNone(window.prism.workspace.web_server)

    def test_workspace_browser_start_reports_thread_failure_and_cleans_up(self):
        window = self.window()
        workspace = window.prism.ensure_workspace()
        listener = server()
        listener.start.side_effect = RuntimeError("Could not start thread")
        self.listener_type.return_value = listener
        workspace.open_browser()
        listener.close.assert_called_once_with()
        self.assertIsNone(workspace.web_server)
        self.assertFalse(workspace.web_was_active)
        self.assertIn("Browser view unavailable", window.status.text())
        self.assertIn("Could not start thread", window.status.text())
        self.browser.assert_not_called()

    def test_explicit_stop_clears_prior_on_state_and_new_off_dialog_defaults_loopback(self):
        window = self.window()
        listener = server("http://192.0.2.10:34881")
        self.listener_type.return_value = listener
        start_web_access(window.prism, "192.0.2.10", 34881)
        dialog = WebAccessDialog(window.prism, window)
        self.dialogs.append(dialog)
        dialog.stop_access()
        listener.close.assert_called_once_with()
        self.assertFalse(self.saved()["web_was_active"])
        reopened = WebAccessDialog(window.prism, window)
        self.dialogs.append(reopened)
        self.assertEqual(reopened.bind_ip.text(), "127.0.0.1")
        self.assertEqual(reopened.port.value(), 34881)
        window.restore_web_access()
        self.question.assert_not_called()

    def test_shutdown_remembers_active_before_close_and_later_hidden_close_preserves_it(self):
        window = self.window()
        listener = server("http://192.0.2.10:34881")
        self.listener_type.return_value = listener
        start_web_access(window.prism, "192.0.2.10", 34881)
        workspace = window.prism.workspace
        workspace.close()
        listener.close.assert_not_called()
        self.assertTrue(self.saved()["web_was_active"])
        def stopped():
            self.assertTrue(self.saved()["web_was_active"])
            self.assertEqual(self.saved()["web_host"], "192.0.2.10")
        listener.close.side_effect = stopped
        window.allow_close = True
        window.close()
        self.assertIsNone(workspace.web_server)
        workspace.close()
        workspace.save_preferences()
        self.assertTrue(self.saved()["web_was_active"])
        self.assertEqual(self.saved()["web_port"], 34881)
        restarted = PrismWorkspace(self.root)
        self.workspaces.append(restarted)
        self.assertTrue(restarted.web_was_active)
        self.assertIsNone(restarted.web_server)
        self.question.assert_not_called()

    def test_corrupt_stale_preferences_never_prompt_or_start(self):
        cases = [[], None, {"web_was_active": "yes", "web_host": "192.0.2.10", "web_port": 34881},
                 {"web_was_active": True, "web_port": 34881},
                 {"web_was_active": True, "web_host": "0.0.0.0", "web_port": 34881},
                 {"web_was_active": True, "web_host": "::ffff:0.0.0.0", "web_port": 34881},
                 {"web_was_active": True, "web_host": "::ffff:0:0", "web_port": 34881},
                 {"web_was_active": True, "web_host": "::ffff:224.0.0.1", "web_port": 34881},
                 {"web_was_active": True, "web_host": "::ffff:efff:ffff", "web_port": 34881},
                 {"web_was_active": True, "web_host": [], "web_port": 34881},
                 {"web_was_active": True, "web_host": "192.0.2.10", "web_port": 0},
                 {"web_was_active": True, "web_host": "192.0.2.10", "web_port": "34881"}]
        for value in cases:
            with self.subTest(value=value):
                write_private(self.root / "prism-workspace.json", value)
                window = self.window()
                window.restore_web_access()
                self.assertFalse(window.prism.workspace.web_was_active)
                self.assertIsNone(window.prism.workspace.web_server)
        self.question.assert_not_called()
        self.listener_type.assert_not_called()

    def test_saved_ipv4_and_ipv6_loopback_keep_explicit_no_default_consent(self):
        for host in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            with self.subTest(host=host):
                self.remember(web_host=host)
                window = self.window()
                workspace = window.prism.ensure_workspace()
                self.assertTrue(workspace.web_was_active)
                window.restore_web_access()
                self.assertEqual(self.question.call_args.args[-1], QMessageBox.StandardButton.No)
                self.assertFalse(workspace.web_was_active)
                self.assertIsNone(workspace.web_server)
        self.assertEqual(self.question.call_count, 3)
        self.listener_type.assert_not_called()

    def test_only_normal_launcher_schedules_one_startup_check_smoke_never_does(self):
        for smoke in (False, True):
            with self.subTest(smoke=smoke), patch("visor.ui.QApplication") as application, \
                    patch("visor.ui.Window") as window_type, patch("visor.ui.QTimer.singleShot") as timer, \
                    patch("visor.instance.SupervisorInstance") as instance_type:
                application.return_value.exec.return_value = 0
                instance_type.return_value.start.return_value = True
                launch(self.root, smoke=smoke)
                callback = window_type.return_value.restore_web_access
                scheduled = [call for call in timer.call_args_list if call.args == (0, callback)]
                self.assertEqual(len(scheduled), 0 if smoke else 1)
                callback.assert_not_called()
