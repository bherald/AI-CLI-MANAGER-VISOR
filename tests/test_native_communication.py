import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu, QPlainTextEdit, QPushButton

from visor.broker import Broker
from visor.store import PolicyError
from visor.ui import STYLE, Window


class NativeCommunicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.broker = Broker(Path(self.tmp.name))
        self.target = self.broker.store.upsert_session("codex", str(uuid.uuid4()), "Fixture recipient", self.tmp.name)
        self.calls = []
        self.drop_send = False
        self.drop_draft = False
        self.bad_receipt = None
        case = self

        class Client:
            def call(self, action, **params):
                case.calls.append((action, params))
                result = case.broker.dispatch(action, params, "operator", "operator")
                if action == "send" and case.drop_send:
                    case.drop_send = False
                    raise OSError("Accepted response was lost")
                if action == "message_draft" and "value" in params and case.drop_draft:
                    case.drop_draft = False
                    raise OSError("Draft save response was lost")
                if action == "send" and case.bad_receipt:
                    result = {**result, **case.bad_receipt}
                return result

        self.client = Client()
        self.window = Window(Path(self.tmp.name))
        self.window.timer.stop()
        self.window.draft_timer.stop()
        self.window.client = self.client
        self.window.poll = Mock()
        self.window.job = self.immediate
        self.window.snapshot["sessions"] = [{"id": self.target, "name": "Fixture recipient"}]
        self.window.recipient.addItem("Fixture recipient", self.target)
        self.window.load_draft()
        self.warnings = patch("visor.ui.QMessageBox.warning").start()
        self.addCleanup(patch.stopall)

    @staticmethod
    def immediate(function, callback):
        try:
            result, error = function(), None
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result, error = None, str(exc)
        callback(result, error)

    def tearDown(self):
        self.window.draft_timer.stop()
        self.window.allow_close = True
        self.window.close()
        self.window.pool.waitForDone(1000)
        self.window.deleteLater()
        self.app.processEvents()

    def test_lost_accepted_send_retries_the_same_request_without_a_second_message(self):
        self.window.composer.setText("A bounded operator request")
        self.drop_send = True
        self.window.send()
        self.assertEqual(len(self.broker.store.messages()), 1)
        self.window.send()
        self.assertEqual(len(self.broker.store.messages()), 1, "Retry after a lost response must reuse the accepted request ID")
        sends = [params for action, params in self.calls if action == "send"]
        self.assertEqual(sends[0], sends[1])

    def test_pending_save_unknown_never_dispatches_until_retried(self):
        self.window.composer.setText("  Original whitespace stays in the display  ")
        self.drop_draft = True
        self.window.send()
        self.assertEqual(self.broker.store.messages(), [])
        pending = dict(self.window.pending_send)
        self.assertEqual(self.broker.store.get_setting("message_draft")["pending"], pending)
        self.assertEqual(pending["body"], "Original whitespace stays in the display")
        self.assertTrue(self.window.composer.isReadOnly())
        self.assertFalse(self.window.recipient.isEnabled())
        self.window.select_recipient("codex:another-session")
        self.assertEqual(self.window.recipient.currentData(), self.target)
        self.window.send()
        self.assertEqual(len(self.broker.store.messages()), 1)
        self.assertEqual(self.broker.store.messages()[0]["dedupe"], pending["request_id"])
        self.assertIsNone(self.window.pending_send)
        self.assertEqual(self.broker.store.get_setting("message_draft")["text"], "")

    def test_pending_survives_close_and_restart_then_deduplicates(self):
        self.window.composer.setText("Restart preserves this exact send")
        self.drop_send = True
        self.window.send()
        pending = dict(self.window.pending_send)
        self.window.save_and_close()
        self.assertTrue(self.window.closed)
        self.assertEqual(self.broker.store.get_setting("message_draft")["pending"], pending)
        restored = Window(Path(self.tmp.name))
        restored.timer.stop()
        restored.client = self.client
        restored.job = self.immediate
        restored.poll = Mock()
        restored.snapshot = self.window.snapshot
        restored.draft_loading = True
        restored.recipient.addItem("Fixture recipient", self.target)
        restored.draft_loading = False
        try:
            restored.load_draft()
            self.assertEqual(restored.pending_send, pending)
            self.assertEqual(restored.composer.text(), "Restart preserves this exact send")
            restored.send()
            self.assertEqual(len(self.broker.store.messages()), 1)
            self.assertIsNone(restored.pending_send)
        finally:
            restored.draft_timer.stop()
            restored.allow_close = True
            restored.close()
            restored.deleteLater()

    def test_valid_non_bmp_pending_body_restores_without_qt_truncation(self):
        body = "\U0001f680" * 16000
        pending = {"recipient": self.target, "body": body, "request_id": str(uuid.uuid4())}
        saved = {"text": body, "recipient": self.target, "pending": pending}
        self.client.call("message_draft", value=saved)
        self.window.draft_dirty = False
        self.window.load_draft()
        self.assertEqual(self.window.composer.text(), body)
        self.window.send()
        self.assertEqual(self.broker.store.messages()[0]["body"], body)

    def test_oversized_outgoing_text_stays_editable_and_is_not_sent(self):
        self.window.composer.setText("x" * 16001)
        self.window.send()
        self.assertIsNone(self.window.pending_send)
        self.assertFalse(self.window.composer.isReadOnly())
        self.assertEqual(self.window.composer.text(), "x" * 16001)
        self.assertEqual(self.broker.store.messages(), [])

    def test_save_rejection_leaves_pending_and_does_not_send(self):
        self.window.composer.setText("Save must precede send")
        with patch.object(self.broker.store, "set_setting", side_effect=OSError("Disk unavailable")):
            self.window.send()
        self.assertEqual(self.broker.store.messages(), [])
        self.assertIsNotNone(self.window.pending_send)
        self.assertTrue(self.window.draft_dirty)

    def test_lost_clear_receipt_does_not_claim_the_accepted_send_was_not_attempted(self):
        self.window.composer.setText("Accepted before draft cleanup")
        call = self.client.call
        def lose_clear(action, **params):
            result = call(action, **params)
            if action == "message_draft" and params.get("value", {}).get("text") == "":
                raise OSError("Clear response lost")
            return result
        with patch.object(self.client, "call", side_effect=lose_clear):
            self.window.send()
        self.assertEqual(len(self.broker.store.messages()), 1)
        self.assertIsNone(self.window.pending_send)
        self.assertTrue(self.window.draft_dirty)
        self.assertNotIn("did not send", self.window.send_notice.text())

    def test_each_mismatched_receipt_keeps_pending_for_same_request_retry(self):
        wrong = {"id": "not-a-uuid", "sender": "other", "origin": "agent", "kind": "wrap_up",
                 "recipient": "codex:other", "body": "different", "dedupe": str(uuid.uuid4())}
        for key, value in wrong.items():
            with self.subTest(field=key):
                self.window.composer.setText("Receipt checks " + key)
                self.bad_receipt = {key: value}
                self.window.send()
                pending = dict(self.window.pending_send)
                self.assertEqual(self.window.composer.text(), "Receipt checks " + key)
                self.bad_receipt = None
                self.window.send()
                self.assertIsNone(self.window.pending_send)
                self.assertEqual(sum(row["dedupe"] == pending["request_id"] for row in self.broker.store.messages()), 1)

    def test_slow_autosave_is_serialized_before_persisting_and_sending_pending(self):
        jobs = []
        self.window.job = lambda function, callback: jobs.append((function, callback))
        self.window.composer.setText("Earlier draft")
        self.window.save_draft()
        self.window.composer.setText("Final requested text")
        self.window.send()
        self.window.send()  # Double click while save is in flight is inert.
        self.assertEqual(len(jobs), 1)
        while jobs:
            function, callback = jobs.pop(0)
            callback(function(), None)
        self.assertEqual([m["body"] for m in self.broker.store.messages()], ["Final requested text"])
        sent = [params for action, params in self.calls if action == "send"]
        self.assertEqual(len(sent), 1)
        prior = [params["value"] for action, params in self.calls if action == "message_draft" and "value" in params]
        self.assertEqual(prior[-2]["pending"], sent[0])
        self.assertEqual(prior[-1]["text"], "")

    def test_malformed_stored_pending_fails_closed_without_overwriting_either_draft(self):
        original = {"text": "Saved original", "recipient": self.target,
                    "pending": {"request_id": "invalid", "recipient": self.target, "body": "Saved original"}}
        self.broker.store.set_setting("message_draft", original)
        self.window.composer.setText("Local text also preserved")
        self.window.load_draft()
        self.window.send()
        self.window.save_draft()
        self.window.save_and_close()
        self.assertFalse(self.window.closed)
        self.assertTrue(self.window.draft_invalid)
        self.assertEqual(self.window.composer.text(), "Local text also preserved")
        self.assertEqual(self.broker.store.get_setting("message_draft"), original)
        self.assertEqual(self.broker.store.messages(), [])

    def test_close_before_loading_saved_draft_preserves_it(self):
        original = {"text": "Unloaded draft", "recipient": self.target}
        self.broker.store.set_setting("message_draft", original)
        self.window.draft_ready = False
        self.window.save_draft()
        self.window.save_and_close()
        self.assertTrue(self.window.closed)
        self.assertEqual(self.broker.store.get_setting("message_draft"), original)

    def test_typed_draft_rejects_malformed_pending_and_retains_last_valid_value(self):
        request = str(uuid.uuid4())
        valid = {"text": "  Exact text  ", "recipient": self.target,
                 "pending": {"request_id": request, "recipient": self.target, "body": "Exact text"}}
        self.assertEqual(self.client.call("message_draft", value=valid), valid)
        bad = [{"pending": []}, {"pending": {"request_id": request}},
               {"pending": {**valid["pending"], "request_id": request.replace("-", "")}},
               {"pending": {**valid["pending"], "request_id": str(uuid.uuid1())}},
               {"pending": {**valid["pending"], "recipient": ""}},
               {"pending": {**valid["pending"], "recipient": "codex:another"}},
               {"pending": {**valid["pending"], "body": "Different"}},
               {"pending": {**valid["pending"], "body": " Exact text "}},
               {"text": "\ud800", "pending": {**valid["pending"], "body": "\ud800"}},
               {"text": "x" * 16001, "pending": {**valid["pending"], "body": "x" * 16001}},
               {"text": "", "pending": {**valid["pending"], "body": ""}}]
        for changed in bad:
            with self.subTest(changed=repr(changed)[:100]):
                with self.assertRaises(PolicyError):
                    self.client.call("message_draft", value={**valid, **changed})
                self.assertEqual(self.client.call("message_draft"), valid)
        legacy = {"text": "Legacy draft remains readable", "recipient": None}
        self.assertEqual(self.client.call("message_draft", value=legacy), legacy)

    def test_native_message_view_reads_the_exact_recipient_reply(self):
        parent = self.broker.store.enqueue("operator", self.target, "Original operator question", str(uuid.uuid4()), operator=True)
        self.broker.store.claim(self.target)
        reply = self.broker.store.operator_reply(parent["id"], "Full final reply from the original recipient", str(uuid.uuid4()), identity=self.target, role="agent")
        self.window.snapshot["messages"] = self.broker.store.messages()
        self.window.render_messages()
        captured = []
        class ImmediateMenu(QMenu):
            def exec(self, *args):
                self.actions()[0].trigger()
        with patch("visor.ui.QMessageBox.information", side_effect=lambda *args: captured.append(args[2])), \
                patch("visor.ui.QMenu", ImmediateMenu):
            self.window.message_menu(self.window.messages.visualItemRect(self.window.messages.item(0, 0)).center())
        contents = captured + [widget.toPlainText() for widget in self.window.findChildren(QPlainTextEdit)]
        self.assertIn(reply["body"], "\n".join(contents))
        self.assertIn(("message_get", {"message": parent["id"]}), self.calls)
        self.assertEqual(self.broker.store.message(parent["id"])["state"], "sending", "Viewing does not acknowledge or complete work")

    def test_receipt_lookup_refresh_is_exact_and_followup_does_not_send_or_change_other_draft(self):
        parent = self.broker.store.enqueue("operator", self.target, "<b>Literal original</b>", str(uuid.uuid4()), operator=True)
        self.broker.store.claim(self.target)
        self.window.snapshot["messages"] = self.broker.store.messages()
        dialog = self.window.inspect_message(parent["id"])
        contents = dialog.findChild(QPlainTextEdit)
        self.assertIn("<b>Literal original</b>", contents.toPlainText())
        self.assertIn("No reply recorded", contents.toPlainText())
        reply = self.broker.store.operator_reply(parent["id"], "<script>Literal reply</script>", str(uuid.uuid4()), identity=self.target, role="agent")
        next(b for b in dialog.findChildren(QPushButton) if b.text() == "Refresh receipt").click()
        self.assertIn(reply["body"], contents.toPlainText())
        follow = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Compose follow-up")
        self.window.recipient.addItem("Other", "codex:other")
        self.window.recipient.setCurrentIndex(1)
        self.window.composer.setText("Existing unrelated draft")
        follow.click()
        self.assertEqual(self.window.recipient.currentData(), "codex:other")
        self.assertEqual(self.window.composer.text(), "Existing unrelated draft")
        self.window.composer.clear()
        follow.click()
        self.assertEqual(self.window.recipient.currentData(), self.target)
        self.assertEqual(len(self.broker.store.messages()), 1)

    def test_late_receipt_refresh_cannot_replace_newer_reply_or_closed_dialog(self):
        parent = self.broker.store.enqueue("operator", self.target, "Exact message", str(uuid.uuid4()), operator=True)
        self.window.snapshot["messages"] = self.broker.store.messages()
        jobs = []
        self.window.job = lambda function, callback: jobs.append((function, callback))
        dialog = self.window.inspect_message(parent["id"])
        contents = dialog.findChild(QPlainTextEdit)
        next(b for b in dialog.findChildren(QPushButton) if b.text() == "Refresh receipt").click()
        function, callback = jobs[1]
        callback(function(), None)
        rendered = contents.toPlainText()
        jobs[0][1](None, "Stale response")
        self.assertEqual(contents.toPlainText(), rendered)
        dialog.reject()
        callback(None, "Late after close")
        self.assertEqual(contents.toPlainText(), rendered)

    def test_changed_parent_or_deleted_message_does_not_show_stale_reply(self):
        parent = self.broker.store.enqueue("operator", self.target, "Exact message", str(uuid.uuid4()), operator=True)
        self.window.snapshot["messages"] = self.broker.store.messages()
        with patch.object(self.client, "call", return_value={**parent, "body": "Changed", "reply": None}):
            dialog = self.window.inspect_message(parent["id"])
        self.assertIn("Original message content or recipient changed", dialog.findChild(QPlainTextEdit).toPlainText())
        self.assertFalse(next(b for b in dialog.findChildren(QPushButton) if b.text() == "Compose follow-up").isEnabled())
        self.broker.store.delete_messages([parent["id"]])
        next(b for b in dialog.findChildren(QPushButton) if b.text() == "Refresh receipt").click()
        self.assertIn("missing, deleted or cleared", dialog.findChild(QPlainTextEdit).toPlainText())

    def test_attention_includes_failed_held_and_unknown_without_ordinary_waiting(self):
        self.window.snapshot["messages"] = [
            {"id": state, "sender": "operator", "recipient": self.target, "created": 0,
             "body": state, "detail": "", "state": state}
            for state in ("queued", "sending", "delivered", "acknowledged", "held", "failed", "delivery_unknown", "completed")]
        self.window.message_filter.setCurrentIndex(1)
        self.window.render_messages()
        shown = {self.window.messages.item(row, 0).data(Qt.ItemDataRole.UserRole) for row in range(self.window.messages.rowCount())}
        self.assertEqual(shown, {"held", "failed", "delivery_unknown"})
