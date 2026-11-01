"""Real loopback RPC boundaries; synthetic data, no broker delivery worker/providers."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from visor.broker import Broker, Client, LoopbackHTTPServer, handler_for
from visor.paths import write_private
from visor.store import PolicyError


class NativeDraftRPCTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.broker = Broker(self.root)
        self.agent = self.broker.store.upsert_session("codex", str(uuid.uuid4()), "Synthetic sender", self.tmp.name)
        self.target = self.broker.store.upsert_session("claude", str(uuid.uuid4()), "Synthetic target", self.tmp.name)
        self.broker.store.pair(self.agent, self.target, True)
        self.server = LoopbackHTTPServer(("127.0.0.1", 0), handler_for(self.broker))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        write_private(self.root / "broker.json", {"port": self.server.server_port, "token": self.broker.token})
        self.client = Client(self.root)
        self.tokens = {role: self.broker.store.issue_credential(self.agent, role) for role in ("agent", "helper", "activity")}
        self.pending = {"recipient": self.target, "body": "\U0001f680" * 16000, "request_id": str(uuid.uuid4())}
        self.draft = {"text": self.pending["body"], "recipient": self.target, "pending": self.pending}

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_max_unicode_pending_round_trips_and_exact_send_deduplicates(self):
        self.assertEqual(self.client.call("message_draft", value=self.draft), self.draft)
        result = self.client.call("send", **self.pending)
        self.assertEqual(result["body"], self.pending["body"])
        self.assertEqual(self.client.call("send", **self.pending)["id"], result["id"])
        self.assertEqual(self.client.call("message_draft"), self.draft)
        self.assertEqual(len(self.broker.store.messages()), 1)

    def test_large_unicode_send_preserves_agent_role_and_draft_is_operator_only(self):
        result = Client(self.root, self.tokens["agent"]).call("send", **self.pending)
        self.assertEqual(result["sender"], self.agent)
        for role in ("agent", "helper", "activity"):
            with self.subTest(role=role), self.assertRaises(PolicyError):
                Client(self.root, self.tokens[role]).call("message_draft", value=self.draft)
        for role in ("helper", "activity"):
            with self.subTest(role=role), self.assertRaises(PolicyError):
                Client(self.root, self.tokens[role]).call("send", **self.pending)
        self.assertIsNone(self.broker.store.get_setting("message_draft"))
        self.assertEqual(len(self.broker.store.messages()), 1)

    def test_large_requests_require_exact_authorized_header_action_and_auth(self):
        cases = [("message_draft", "send", self.broker.token),
                 ("send", "message_draft", self.broker.token),
                 ("message_draft", "", self.broker.token),
                 ("ping", "message_draft", self.broker.token),
                 ("message_draft", "message_draft", "invalid")]
        for action, hint, token in cases:
            with self.subTest(action=action, hint=hint, authenticated=token != "invalid"):
                params = self.pending if action == "send" else {"value": self.draft}
                request = urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}/rpc",
                    data=json.dumps({"action": action, "params": params}).encode(),
                    headers={"Authorization": "Bearer " + token, "X-Visor-Action": hint})
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    opener.open(request, timeout=5)
                self.assertEqual(rejected.exception.code, 400)
                rejected.exception.close()
        self.assertIsNone(self.broker.store.get_setting("message_draft"))
        self.assertEqual(self.broker.store.messages(), [])

    def test_large_admission_retains_typed_body_limits_and_old_draft_on_failure(self):
        self.client.call("message_draft", value=self.draft)
        for body in ("\U0001f680" * 16001, "\ud800" * 16000):
            pending = {**self.pending, "body": body}
            with self.subTest(kind="oversize" if len(body) > 16000 else "invalid UTF8"):
                with self.assertRaises(PolicyError):
                    self.client.call("message_draft", value={**self.draft, "text": body, "pending": pending})
                with self.assertRaises(PolicyError):
                    self.client.call("send", **pending)
                self.assertEqual(self.client.call("message_draft"), self.draft)
        self.assertEqual(self.broker.store.messages(), [])
