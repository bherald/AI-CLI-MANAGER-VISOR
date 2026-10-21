import json
import os
import shutil
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

from visor.broker import Broker, BrokerLock, LoopbackHTTPServer
from visor.layout import Rect, arrange
from visor.mcp import handle
from visor.platforms import WindowNotFound, WindowUnavailable
from visor.providers import codex_status, session_command
from visor.store import PolicyError, Store


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parents[1] / ".cache" / "tests"
        self.test_root.mkdir(parents=True, exist_ok=True)
        self.root = self.test_root / uuid.uuid4().hex
        self.root.mkdir()
        self.store = Store(self.root)
        self.a = self.store.upsert_session("codex", "a", "A", str(self.root))
        self.b = self.store.upsert_session("claude", "b", "B", str(self.root))
        self.c = self.store.upsert_session("codex", "c", "C", str(self.root))
        for key in (self.a, self.b, self.c):
            self.store.participate(key, True)
        self.store.pair(self.a, self.b)
        self.store.pair(self.a, self.c)
        self.store.toggle(True)

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(self.test_root.resolve()))
        shutil.rmtree(self.root)

    def send(self, **kwargs):
        return self.store.enqueue(self.a, self.b, "Hello", "once", **kwargs)

    def test_last_used_tracks_activity_and_never_refresh_or_stale_metadata(self):
        self.assertIsNone(self.store.session(self.a)["last_used"])
        self.store.upsert_session("codex", "a", "A", str(self.root), updated=100)
        self.assertEqual(self.store.session(self.a)["last_used"], 100)
        self.store.upsert_session("codex", "a", "A", str(self.root))
        self.store.upsert_session("codex", "a", "A", str(self.root), updated=50)
        self.assertEqual(self.store.session(self.a)["last_used"], 100)
        self.store.update_session(self.a, last_used=200)
        self.store.upsert_session("codex", "a", "A", str(self.root), updated=150)
        self.assertEqual(Store(self.root).session(self.a)["last_used"], 200)

    def test_provider_qualified_identity(self):
        other = self.store.upsert_session("claude", "a", "Other", str(self.root))
        self.assertNotEqual(self.a, other)
        self.assertEqual(len(self.store.sessions()), 4)

    def test_multiple_pairs_are_not_transitive(self):
        self.send()
        self.store.enqueue(self.a, self.c, "Hello C", "two")
        with self.assertRaises(PolicyError):
            self.store.enqueue(self.b, self.c, "Not approved", "three")

    def test_toggle_does_not_flush_held(self):
        message = self.send()
        self.store.toggle(False)
        self.store.toggle(True)
        self.assertIsNone(self.store.claim())
        self.store.release(message["id"])
        self.assertEqual(self.store.claim()["id"], message["id"])

    def test_removing_pair_holds_pending(self):
        self.send()
        self.store.pair(self.a, self.b, False)
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.messages()[0]["state"], "held")

    def test_participation_holds_both_directions(self):
        self.send()
        self.store.enqueue(self.b, self.a, "Reply", "reply")
        self.store.participate(self.b, False)
        self.assertTrue(all(m["state"] == "held" for m in self.store.messages()))

    def test_operator_independent_of_agent_policy(self):
        self.store.toggle(False)
        self.store.participate(self.b, False)
        self.store.pair(self.a, self.b, False)
        message = self.store.enqueue("ignored", self.b, "Operator", "op", operator=True)
        self.assertEqual(self.store.claim()["id"], message["id"])
        self.assertEqual(message["sender"], "operator")

    def test_deduplication_and_conflicting_id(self):
        first = self.send()
        self.assertEqual(self.send()["id"], first["id"])
        with self.assertRaises(PolicyError):
            self.store.enqueue(self.a, self.b, "Changed", "once")

    def test_concurrent_delivery_only_claimed_once(self):
        self.send()
        with ThreadPoolExecutor(max_workers=8) as executor:
            result = list(executor.map(lambda _: self.store.claim(), range(8)))
        self.assertEqual(sum(r is not None for r in result), 1)

    def test_crash_during_delivery_is_unknown_and_not_retryable(self):
        message = self.send()
        self.store.claim()
        Store(self.root).recover()
        self.assertEqual(self.store.messages()[0]["state"], "delivery_unknown")
        with self.assertRaises(PolicyError):
            self.store.release(message["id"])
        self.assertIsNone(self.store.claim())

    def test_delivery_is_not_acknowledgment(self):
        message = self.send()
        self.store.claim()
        self.store.delivery_result(message["id"], "delivered")
        self.assertEqual(self.store.messages()[0]["state"], "delivered")
        with self.assertRaises(PolicyError):
            self.store.acknowledge(self.c, message["id"])
        self.store.acknowledge(self.b, message["id"])
        self.assertEqual(self.store.messages()[0]["state"], "acknowledged")

    def test_completed_never_regresses(self):
        message = self.send()
        self.store.claim()
        self.store.delivery_result(message["id"], "delivered")
        self.store.acknowledge(self.b, message["id"], completed=True)
        self.store.acknowledge(self.b, message["id"], completed=False)
        self.assertEqual(self.store.messages()[0]["state"], "completed")

    def test_credentials_bound_and_rotation_revokes(self):
        token = self.store.issue_credential(self.a)
        self.assertEqual(self.store.authenticate(token), self.a)
        self.store.issue_credential(self.a)
        self.assertIsNone(self.store.authenticate(token))

    def test_agent_cannot_call_operator_controls(self):
        broker = Broker(self.root)
        for action in ("toggle", "new", "pair", "stop", "wrap_up", "register"):
            with self.subTest(action=action), self.assertRaises(PolicyError):
                broker.dispatch(action, {}, self.a)

    def test_agent_cannot_spoof_sender(self):
        broker = Broker(self.root)
        message = broker.dispatch("send", {"sender": self.c, "operator": True,
                                           "recipient": self.b, "body": "Hello", "request_id": "q"}, self.a)
        self.assertEqual(message["sender"], self.a)
        self.assertEqual(message["origin"], "agent")

    def test_rate_limit(self):
        for i in range(12):
            self.store.enqueue(self.a, self.b, "hi", str(i))
        with self.assertRaises(PolicyError):
            self.store.enqueue(self.a, self.b, "hi", "over")

    def test_feedback_loop_hold_requires_explicit_operator_release(self):
        for index in range(3):
            self.store.enqueue(self.a, self.b, "Repeated reply", str(index))
        held = self.store.enqueue(self.a, self.b, "Repeated reply", "four")
        self.assertEqual(held["state"], "held")
        self.assertIn("loop hold", held["detail"])
        self.store.toggle(False)
        self.store.toggle(True)
        self.assertEqual(self.store.message(held["id"])["state"], "held")
        self.store.release(held["id"])
        self.assertEqual(self.store.message(held["id"])["state"], "queued")
        operator = self.store.enqueue("operator", self.b, "Repeated reply", "operator", operator=True)
        self.assertEqual(operator["state"], "queued")

    def test_pair_budget_holds_varying_messages_after_rate_window(self):
        for index in range(24):
            sender, recipient = (self.a, self.b) if index % 2 else (self.b, self.a)
            self.store.enqueue(sender, recipient, f"Work {index}", str(index))
        with self.store.connect(True) as db:
            db.execute("UPDATE messages SET created=?", (time.time() - 61,))
        held = self.store.enqueue(self.a, self.b, "Another reply", "25")
        self.assertEqual(held["state"], "held")

    def test_delete_messages_in_every_state_prevents_replay(self):
        for state in ("queued", "held", "sending", "delivered", "delivery_unknown",
                      "acknowledged", "completed", "failed"):
            with self.subTest(state=state):
                message = self.store.enqueue("operator", self.b, "Private text", state, operator=True)
                with self.store.connect(True) as db:
                    db.execute("UPDATE messages SET state=? WHERE id=?", (state, message["id"]))
                self.assertEqual(self.store.delete_messages([message["id"]]), 1)
                self.store.delivery_result(message["id"], "delivered")
                self.store.transport_received(self.b, message["id"])
                with self.assertRaises(PolicyError):
                    self.store.acknowledge(self.b, message["id"], completed=True)
                with self.assertRaises(PolicyError):
                    self.store.enqueue("operator", self.b, "Private text", state, operator=True)
                self.assertEqual(self.store.message(message["id"])["state"], "deleted")
                self.assertEqual(self.store.message(message["id"])["body"], "[deleted]")
                self.assertEqual(self.store.delete_messages([message["id"]]), 0)
        self.assertEqual(self.store.messages(visible=True), [])
        self.assertEqual(self.store.receipt_messages(self.b), [])
        self.assertIsNone(self.store.claim())
        self.store.enqueue("operator", self.b, "New text", "new", operator=True)
        self.assertIsNotNone(self.store.claim())

    def test_delete_does_not_wait_for_slow_provider_delivery(self):
        message = self.send()
        broker = Broker(self.root)
        with ThreadPoolExecutor(max_workers=1) as pool, broker.delivery_lock:
            deleted = pool.submit(broker.dispatch, "delete_messages",
                                  {"messages": [message["id"]]}, "operator")
            self.assertEqual(deleted.result(timeout=2), 1)

    def test_visible_history_filters_before_limit(self):
        active = self.send()
        cleared = self.store.enqueue("operator", self.b, "Old", "clear", operator=True)
        with self.store.connect(True) as db:
            db.execute("UPDATE messages SET state='completed' WHERE id=?", (cleared["id"],))
            db.execute("UPDATE messages SET state='delivery_unknown' WHERE id=?", (active["id"],))
        self.store.clear_history()
        self.assertEqual(self.store.messages(limit=1, visible=True)[0]["id"], active["id"])

    def test_loopback_broker_binding_does_not_use_dns(self):
        from http.server import BaseHTTPRequestHandler
        with patch("socket.getfqdn", side_effect=AssertionError("Must not resolve DNS")):
            server = LoopbackHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
            try:
                self.assertEqual(server.server_name, "127.0.0.1")
                self.assertGreater(server.server_port, 0)
            finally:
                server.server_close()

    def test_clear_history_preserves_pending_and_dedup(self):
        message = self.send()
        self.assertEqual(self.store.clear_history(), 0)
        self.store.claim()
        self.store.delivery_result(message["id"], "failed")
        self.assertEqual(self.store.clear_history(), 1)
        self.assertEqual(self.store.messages()[0]["body"], "[cleared]")
        with self.assertRaises(PolicyError):
            self.send()
        self.assertEqual(len(self.store.messages()), 1)

    def test_receipt_reconciliation_includes_messages_outside_ui_window(self):
        message = self.store.enqueue("operator", self.a, "Older pending receipt", "old", operator=True)
        self.store.claim(self.a)
        self.store.delivery_result(message["id"], "delivered")
        for index in range(205):
            self.store.enqueue("operator", self.b, "Newer UI entry", str(index), operator=True)
        self.assertNotIn(message["id"], {m["id"] for m in self.store.messages()})
        with patch("visor.broker.scan_codex_receipts", return_value=({message["id"]: True}, {})) as read:
            Broker(self.root).check_receipts()
        self.assertEqual(read.call_args.args[1][0]["id"], message["id"])
        self.assertEqual(self.store.message(message["id"])["state"], "completed")

    def test_offline_maintenance_preserves_unresolved_content_and_deduplication(self):
        states = ("queued", "held", "sending", "delivered", "delivery_unknown", "acknowledged", "completed", "failed")
        messages = {}
        for state in states:
            message = self.store.enqueue("operator", self.b, state + " body", state, operator=True)
            messages[state] = message["id"]
            with self.store.connect(True) as db:
                db.execute("UPDATE messages SET state=?,created=0 WHERE id=?", (state, message["id"]))
        result = self.store.maintain(30, compact=True)
        self.assertEqual(result["cleared_messages"], 2)
        for state, mid in messages.items():
            self.assertEqual(self.store.message(mid)["body"], "[cleared]" if state in ("completed", "failed") else state + " body")
        with self.assertRaises(PolicyError):
            self.store.enqueue("operator", self.b, "new body", "completed", operator=True)
        self.assertEqual(self.store.maintain(30)["cleared_messages"], 0)
        with self.assertRaises(PolicyError):
            self.store.maintain(-1)

    def test_receipt_scan_state_survives_restart_and_is_not_recreated_after_delete(self):
        self.store.save_receipt_scan(self.a, {"cursor": "older", "seen": ["first"]})
        self.assertEqual(Store(self.root).get_setting("receipt_scan:" + self.a)["cursor"], "older")
        self.store.delete_session(self.a)
        self.store.save_receipt_scan(self.a, {"cursor": "late-result"})
        self.assertIsNone(self.store.get_setting("receipt_scan:" + self.a))

    def test_receipt_scheduler_rotates_sessions_and_limits_batch(self):
        keys = [self.a]
        for index in range(5):
            keys.append(self.store.upsert_session("codex", f"extra-{index}", str(index), str(self.root)))
        for key in keys:
            message = self.store.enqueue("operator", key, "Pending", key, operator=True)
            self.store.claim(key)
            self.store.delivery_result(message["id"], "delivered")
        broker = Broker(self.root)
        with patch("visor.broker.scan_codex_receipts", return_value=({}, {})) as scan:
            broker.check_receipts()
            self.assertEqual(scan.call_count, 4)
            first = {call.args[0]["id"] for call in scan.call_args_list}
            scan.reset_mock()
            broker.check_receipts()
            second = {call.args[0]["id"] for call in scan.call_args_list}
        self.assertEqual(first | second, set(keys))

    def test_wrap_up_is_request_until_explicit_completion(self):
        self.store.update_session(self.a, managed=True, status="working")
        broker = Broker(self.root)
        result = broker.dispatch("wrap_up", {"request_id": "shutdown"}, "operator")
        self.assertEqual(len(result), 1)
        self.assertEqual(self.store.session(self.a)["shutdown"], "requested")
        self.assertFalse(self.store.get_setting("agent_enabled"))
        self.store.claim(self.a)
        self.store.delivery_result(result[0]["id"], "delivered")
        self.assertEqual(self.store.session(self.a)["shutdown"], "requested")
        self.store.acknowledge(self.a, result[0]["id"], True)
        self.assertIn("background work unverified", self.store.session(self.a)["shutdown"])

    def test_wrap_up_timeout_is_progress_only_and_survives_restart(self):
        broker = Broker(self.root)
        broker.dispatch("wrap_up_timeout", {"session": self.a, "seconds": 60}, "operator")
        self.store.update_session(self.a, managed=True, status="working")
        message = broker.dispatch("wrap_up", {"sessions": [self.a], "request_id": "timed"}, "operator")[0]
        request = self.store.get_setting("wrap_up_request:" + self.a)
        request["requested"] -= 120
        self.store.set_setting("wrap_up_request:" + self.a, request)
        self.store.set_setting("owned_work:" + self.a, [{"pid": 42, "created": 1}])
        with patch("visor.broker.retired", return_value=False):
            progress = Broker(self.root).dispatch("snapshot", {}, "operator")["wrap_up_progress"][self.a]
        self.assertEqual(progress["state"], "wait exceeded")
        self.assertEqual(progress["observed_workers_unresolved"], 1)
        self.assertEqual(self.store.message(message["id"])["state"], "queued")
        self.assertFalse(self.store.get_setting("agent_enabled"))
        for seconds in (True, 0, 86401):
            with self.assertRaises(PolicyError):
                broker.dispatch("wrap_up_timeout", {"session": self.a, "seconds": seconds}, "operator")

    def test_wrap_up_does_not_queue_for_closed_sessions(self):
        self.store.update_session(self.a, managed=True, status="disconnected")
        with self.assertRaisesRegex(PolicyError, "Wrap-up was not sent"):
            Broker(self.root).dispatch("wrap_up", {"request_id": "closed"}, "operator")
        self.assertEqual(self.store.messages(), [])

    def test_separate_wrap_up_requests_preserve_all_tracked_targets(self):
        broker = Broker(self.root)
        for key in (self.a, self.b):
            self.store.update_session(key, managed=True, status="idle")
            broker.dispatch("wrap_up", {"sessions": [key], "request_id": key}, "operator")
        self.assertEqual(self.store.get_setting("shutdown_targets"), [self.a, self.b])

    def test_selected_unattached_wrap_up_reports_failure(self):
        self.store.update_session(self.a, managed=True, status="disconnected", heartbeat=time.time())
        self.store.toggle(True)
        with self.assertRaisesRegex(PolicyError, "Wrap-up was not sent"):
            Broker(self.root).dispatch("wrap_up", {"sessions": [self.a], "request_id": "not-attached"}, "operator")
        self.assertEqual(self.store.messages(), [])
        self.assertTrue(self.store.get_setting("agent_enabled"))

    def test_resume_recovers_proven_closed_windows_terminal(self):
        self.store.update_session(self.a, managed=True, status="idle", window_token="old-title",
                                  process_id=12345, heartbeat=1, helper_identity=json.dumps({"pid": 12345}))
        with patch("visor.broker.sys.platform", "win32"), patch("visor.broker.windows") as windows, \
                patch("visor.win_identity.process_identity", side_effect=OSError(87, "Gone")), \
                patch("visor.broker.session_command", return_value=["mock-provider"]), \
                patch("visor.broker.ensure_codex_daemon"), \
                patch("visor.broker.terminal_launch") as launch:
            windows.return_value.find.side_effect = WindowNotFound("gone")
            Broker(self.root).launch(self.a)
            launch.assert_called_once()
        self.assertNotEqual(self.store.session(self.a)["window_token"], "old-title")
        self.assertIsNone(self.store.session(self.a)["helper_identity"])

    def test_resume_does_not_recover_inaccessible_helper(self):
        self.store.update_session(self.a, managed=True, window_token="old-title", process_id=12345,
                                  helper_identity=json.dumps({"pid": 12345}))
        with patch("visor.broker.sys.platform", "win32"), patch("visor.broker.windows") as windows, \
                patch("visor.win_identity.process_identity", side_effect=OSError(5, "Access denied")):
            windows.return_value.find.side_effect = WindowNotFound("gone")
            with self.assertRaises(PolicyError):
                Broker(self.root).launch(self.a)
        self.assertEqual(self.store.session(self.a)["window_token"], "old-title")

    def test_closed_terminal_recovers_after_broker_restart_clears_pid(self):
        self.store.update_session(self.a, managed=True, window_token="old-title", process_id=12345,
                                  heartbeat=time.time(), helper_identity=json.dumps({"pid": 12345, "created": 7}))
        self.store.recover()
        self.assertIsNone(self.store.session(self.a)["process_id"])
        self.store.update_session(self.a, status="idle")
        with patch("visor.broker.sys.platform", "win32"), patch("visor.broker.windows") as windows, \
                patch("visor.win_identity.process_identity", side_effect=OSError(87, "Gone")) as identity, \
                patch("visor.broker.session_command", return_value=["mock-provider"]), \
                patch("visor.broker.ensure_codex_daemon"), \
                patch("visor.broker.terminal_launch") as launch:
            windows.return_value.find.side_effect = WindowNotFound("gone")
            Broker(self.root).launch(self.a)
            identity.assert_called_once_with(12345)
            launch.assert_called_once()

    def test_broker_restart_does_not_forget_a_still_live_helper(self):
        saved = {"pid": 12345, "created": 7, "image": "helper.exe"}
        self.store.update_session(self.a, managed=True, window_token="old-title", process_id=12345,
                                  heartbeat=time.time(), helper_identity=json.dumps(saved))
        self.store.recover()
        with patch("visor.broker.sys.platform", "win32"), patch("visor.broker.windows") as windows, \
                patch("visor.win_identity.process_identity", return_value=saved):
            windows.return_value.find.side_effect = WindowNotFound("gone")
            with self.assertRaises(PolicyError):
                Broker(self.root).launch(self.a)
        self.assertEqual(self.store.session(self.a)["window_token"], "old-title")

    def test_resume_does_not_recover_ambiguous_window(self):
        self.store.update_session(self.a, managed=True, window_token="old-title")
        with patch("visor.broker.windows") as windows:
            windows.return_value.find.side_effect = WindowUnavailable("ambiguous")
            with self.assertRaises(PolicyError):
                Broker(self.root).launch(self.a)
        self.assertEqual(self.store.session(self.a)["window_token"], "old-title")

    def test_shutdown_tracking_persists_and_cancel_keeps_messages_held(self):
        self.store.update_session(self.a, managed=True, status="working")
        self.send()
        broker = Broker(self.root)
        broker.dispatch("wrap_up", {"request_id": "shutdown"}, "operator")
        reopened = Broker(self.root)
        self.assertEqual(reopened.dispatch("snapshot", {}, "operator")["shutdown_targets"], [self.a])
        reopened.dispatch("cancel_shutdown_tracking", {}, "operator")
        self.assertEqual(self.store.get_setting("shutdown_targets"), [])
        self.assertFalse(self.store.get_setting("agent_enabled"))
        agent_message = next(m for m in self.store.messages() if m["origin"] == "agent")
        self.assertEqual(agent_message["state"], "held")

    def test_helper_and_agent_credentials_have_distinct_roles(self):
        agent = self.store.issue_credential(self.a)
        helper = self.store.issue_credential(self.a, role="helper")
        self.assertEqual(self.store.authenticate_principal(agent), (self.a, "agent"))
        self.assertEqual(self.store.authenticate_principal(helper), (self.a, "helper"))
        broker = Broker(self.root)
        with self.assertRaises(PolicyError):
            broker.dispatch("heartbeat", {"pid": 1}, self.a)
        with self.assertRaises(PolicyError):
            broker.dispatch("send", {}, self.a, role="helper")

    def test_verified_existing_helper_survives_installation_upgrade_but_reused_pid_does_not(self):
        saved = {"pid": 42, "created": 10, "image": "previous-install/visor-console"}
        self.store.update_session(self.a, helper_identity=json.dumps(saved))
        self.store.recover()  # Upgrade restarts broker while preserving native identity.
        broker = Broker(self.root)
        with patch("visor.broker.process_identity", return_value=saved), \
                patch("visor.broker.expected_helper_image", return_value="new-install/visor-console"), \
                patch("visor.broker.descendants", return_value=[]):
            broker.dispatch("heartbeat", {"pid": 42}, self.a, role="helper")
        self.assertEqual(self.store.session(self.a)["process_id"], 42)
        self.assertIsNotNone(self.store.session(self.a)["heartbeat"])
        with patch("visor.broker.process_identity", return_value={**saved, "created": 11}), \
                self.assertRaisesRegex(PolicyError, "identity changed"):
            broker.dispatch("heartbeat", {"pid": 42}, self.a, role="helper")
        self.store.update_session(self.a, helper_identity=None)
        with patch("visor.broker.process_identity", return_value=saved), \
                patch("visor.broker.expected_helper_image", return_value="new-install/visor-console"), \
                self.assertRaisesRegex(PolicyError, "executable does not match"):
            broker.dispatch("heartbeat", {"pid": 42}, self.a, role="helper")

    def test_mcp_transport_confirmation_and_timeout(self):
        message = self.send()
        broker = Broker(self.root)
        broker.dispatch("receive", {}, self.b)
        self.assertEqual(self.store.messages()[0]["state"], "sending")
        self.store.expire_sending(age=-1)
        self.assertEqual(self.store.messages()[0]["state"], "delivery_unknown")
        with self.assertRaises(PolicyError):
            broker.dispatch("transport_received", {"message": message["id"]}, self.c)
        broker.dispatch("transport_received", {"message": message["id"]}, self.b)
        self.assertEqual(self.store.messages()[0]["state"], "delivered")

    def test_transport_receipt_does_not_regress_completion(self):
        message = self.send()
        self.store.claim(self.b)
        self.store.acknowledge(self.b, message["id"], completed=True)
        self.store.transport_received(self.b, message["id"])
        self.assertEqual(self.store.messages()[0]["state"], "completed")

    def test_single_broker_lock(self):
        lock = BrokerLock(self.root / "lock")
        try:
            with self.assertRaises(RuntimeError):
                BrokerLock(self.root / "lock")
        finally:
            lock.close()
        replacement = BrokerLock(self.root / "lock")
        replacement.close()

    def test_unverified_window_cannot_be_controlled(self):
        self.store.update_session(self.a, managed=True, window_token="old", heartbeat=1)
        with self.assertRaises(PolicyError):
            Broker(self.root).verified_window(self.a)

    @unittest.skipUnless(os.name == "nt", "Windows native process identity")
    def test_helper_identity_uses_native_process_birth(self):
        broker = Broker(self.root)
        broker.dispatch("heartbeat", {"pid": os.getpid()}, self.a, role="helper")
        session = self.store.session(self.a)
        identity = json.loads(session["helper_identity"])
        self.assertEqual(identity["pid"], os.getpid())
        self.assertGreater(identity["created"], 0)
        identity["created"] += 1
        self.store.update_session(self.a, helper_identity=json.dumps(identity))
        with self.assertRaises(PolicyError):
            broker.dispatch("heartbeat", {"pid": os.getpid()}, self.a, role="helper")

    def test_reopen_preserves_migrated_credentials_and_policy(self):
        token = self.store.issue_credential(self.a, role="helper")
        reopened = Store(self.root)
        self.assertEqual(reopened.authenticate_principal(token), (self.a, "helper"))
        self.assertEqual(reopened.pairs(), self.store.pairs())
        with reopened.connect() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)


