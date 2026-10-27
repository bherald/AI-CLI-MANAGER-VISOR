"""Optional browser controls use a fake listener and never call a live broker."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from visor.ui import Window
from visor.web_access import WebAccessDialog, validated_bind_host


def server(origin="http://127.0.0.1:34881", token="fixture-first"):
    value = SimpleNamespace(origin=origin, token=token, url=origin + "/#token=" + token,
                            start=Mock(), close=Mock())
    value.start.return_value = value
    return value


class WebAccessCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = SimpleNamespace(web_server=None, web_port=0, web_host="127.0.0.1",
                                         web_was_active=False, save_preferences=Mock())
        self.manager = SimpleNamespace(supervisor=SimpleNamespace(root=self.root),
                                       ensure_workspace=Mock(return_value=self.workspace))
        self.dialogs = []
        server_patch = patch("visor.web_access.PrismWebServer")
        self.server_type = server_patch.start()
        self.addCleanup(server_patch.stop)
        browser_patch = patch("visor.web_access.QDesktopServices.openUrl", return_value=True)
        self.browser = browser_patch.start()
        self.addCleanup(browser_patch.stop)

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def dialog(self):
        dialog = WebAccessDialog(self.manager)
        self.dialogs.append(dialog)
        return dialog

    def start(self, dialog, listener=None):
        listener = listener or server()
        self.server_type.return_value = listener
        dialog.start_button.click()
        return listener

    def test_opening_dialog_is_optional_off_without_starting_or_opening_anything(self):
        dialog = self.dialog()
        self.manager.ensure_workspace.assert_called_once_with()
        self.server_type.assert_not_called()
        self.browser.assert_not_called()
        self.assertIsNone(self.workspace.web_server)
        self.assertEqual(dialog.bind_ip.text(), "127.0.0.1")
        self.assertEqual(dialog.port.value(), 0)
        self.assertIn("Off", dialog.status.text())
        self.assertTrue(dialog.start_button.isEnabled())
        self.assertFalse(dialog.stop_button.isEnabled())
        self.assertFalse(dialog.open_visor.isEnabled())
        self.assertTrue(dialog.private_link.isReadOnly())
        self.assertFalse(dialog.private_link.text())

    def test_start_persists_actual_port_and_opens_both_surfaces_with_private_capability(self):
        dialog = self.dialog()
        listener = self.start(dialog)
        self.server_type.assert_called_once_with(self.root, host="127.0.0.1", port=0)
        listener.start.assert_called_once_with()
        self.assertIs(self.workspace.web_server, listener)
        self.assertEqual(self.workspace.web_port, 34881)
        self.assertTrue(self.workspace.web_was_active)
        self.workspace.save_preferences.assert_called_once_with()
        self.assertEqual(dialog.private_link.text(), listener.origin + "/visor#token=" + listener.token)
        self.assertFalse(dialog.bind_ip.isEnabled())
        self.assertFalse(dialog.start_button.isEnabled())
        dialog.open_visor.click()
        self.assertEqual(self.browser.call_args.args[0].toString(), listener.origin + "/visor#token=" + listener.token)
        dialog.open_prism.click()
        self.assertEqual(self.browser.call_args.args[0].toString(), listener.url)

    def test_stop_revokes_link_and_restart_has_new_capability(self):
        dialog = self.dialog()
        first = self.start(dialog)
        dialog.stop_button.click()
        first.close.assert_called_once_with()
        self.assertIsNone(self.workspace.web_server)
        self.assertFalse(self.workspace.web_was_active)
        self.assertEqual(dialog.private_link.text(), "")
        self.assertTrue(dialog.start_button.isEnabled())
        self.assertFalse(dialog.open_visor.isEnabled())
        self.assertFalse(dialog.copy_button.isEnabled())
        second = self.start(dialog, server(token="fixture-second"))
        self.assertIs(self.workspace.web_server, second)
        self.assertIn("fixture-second", dialog.private_link.text())
        self.assertNotIn("fixture-first", dialog.private_link.text())

    def test_port_is_restored_but_a_stopped_lan_host_is_not(self):
        self.workspace.web_port = 34881
        first = self.dialog()
        first.bind_ip.setText("192.0.2.10")
        self.start(first, server("http://192.0.2.10:34881"))
        first.stop_access()
        second = self.dialog()
        self.assertEqual(second.bind_ip.text(), "127.0.0.1")
        self.assertEqual(second.port.value(), 34881)
        self.assertIsNone(self.workspace.web_server)
        self.assertEqual(self.server_type.call_count, 1)

    def test_existing_prism_listener_is_reused_without_restart_and_host_is_accurate(self):
        listener = server("http://[fd00::2]:39811")
        self.workspace.web_server = listener
        dialog = self.dialog()
        self.assertEqual(dialog.bind_ip.text(), "fd00::2")
        self.assertEqual(dialog.port.value(), 39811)
        self.assertFalse(dialog.start_button.isEnabled())
        dialog.start_access()
        self.server_type.assert_not_called()
        listener.start.assert_not_called()
        self.assertIs(self.workspace.web_server, listener)
        dialog.reject()
        listener.close.assert_not_called()
        dialog.stop_access()
        listener.close.assert_called_once_with()

    def test_wildcards_multicast_and_non_ip_hosts_are_refused_before_listener_creation(self):
        dialog = self.dialog()
        for host in ("0.0.0.0", "::", "224.0.0.1", "ff02::1", "*", "example.invalid", "",
                     "::ffff:0.0.0.0", "::ffff:0:0", "0:0:0:0:0:ffff:0:0",
                     "::ffff:224.0.0.1", "::ffff:e000:1", "::ffff:239.255.255.255"):
            with self.subTest(host=host):
                dialog.bind_ip.setText(host)
                dialog.start_access()
                self.assertIn("could not start", dialog.status.text())
                self.assertIsNone(self.workspace.web_server)
        self.server_type.assert_not_called()
        self.workspace.save_preferences.assert_not_called()

    def test_specific_ipv4_and_ipv6_loopback_binds_remain_supported(self):
        expected = {"localhost": "127.0.0.1", "127.0.0.1": "127.0.0.1", "::1": "::1",
                    "0:0:0:0:0:0:0:1": "::1", "::ffff:127.0.0.1": "::ffff:7f00:1"}
        for host, normalized in expected.items():
            with self.subTest(host=host):
                self.assertEqual(validated_bind_host(host), normalized)

    def test_start_failure_closes_partial_listener_and_keeps_access_off(self):
        dialog = self.dialog()
        listener = server()
        listener.start.side_effect = OSError("Port is unavailable")
        self.server_type.return_value = listener
        dialog.start_access()
        listener.close.assert_called_once_with()
        self.assertIsNone(self.workspace.web_server)
        self.assertFalse(dialog.private_link.text())
        self.assertIn("Port is unavailable", dialog.status.text())
        self.workspace.save_preferences.assert_not_called()

    def test_supervisor_exit_stops_shared_listener_without_showing_prism(self):
        window = Window(self.root)
        window.timer.stop()
        window.draft_timer.stop()
        dialog = WebAccessDialog(window.prism, window)
        self.dialogs.append(dialog)
        try:
            workspace = window.prism.workspace
            self.assertFalse(workspace.isVisible())
            self.assertIsNone(workspace.web_server)
            listener = self.start(dialog)
            self.assertFalse(workspace.isVisible())
            saved = json.loads((self.root / "prism-workspace.json").read_text())
            self.assertEqual(saved["web_port"], 34881)
            self.assertEqual(saved["web_host"], "127.0.0.1")
            self.assertTrue(saved["web_was_active"])
            self.assertNotIn("host", saved)
            window.allow_close = True
            window.close()
            listener.close.assert_called_once_with()
            self.assertIsNone(workspace.web_server)
        finally:
            dialog.close()
            self.dialogs.remove(dialog)
            dialog.deleteLater()
            window.allow_close = True
            window.close()
            if window.prism.workspace:
                window.prism.workspace.deleteLater()
            window.deleteLater()
