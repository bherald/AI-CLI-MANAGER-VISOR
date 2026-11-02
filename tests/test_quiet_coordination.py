"""Routine inbox traffic must not become provider prompts or false wake receipts."""
import json
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from visor.broker import Broker, Client, LoopbackHTTPServer, handler_for
from visor.mcp import channel_loop, handle
from visor.paths import write_private
from visor.store import PolicyError


class QuietCoordinationCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.broker = Broker(self.root)
        self.a, self.b = [self.broker.store.upsert_session("codex", str(uuid.uuid4()), name, self.folder.name)
                          for name in ("Sender fixture", "Recipient fixture")]
        for key in (self.a, self.b):
            self.broker.store.participate(key, True)
            self.broker.store.update_session(key, status="idle")
        self.broker.store.pair(self.a, self.b)
        self.broker.store.toggle(True)

    def send(self, request="routine", **options):
        return self.broker.dispatch("send", {"recipient": self.b, "body": "Exact fixture handoff José 尾",
                                            "request_id": request, **options}, self.a, role="agent")

    def test_default_inbox_survives_restart_and_read_ack_without_native_prompt(self):
        message = self.send()
        self.assertEqual(message["kind"], "coordination")
        with patch("visor.broker.deliver_codex") as native:
            self.broker.deliver_session(self.b)
            self.assertEqual(self.broker.store.queued_recipients(prompt_only=True), [])
            self.assertEqual(self.broker.store.message(message["id"])["state"], "queued")
            self.broker = Broker(self.root)
            self.broker.store.recover()
            self.assertIsNone(self.broker.dispatch("receive", {}, self.b, role="agent"))
            received = self.broker.dispatch("receive", {"prompt_only": False}, self.b, role="agent")
            self.assertEqual(received["id"], message["id"])
            self.assertEqual(received["body"], message["body"])
            self.broker.dispatch("transport_received", {"message": message["id"]}, self.b, role="agent")
            self.assertEqual(self.broker.store.message(message["id"])["state"], "delivered")
            self.broker.dispatch("acknowledge", {"message": message["id"]}, self.b, role="agent")
            self.assertEqual(self.broker.store.message(message["id"])["state"], "acknowledged")
            self.assertEqual(self.broker.store.receipt_messages(self.b), [])
            self.assertIsNone(self.broker.dispatch("receive", {"prompt_only": False}, self.b, role="agent"))
            native.assert_not_called()

    def test_operator_and_explicit_attention_bypass_quiet_backlog(self):
        quiet = self.send()
        operator = self.broker.dispatch("send", {"recipient": self.b, "body": "Human request", "request_id": "op"}, "operator")
        attention = self.send("attention", delivery="prompt")
        self.assertEqual(self.broker.store.queued_recipients(prompt_only=True), [self.b])
        with patch("visor.broker.deliver_codex", return_value=("delivered", "Synthetic provider receipt")) as native:
            self.broker.deliver_session(self.b)
            self.broker.deliver_session(self.b)
            self.broker.deliver_session(self.b)
        self.assertEqual([call.args[1]["id"] for call in native.call_args_list], [operator["id"], attention["id"]])
        self.assertEqual(self.broker.store.message(quiet["id"])["state"], "queued")
        self.assertEqual(operator["origin"], "operator")
        self.assertEqual(attention["origin"], "agent")

    def test_exact_retry_and_legacy_retry_preserve_identity_and_delivery(self):
        message = self.send()
        self.assertEqual(self.send()["id"], message["id"])
        self.assertEqual(self.send(delivery="inbox")["id"], message["id"])
        with self.assertRaises(PolicyError):
            self.send(delivery="prompt")
        legacy = self.broker.store.enqueue(self.a, self.b, "Exact fixture handoff José 尾", "legacy")
        self.assertEqual(self.send("legacy")["id"], legacy["id"])
        self.assertEqual(self.send("legacy")["kind"], "message")
        with self.assertRaises(PolicyError):
            self.send("legacy", delivery="inbox")

    def test_quiet_keeps_pair_role_hold_and_request_validation(self):
        for delivery in [None, "silent", 1, {}, []]:
            with self.subTest(delivery=delivery), self.assertRaises(PolicyError):
                self.send(delivery=delivery)
        for role in ["helper", "activity"]:
            with self.subTest(role=role), self.assertRaises(PolicyError):
                self.broker.dispatch("send", {"recipient": self.b, "body": "No permission", "request_id": role}, self.a, role=role)
        self.broker.store.toggle(False)
        held = self.send()
        self.assertEqual(held["state"], "held")
        self.assertIsNone(self.broker.dispatch("receive", {"prompt_only": False}, self.b))
        self.broker.store.toggle(True)
        self.assertIsNone(self.broker.dispatch("receive", {"prompt_only": False}, self.b))
        self.broker.store.release(held["id"])
        self.assertEqual(self.broker.dispatch("receive", {"prompt_only": False}, self.b)["id"], held["id"])
        self.broker.store.pair(self.a, self.b, False)
        with self.assertRaises(PolicyError):
            self.send("unpaired")

    def test_unknown_pull_is_not_replayed_or_reclassified(self):
        message = self.send()
        self.broker.dispatch("receive", {"prompt_only": False}, self.b)
        self.broker.store.recover()
        self.assertEqual(self.broker.store.message(message["id"])["state"], "delivery_unknown")
        self.assertEqual(self.send()["state"], "delivery_unknown")
        self.assertIsNone(self.broker.dispatch("receive", {"prompt_only": False}, self.b))
        with self.assertRaises(PolicyError):
            self.broker.store.release(message["id"])
        self.broker.dispatch("acknowledge", {"message": message["id"]}, self.b)
        self.broker.dispatch("transport_received", {"message": message["id"]}, self.b)
        self.assertEqual(self.broker.store.message(message["id"])["state"], "acknowledged")

    def test_visible_channel_does_not_push_quiet_coordination(self):
        quiet = self.send()
        attention = self.send("attention", delivery="prompt")
        broker, key = self.broker, self.b
        class ScopedClient:
            def call(self, action, **params):
                return broker.dispatch(action, params, key, role="agent")
        stopped, events = threading.Event(), []
        def emit(event):
            events.append(event)
            stopped.set()
        channel_loop(ScopedClient(), emit, stopped)
        self.assertEqual([event["params"]["meta"]["message_id"] for event in events], [attention["id"]])
        self.assertEqual(self.broker.store.message(quiet["id"])["state"], "queued")
        self.assertEqual(self.broker.store.message(attention["id"])["state"], "delivery_unknown")

    def test_mcp_contract_exposes_inbox_default_and_explicit_attention(self):
        tools = handle({"method": "tools/list"}, None)["tools"]
        send = next(tool for tool in tools if tool["name"] == "send")
        self.assertEqual(send["inputSchema"]["properties"]["delivery"]["enum"], ["inbox", "prompt"])
        self.assertIn("does not wake an idle worker", send["description"])
        capability = self.broker.dispatch("ping", {}, self.a, role="agent")
        self.assertEqual(capability["capabilities"], ["quiet_agent_inbox"])
        self.assertEqual(capability["default_agent_delivery"], "inbox")
        self.assertFalse(capability["idle_wake"])
        broker, key = self.broker, self.b
        class ScopedClient:
            def call(self, action, **params):
                return broker.dispatch(action, params, key, role="agent")
        message = self.send()
        result = handle({"method": "tools/call", "params": {"name": "receive"}}, ScopedClient())
        self.assertEqual(json.loads(result["content"][0]["text"])["id"], message["id"])

    def test_actual_loopback_scoped_delivery_and_ack_has_zero_native_events(self):
        server = LoopbackHTTPServer(("127.0.0.1", 0), handler_for(self.broker))
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
        worker.start()
        try:
            write_private(self.root / "broker.json", {"port": server.server_address[1], "token": self.broker.token})
            sender = Client(self.root, self.broker.store.issue_credential(self.a))
            recipient = Client(self.root, self.broker.store.issue_credential(self.b))
            self.assertIn("quiet_agent_inbox", sender.call("ping")["capabilities"])
            started = time.perf_counter()
            with patch("visor.broker.deliver_codex") as native:
                message = sender.call("send", recipient=self.b, body="Synthetic quiet acceptance", request_id="loopback")
                self.broker.deliver_session(self.b)
                self.assertIsNone(recipient.call("receive"))  # Existing old channel polls are safe.
                received = recipient.call("receive", prompt_only=False)
                self.assertEqual(received["id"], message["id"])
                recipient.call("transport_received", message=message["id"])
                recipient.call("acknowledge", message=message["id"])
                self.assertEqual(self.broker.store.message(message["id"])["state"], "acknowledged")
                native.assert_not_called()
                print("QUIET_TRANSPORT_METRICS " + json.dumps({"scope": "disposable authenticated loopback broker",
                    "send_receive_ack_ms": round((time.perf_counter() - started) * 1000, 3),
                    "durable_messages": 1, "read_acknowledgments": 1, "native_prompt_calls": native.call_count,
                    "native_event_emissions": 0, "live_main_thread_count_measured": False}))
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)
        self.assertFalse(worker.is_alive())