class CommandsCase(unittest.TestCase):
    @patch("visor.providers.executable", return_value="provider")
    def test_exact_session_and_argument_boundaries(self, _):
        session = {"provider": "codex", "native_id": "exact-id", "cwd": 'project with spaces & quotes"'}
        command = session_command(session, ["python", "-m", "visor", "--mcp"])
        self.assertEqual(command[-2:], ["resume", "exact-id"])
        self.assertIn(session["cwd"], command)
        self.assertNotIn("--last", command)
        self.assertEqual(command[1:3], ["--remote", "unix://"])

    @patch("visor.providers.executable", return_value="claude")
    def test_claude_own_resume_contract(self, _):
        session = {"provider": "claude", "native_id": "uuid", "cwd": "/project"}
        self.assertEqual(session_command(session, ["visor"], new=True)[1:3], ["--session-id", "uuid"])
        command = session_command(session, ["visor"])
        self.assertEqual(command[1:3], ["--resume", "uuid"])
        self.assertNotIn("-p", command)
        self.assertNotIn("--dangerously-skip-permissions", command)

    def test_status_preserves_unknown(self):
        self.assertEqual(codex_status({"type": "newFutureStatus"}), "unknown")
        self.assertEqual(codex_status({"type": "active", "activeFlags": ["waitingOnApproval"]}), "waiting")
        self.assertEqual(codex_status({"type": "notLoaded"}), "disconnected")


