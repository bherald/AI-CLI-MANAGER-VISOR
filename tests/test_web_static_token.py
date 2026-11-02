"""Synthetic browser capabilities; isolated profiles/listeners, never real credentials."""
import contextlib
import http.client
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

from visor.broker import Broker
from visor.data_security import ensure_windows_private
from visor.paths import write_private
from visor.prism_web import (
    PrismWebServer,
    read_web_access_settings,
    save_web_access_settings,
    validated_web_token,
)
from visor.state_backup import restore, snapshot
from visor.web_access import WebAccessDialog, restore_web_access


class WebStaticTokenCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        if os.name == "nt":
            ensure_windows_private(self.root, created=True)
        self.workspace = SimpleNamespace(web_server=None, web_port=0, web_host="127.0.0.1",
                                         web_was_active=False, save_preferences=Mock())
        self.manager = SimpleNamespace(supervisor=SimpleNamespace(root=self.root, status=Mock()),
                                       ensure_workspace=Mock(return_value=self.workspace))
        self.dialogs = []
        self.listeners = []
        self.client = SimpleNamespace(call=Mock(return_value={}))

        def create(*args, **kwargs):
            listener = PrismWebServer(*args, **kwargs, client=self.client)
            self.listeners.append(listener)
            return listener

        factory = patch("visor.web_access.PrismWebServer", side_effect=create)
        self.factory = factory.start()
        self.addCleanup(factory.stop)

    def tearDown(self):
        for listener in self.listeners:
            listener.close()
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

    def request(self, listener, token):
        connection = http.client.HTTPConnection("127.0.0.1", listener.httpd.server_port, timeout=3)
        try:
            connection.request("GET", "/api/catalog", headers={"Authorization": "Bearer " + token})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_select_generate_override_and_reopen_always_save_without_starting(self):
        dialog = self.dialog()
        self.assertEqual(dialog.token_edit.echoMode(), QLineEdit.EchoMode.Password)
        self.assertFalse((self.root / "web-access.json").exists())
        dialog.token_mode.setCurrentIndex(1)
        first = read_web_access_settings(self.root)["token"]
        self.assertGreaterEqual(len(first), 32)
        self.assertNotIn(first, dialog.status.text())
        dialog.generate_button.click()
        generated = read_web_access_settings(self.root)["token"]
        self.assertNotEqual(first, generated)
        dialog.token_edit.setText("synthetic-static-override_20261101")
        dialog.token_edit.editingFinished.emit()
        self.assertEqual(read_web_access_settings(self.root)["token"], "synthetic-static-override_20261101")
        reopened = self.dialog()
        self.assertEqual(reopened.token_mode.currentData(), "saved_static")
        self.assertEqual(reopened.token_edit.text(), "synthetic-static-override_20261101")
        token_path = self.root / "web-access.json"
        if os.name == "nt":
            ensure_windows_private(token_path, created=False, file=True)
        else:
            self.assertEqual(stat.S_IMODE(token_path.stat().st_mode), 0o600)
        self.factory.assert_not_called()
        self.assertIsNone(self.workspace.web_server)

    def test_static_survives_stop_start_and_restart_requires_existing_consent(self):
        saved = "synthetic-stable-token_20261101"
        save_web_access_settings(self.root, "saved_static", saved)
        dialog = self.dialog()
        dialog.start_access()
        self.assertEqual(self.workspace.web_server.token, saved)
        dialog.stop_access()
        self.assertEqual(read_web_access_settings(self.root)["token"], saved)
        dialog.start_access()
        self.assertEqual(self.workspace.web_server.token, saved)
        dialog.stop_access()
        self.workspace.web_was_active = True
        with patch("visor.web_access.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            restore_web_access(self.manager)
        self.assertIsNone(self.workspace.web_server)
        self.assertEqual(read_web_access_settings(self.root)["token"], saved)
        self.workspace.web_was_active = True
        self.workspace.web_port = 0
        with patch("visor.web_access.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            restore_web_access(self.manager)
        self.assertEqual(self.workspace.web_server.token, saved)

    def test_generated_and_overridden_rotation_revoke_old_http_token_only_after_save(self):
        dialog = self.dialog()
        dialog.token_mode.setCurrentIndex(1)
        dialog.start_access()
        listener = self.workspace.web_server
        old = listener.token
        self.assertEqual(self.request(listener, old)[0], 200)
        dialog.generate_token()
        generated = listener.token
        self.assertNotEqual(old, generated)
        self.assertEqual(self.request(listener, old)[0], 401)
        self.assertEqual(self.request(listener, generated)[0], 200)
        self.assertIn(generated, listener.url)
        dialog.token_edit.setText("synthetic-second-override_20261101")
        dialog.save_token()
        self.assertEqual(self.request(listener, generated)[0], 401)
        self.assertEqual(self.request(listener, "synthetic-second-override_20261101")[0], 200)
        self.assertNotIn(listener.token, dialog.status.text())

    def test_atomic_save_failure_keeps_old_file_and_active_token_and_does_not_leak(self):
        dialog = self.dialog()
        dialog.token_mode.setCurrentIndex(1)
        dialog.start_access()
        listener = self.workspace.web_server
        old = listener.token
        original = (self.root / "web-access.json").read_bytes()
        dialog.token_edit.setText("synthetic-failed-override_20261101")
        output = io.StringIO()
        with patch("visor.paths.os.replace", side_effect=OSError("synthetic-failed-override_20261101")), contextlib.redirect_stderr(output):
            self.assertFalse(dialog.save_token())
        self.assertEqual(original, (self.root / "web-access.json").read_bytes())
        self.assertEqual(listener.token, old)
        self.assertEqual(self.request(listener, old)[0], 200)
        self.assertNotIn(dialog.token_edit.text(), dialog.status.text())
        self.assertNotIn(dialog.token_edit.text(), output.getvalue())
        self.assertFalse(list(self.root.glob(".write-*")))

    def test_invalid_header_url_unicode_or_control_override_never_changes_current_token(self):
        good = "synthetic-safe-browser-token"
        save_web_access_settings(self.root, "saved_static", good)
        for token in ("", "Bearer value", "line\r\nAuthorization: value", "https://example.invalid/#token=value",
                      "url?token=value", "value#fragment", "a/b", "a+b", "a=b", "tab\tvalue", "nul\0value", "café", "a" * 513):
            with self.subTest(kind=type(token).__name__):
                with self.assertRaises(ValueError):
                    save_web_access_settings(self.root, "saved_static", token)
                self.assertEqual(read_web_access_settings(self.root)["token"], good)
        for mode in ([], {}, None, 1):
            with self.subTest(mode_type=type(mode).__name__):
                write_private(self.root / "web-access.json", {"mode": mode, "token": good})
                with self.assertRaises(ValueError):
                    read_web_access_settings(self.root)
        validated_web_token("synthetic.safe_token~with-hyphen")

    def test_private_file_symlink_and_permissions_are_enforced_without_overwrite(self):
        target = self.root / "unrelated.json"
        write_private(target, {"keep": "original"})
        path = self.root / "web-access.json"
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            save_web_access_settings(self.root, "saved_static", "synthetic-token")
        self.assertEqual(json.loads(target.read_text()), {"keep": "original"})
        path.unlink()
        save_web_access_settings(self.root, "saved_static", "synthetic-token")
        if os.name != "nt":
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                read_web_access_settings(self.root)
            with self.assertRaises(ValueError):
                save_web_access_settings(self.root, "saved_static", "synthetic-new-token")

    def test_per_start_mode_retains_saved_choice_but_rotates_runtime_capability(self):
        save_web_access_settings(self.root, "saved_static", "synthetic-static-choice")
        dialog = self.dialog()
        dialog.start_access()
        old = self.workspace.web_server.token
        dialog.token_mode.setCurrentIndex(0)
        current = self.workspace.web_server.token
        self.assertNotEqual(old, current)
        self.assertEqual(read_web_access_settings(self.root)["token"], old)
        dialog.stop_access()
        dialog.start_access()
        self.assertNotEqual(current, self.workspace.web_server.token)

    def test_another_static_listener_cannot_keep_old_access_after_rotation_or_recovery(self):
        save_web_access_settings(self.root, "saved_static", "synthetic-old-static-token")
        listener = PrismWebServer(self.root, client=self.client).start()
        self.listeners.append(listener)
        self.assertEqual(self.request(listener, "synthetic-old-static-token")[0], 200)
        save_web_access_settings(self.root, "saved_static", "synthetic-new-static-token")
        self.assertEqual(self.request(listener, "synthetic-old-static-token")[0], 401)
        self.assertEqual(self.request(listener, "synthetic-new-static-token")[0], 401)
        listener.rotate_browser_token("synthetic-new-static-token", saved_static=True)
        self.assertEqual(self.request(listener, "synthetic-new-static-token")[0], 200)
        (self.root / "web-access.json").unlink()
        self.assertEqual(self.request(listener, "synthetic-new-static-token")[0], 401)

    def test_known_current_credentials_reject_before_save_rotation_and_start_with_old_access_intact(self):
        broker = Broker(self.root)
        write_private(self.root / "broker.json", {"port": 1, "token": broker.token})
        actor = broker.store.upsert_session("codex", "synthetic-collision-role", "Synthetic", str(self.root))
        credentials = [("operator", broker.token)] + [(role, broker.store.issue_credential(actor, role=role))
                                                    for role in ("agent", "helper", "activity")]
        dialog = self.dialog()
        dialog.token_mode.setCurrentIndex(1)
        dialog.start_access()
        listener = self.workspace.web_server
        current = listener.token
        old_file = (self.root / "web-access.json").read_bytes()
        for role, candidate in credentials:
            with self.subTest(role=role):
                dialog.token_edit.setText(candidate)
                self.assertFalse(dialog.save_token())
                self.assertEqual(old_file, (self.root / "web-access.json").read_bytes())
                self.assertEqual(current, listener.token)
                self.assertEqual(self.request(listener, current)[0], 200)
                self.assertNotIn(candidate, dialog.status.text())
                with patch("visor.prism_web.ThreadingHTTPServer") as factory:
                    with self.assertRaises(ValueError) as rejected:
                        PrismWebServer(self.root, client=self.client, token=candidate)
                    factory.assert_not_called()
                self.assertNotIn(candidate, str(rejected.exception))
                # Simulate an older writer having saved a bad value: startup is
                # guarded independently of the new entry UI/save function.
                write_private(self.root / "web-access.json", {"mode": "saved_static", "token": candidate})
                with patch("visor.prism_web.ThreadingHTTPServer") as factory:
                    with self.assertRaises(ValueError):
                        PrismWebServer(self.root, client=self.client)
                    factory.assert_not_called()
                write_private(self.root / "web-access.json", {"mode": "saved_static", "token": current})
                listener.rotate_browser_token(current, saved_static=True)
        dialog.token_edit.setText("synthetic-new-independent-browser-token")
        self.assertTrue(dialog.save_token())
        self.assertEqual(listener.token, "synthetic-new-independent-browser-token")

    def test_missing_fresh_metadata_is_allowed_without_creating_a_store_but_corrupt_or_unsafe_metadata_holds(self):
        path = self.root / "visor.sqlite3"
        self.assertFalse(path.exists())
        save_web_access_settings(self.root, "saved_static", "synthetic-browser-token")
        self.assertFalse(path.exists())
        before = (self.root / "web-access.json").read_bytes()
        path.write_text("synthetic invalid SQLite")
        with self.assertRaises(ValueError):
            save_web_access_settings(self.root, "saved_static", "synthetic-browser-new-token")
        self.assertEqual(before, (self.root / "web-access.json").read_bytes())
        path.unlink()
        endpoint = self.root / "broker.json"
        write_private(endpoint, {"token": ["invalid"]})
        with self.assertRaises(ValueError):
            save_web_access_settings(self.root, "saved_static", "synthetic-browser-new-token")
        self.assertEqual(before, (self.root / "web-access.json").read_bytes())
        endpoint.unlink()
        target = self.root / "unrelated-endpoint.json"
        write_private(target, {"token": "synthetic-endpoint-token"})
        endpoint.symlink_to(target)
        with self.assertRaises(ValueError):
            save_web_access_settings(self.root, "saved_static", "synthetic-browser-new-token")
        self.assertEqual(before, (self.root / "web-access.json").read_bytes())

    def test_browser_secret_is_not_a_broker_agent_or_helper_credential_and_recovery_revokes_it(self):
        broker = Broker(self.root)
        actor = broker.store.upsert_session("codex", "synthetic-web-role", "Synthetic role", str(self.root))
        agent = broker.store.issue_credential(actor)
        helper = broker.store.issue_credential(actor, role="helper")
        browser = "synthetic-browser-only-token"
        save_web_access_settings(self.root, "saved_static", browser)
        self.assertIsNone(broker.store.authenticate_principal(browser))
        self.assertNotEqual(browser, broker.token)
        self.assertIsNone(broker.store.get_setting("browser_token"))
        saved = snapshot(self.root)
        restore(self.root, saved)
        self.assertFalse((self.root / "web-access.json").exists())
        self.assertEqual(read_web_access_settings(self.root), {"mode": "per_start", "token": None})
        self.assertIsNone(broker.store.authenticate_principal(agent))
        self.assertIsNone(broker.store.authenticate_principal(helper))
        self.assertIsNone(self.workspace.web_server)
