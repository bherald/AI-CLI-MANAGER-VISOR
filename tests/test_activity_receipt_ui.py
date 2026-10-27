"""Real operator GUI receipt polling against isolated broker authorization."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from visor.activity import ActivityWindow
from visor.broker import Broker


class ReceiptUICase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.broker = Broker(self.root)
        self.owner = self.broker.store.upsert_session("codex", "receipt-ui", "Session", self.temp.name)
        self.document = {"schema_version": 1, "id": "receipt-ui", "title": "Receipt UI", "revision": 1,
                         "cards": [{"id": "q", "title": "Question", "kind": "question", "prompt": "Next step?"}]}
        self.broker.activities.publish(self.owner, self.document, "publish")
        self.transport_fails = False
        self.calls = []
        self.window = ActivityWindow(self.broker.activities.get(None, "receipt-ui")["path"], self.root / "layouts", submit=self.submit)
        self.window.session_names = {self.owner: "Release coordinator"}
        self.window.show()
        self.app.processEvents()
        self.card = self.window.cards["q"]
        self.card.reply.setPlainText("Continue the approved review")
        self.card.save_answer()
        self.event = self.window.last_reply

    def submit(self, operation, params, callback):
        self.calls.append((operation, params))
        if self.transport_fails:
            callback(None, "Temporary transport failure")
            return
        try:
            result = self.broker.dispatch(operation, params, "operator", "operator")
        except (ValueError, OSError) as exc:
            callback(None, str(exc))
        else:
            callback(result, None)

    def tearDown(self):
        self.window.close()
        self.window.watch.stop()
        self.window.receipt_timer.stop()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_saved_reply_becomes_read_using_real_role_boundary(self):
        self.window.refresh_receipts()
        self.assertIn("waiting for acknowledgment", self.card.response_status.text())
        self.broker.dispatch("activity_acknowledge", {"event_id": self.event["id"]}, self.owner, "activity")
        self.window.refresh_receipts()
        self.assertIn("Read by Release coordinator", self.card.response_status.text())
        self.assertNotIn(self.owner, self.card.response_status.text())
        self.assertIn("not task completion", self.card.response_status.text())
        self.assertEqual(self.calls[-1], ("activity_replies", {"board_id": "receipt-ui"}))
        self.assertEqual(self.broker.activities.replies(self.owner)[0]["state"], "read")

    def test_receipt_failure_is_visible_then_recovers_without_resending(self):
        self.transport_fails = True
        self.window.refresh_receipts()
        self.assertIn("Receipt status unavailable", self.card.response_status.text())
        self.transport_fails = False
        self.window.refresh_receipts()
        self.assertIn("waiting for acknowledgment", self.card.response_status.text())
        self.assertEqual(sum(action == "activity_reply" for action, _ in self.calls), 1)
        self.assertEqual(self.card.reply.toPlainText(), "Continue the approved review")