class LayoutCase(unittest.TestCase):
    def test_grid_with_negative_monitor_coordinates(self):
        screen = Rect(-1920, 0, 1920, 1080)
        for mode in ("Grid", "Rows", "Columns", "Cascade"):
            rectangles = arrange(5, screen, mode)
            self.assertEqual(len(rectangles), 5)
            for rect in rectangles:
                self.assertEqual(rect, rect.clamped(screen))
                self.assertGreater(rect.width, 0)

    def test_stacked_tiles_fill_odd_monitor_height_without_gaps(self):
        area = Rect(-997, 21, 997, 751)
        tiles = arrange(3, area, "Stacked")
        self.assertEqual(tiles, arrange(3, area, "Rows"))
        self.assertEqual(sum(tile.height for tile in tiles), area.height)
        self.assertEqual(tiles[0].y, area.y)
        self.assertEqual(tiles[-1].y + tiles[-1].height, area.y + area.height)
        for a, b in pairwise(tiles):
            self.assertEqual(a.y + a.height, b.y)
            self.assertEqual(a.width, area.width)

    def test_classic_cascade_exposes_title_bars_and_wraps_on_screen(self):
        area = Rect(-1920, 0, 1920, 1080)
        rects = arrange(20, area, "Cascade")
        self.assertEqual(rects[1].x - rects[0].x, 28)
        self.assertEqual(rects[1].y - rects[0].y, 28)
        self.assertEqual(rects[0].width, 1440)
        self.assertEqual(rects[0].height, 810)
        for rect in rects:
            self.assertEqual(rect, rect.clamped(area))

    def test_disconnected_monitor_clamped(self):
        self.assertEqual(Rect(-1920, 10, 1600, 900).clamped(Rect(0, 0, 1280, 720)), Rect(0, 0, 1280, 720))

    def test_no_selection(self):
        self.assertEqual(arrange(0, Rect(0, 0, 100, 100)), [])


class MCPCase(unittest.TestCase):
    def test_no_operator_tools(self):
        result = handle({"method": "tools/list"}, None)
        self.assertEqual({t["name"] for t in result["tools"]}, {"send", "receive", "acknowledge", "peers"})

    def test_reject_sender_parameter(self):
        with self.assertRaises(ValueError):
            handle({"method": "tools/call", "params": {"name": "send", "arguments": {
                "sender": "someone", "recipient": "b", "body": "hello", "request_id": "1"}}}, None)

    def test_protocol_handshake(self):
        result = handle({"method": "initialize"}, None)
        self.assertIn("tools", result["capabilities"])
        json.dumps(result)


if __name__ == "__main__":
    unittest.main()
