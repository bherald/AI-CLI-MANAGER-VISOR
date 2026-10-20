import os
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from visor.broker import Broker
from visor.external_windows import _linux_candidates, _mac_call, candidates, focus, validate
from visor.platforms import WindowUnavailable, focus_verified
from visor.store import PolicyError, Store


class ExternalWindowCase(unittest.TestCase):
    def setUp(self):
        self.parent = Path(__file__).resolve().parents[1] / ".cache/external-tests"
        self.root = self.parent / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.broker = Broker(self.root)
        self.store = self.broker.store
        self.key = self.store.upsert_session("codex", "one", "Session one", str(self.root))
        self.other = self.store.upsert_session("claude", "two", "Session two", str(self.root))
        self.identity = {"platform": sys.platform, "pid": 42, "created": 1,
                         "image": "terminal", "handle": "0x01"}
        self.item = {"title": "Chosen terminal", "identity": self.identity}

    def tearDown(self):
        assert self.root.resolve().is_relative_to(self.parent.resolve())
        shutil.rmtree(self.root)

    def link(self):
        with patch("visor.external_windows.validate", return_value=self.item):
            self.broker.dispatch("external_focus_link", {"session": self.key, "identity": self.identity,
                                                        "confirmed": True}, "operator")

    def test_explicit_link_persists_and_does_not_grant_managed_ownership(self):
        self.link()
        store = Store(self.root)
        self.assertEqual(store.external_focus_links()[self.key], self.item)
        self.assertFalse(store.session(self.key)["managed"])
        with patch("visor.external_windows.validate", return_value=self.item):
            target = self.broker.dispatch("focus_target", {"session": self.key}, "operator")
        self.assertEqual(target, {"external": True, "identity": self.identity})
        with self.assertRaisesRegex(PolicyError, "before resuming"):
            self.broker.launch(self.key)
        with self.assertRaisesRegex(PolicyError, "does not establish worker ownership"):
            self.broker.close_for_delete(store.session(self.key))
        with self.assertRaises(PolicyError):
            self.broker.verified_window(self.key)

    def test_confirmation_roles_duplicate_window_and_cleanup(self):
        with self.assertRaises(PolicyError):
            self.broker.dispatch("external_focus_link", {"session": self.key, "identity": self.identity}, "operator")
        for role in ("agent", "helper"):
            with self.assertRaises(PolicyError):
                self.broker.dispatch("external_window_candidates", {}, self.key, role)
        self.link()
        with self.assertRaisesRegex(PolicyError, "already linked"):
            self.store.set_external_focus(self.other, self.item)
        with self.assertRaises(PolicyError):
            self.broker.dispatch("external_focus_unlink", {"session": self.key}, "operator")
        self.broker.dispatch("external_focus_unlink", {"session": self.key, "confirmed": True}, "operator")
        self.assertEqual(self.store.external_focus_links(), {})
        self.link()
        self.store.delete_session(self.key)
        self.assertEqual(self.store.external_focus_links(), {})
        with self.assertRaises(PolicyError):
            self.store.set_external_focus(self.key, self.item)

    def test_live_managed_owner_cannot_be_replaced(self):
        self.store.update_session(self.key, window_token="managed")
        with self.assertRaisesRegex(PolicyError, "managed terminal"):
            self.store.set_external_focus(self.key, self.item)

    def test_native_identity_changes_and_ambiguity_are_rejected(self):
        with patch("visor.external_windows.candidates", return_value=[self.item]):
            self.assertEqual(validate(self.identity), self.item)
            with self.assertRaises(WindowUnavailable):
                validate({**self.identity, "created": 2})
        with patch("visor.external_windows.candidates", return_value=[self.item, self.item]), self.assertRaises(WindowUnavailable):
            validate(self.identity)

    def test_ui_activation_revalidates_external_identity(self):
        with patch("visor.external_windows.focus") as activate:
            focus_verified({"external": True, "identity": self.identity})
        activate.assert_called_once_with(self.identity)

    def test_x11_only_lists_supported_terminal_processes_and_rechecks_birth(self):
        result = subprocess.CompletedProcess([], 0, "0x01 0 42 host Title\n0x02 0 43 host Other\n", "")
        native = {"pid": 42, "created": 1, "image": "/usr/bin/xterm"}
        with patch("visor.external_windows.Linux"), patch("visor.external_windows.run", return_value=result), \
                patch("visor.external_windows.process_identity", side_effect=[native, {"pid": 43, "created": 1, "image": "/usr/bin/editor"}]):
            items = _linux_candidates()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["identity"]["pid"], 42)
        expected = items[0]["identity"]
        with patch("visor.external_windows.sys.platform", "linux"), patch("visor.external_windows.validate"), \
                patch("visor.external_windows.process_identity", return_value={**native, "created": 2}), \
                patch("visor.external_windows.run") as run, self.assertRaises(WindowUnavailable):
            focus(expected)
        run.assert_not_called()

    def test_mac_window_id_is_an_argument_and_rejects_source_injection(self):
        with patch("visor.external_windows.run", return_value=subprocess.CompletedProcess([], 0, "Title", "")) as run:
            self.assertEqual(_mac_call("42", "title"), "Title")
            self.assertEqual(run.call_args.args[0][-1], "42")
        with self.assertRaises(WindowUnavailable):
            _mac_call('42" & do shell script', "focus")

    @unittest.skipUnless(os.name == "nt", "Read-only native Windows Terminal inventory")
    def test_native_candidate_identity_roundtrip_without_focusing_any_window(self):
        items = candidates()
        for item in items:
            self.assertEqual(item["identity"]["platform"], "win32")
            self.assertGreater(item["identity"]["created"], 0)
            self.assertIn(Path(item["identity"]["image"]).name, ("windowsterminal.exe", "windowsterminalpreview.exe"))
