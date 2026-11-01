"""A slow provider must not block unrelated, already approved recipients."""
import subprocess
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from visor.broker import Broker
from visor.store import PolicyError


class ObservedLock:
    def __init__(self, waiting):
        self.inner = threading.Lock()
        self.waiting = waiting

    def acquire(self, *args, **kwargs):
        if threading.current_thread().name == "delete-fixture":
            self.waiting.set()
        return self.inner.acquire(*args, **kwargs)

    def release(self):
        self.inner.release()

    def locked(self):
        return self.inner.locked()


class ParallelDeliveryCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.broker = Broker(Path(self.folder.name))

    def recipient(self, name):
        key = self.broker.store.upsert_session("codex", str(uuid.uuid4()), name, self.folder.name)
        self.broker.store.update_session(key, status="idle")
        return key

    def send(self, key, text):
        return self.broker.store.enqueue("operator", key, text, str(uuid.uuid4()), operator=True)

    def test_slow_target_does_not_delay_healthy_target(self):
        slow, fast = self.recipient("Slow fixture"), self.recipient("Healthy fixture")
        # The original delivery loop visits the most recently updated first.
        self.broker.store.update_session(slow, updated=self.broker.store.session(fast)["updated"] + 1)
        first, second = self.send(slow, "First"), self.send(fast, "Second")
        entered, release, accepted = threading.Event(), threading.Event(), threading.Event()

        def deliver(session, message):
            if session["id"] == slow:
                entered.set()
                release.wait(8)
            else:
                accepted.set()
            return "delivered", "Synthetic acceptance; acknowledgment pending"

        worker = threading.Thread(target=self.broker.delivery_loop)
        with patch("visor.broker.deliver_codex", side_effect=deliver):
            worker.start()
            try:
                self.assertTrue(entered.wait(3), "slow fixture was admitted")
                self.assertTrue(accepted.wait(2), "healthy recipient must progress before slow release")
                self.assertFalse(release.is_set())
            finally:
                self.broker.stop.set()
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.broker.store.message(first["id"])["state"], "delivered")
        self.assertEqual(self.broker.store.message(second["id"])["state"], "delivered")

    def test_one_recipient_stays_ordered_and_mcp_pull_cannot_overtake(self):
        key = self.recipient("Ordered fixture")
        first, second = self.send(key, "First"), self.send(key, "Second")
        entered, release = threading.Event(), threading.Event()
        calls = []

        def deliver(session, message):
            calls.append(message["id"])
            if message["id"] == first["id"]:
                entered.set()
                release.wait(5)
            return "delivered", "Synthetic acceptance"

        with patch("visor.broker.deliver_codex", side_effect=deliver):
            worker = threading.Thread(target=self.broker.deliver_session, args=(key,))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.broker.deliver_session(key)
                self.assertIsNone(self.broker.dispatch("receive", {}, key))
                self.assertEqual(self.broker.store.message(second["id"])["state"], "queued")
            finally:
                release.set()
                worker.join(3)
            self.broker.deliver_session(key)
        self.assertFalse(worker.is_alive())
        self.assertEqual(calls, [first["id"], second["id"]])

    def test_unknown_send_is_not_replayed_and_other_recipients_progress(self):
        broken, healthy = self.recipient("Broken fixture"), self.recipient("Healthy fixture")
        unknown, good = self.send(broken, "Keep exact original"), self.send(healthy, "Useful result")
        calls = []

        def deliver(session, message):
            calls.append(message["id"])
            if session["id"] == broken:
                raise subprocess.TimeoutExpired("synthetic-provider", 30)
            return "delivered", "Synthetic acceptance"

        with patch("visor.broker.deliver_codex", side_effect=deliver):
            self.broker.deliver_session(broken)
            self.broker.deliver_session(healthy)
            self.broker.deliver_session(broken)
        retained = self.broker.store.message(unknown["id"])
        self.assertEqual(retained["state"], "delivery_unknown")
        self.assertEqual(retained["body"], "Keep exact original")
        self.assertEqual(self.broker.store.message(good["id"])["state"], "delivered")
        self.assertEqual(calls, [unknown["id"], good["id"]])

    def test_operator_policy_change_does_not_wait_for_an_inflight_provider(self):
        a, b = self.recipient("Sender fixture"), self.recipient("Recipient fixture")
        self.broker.store.pair(a, b)
        self.broker.store.participate(a, True)
        self.broker.store.participate(b, True)
        self.broker.store.toggle(True)
        first = self.broker.store.enqueue(a, b, "In flight", "one")
        second = self.broker.store.enqueue(a, b, "Keep held", "two")
        entered, release, toggled = threading.Event(), threading.Event(), threading.Event()

        def deliver(session, message):
            entered.set()
            release.wait(5)
            return "delivered", "Synthetic acceptance"

        def toggle():
            self.broker.dispatch("toggle", {"enabled": False}, "operator")
            toggled.set()

        with patch("visor.broker.deliver_codex", side_effect=deliver):
            worker = threading.Thread(target=self.broker.deliver_session, args=(b,))
            control = threading.Thread(target=toggle)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                control.start()
                self.assertTrue(toggled.wait(1))
                self.assertEqual(self.broker.store.message(second["id"])["state"], "held")
            finally:
                release.set()
                worker.join(3)
                if control.ident:
                    control.join(3)
        self.assertEqual(self.broker.store.message(first["id"])["state"], "delivered")
        self.assertFalse(self.broker.store.get_setting("agent_enabled"))

    def test_deletion_waits_for_admitted_target_and_no_future_send_recreates_it(self):
        key = self.recipient("Delete fixture")
        self.send(key, "Already admitted")
        entered, release, closed, deleting = [threading.Event() for _ in range(4)]

        def deliver(session, message):
            entered.set()
            release.wait(5)
            return "delivered", "Synthetic acceptance"

        def delete_provider(session, before):
            deleting.set()
            before([session["native_id"]])
            closed.set()
            return [session["native_id"]]

        with patch("visor.broker.deliver_codex", side_effect=deliver) as send, \
                patch("visor.broker.delete_provider_session", side_effect=delete_provider), \
                patch.object(self.broker, "close_for_delete"):
            worker = threading.Thread(target=self.broker.deliver_session, args=(key,))
            delete = threading.Thread(target=self.broker.dispatch,
                args=("delete_session", {"session": key, "confirmed": True}, "operator"))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                delete.start()
                self.assertTrue(deleting.wait(2))
                self.assertFalse(closed.is_set(), "target history must remain until its admitted write finishes")
            finally:
                release.set()
                worker.join(3)
                if delete.ident:
                    delete.join(3)
            self.assertTrue(closed.is_set())
            self.broker.deliver_session(key)
            self.assertEqual(send.call_count, 1)
        self.assertFalse(worker.is_alive())
        self.assertFalse(delete.is_alive())
        with self.assertRaises(PolicyError):
            self.broker.store.session(key)

    def test_delivery_options_are_validated_and_operator_owned(self):
        key = self.recipient("Policy fixture")
        self.assertEqual(self.broker.dispatch("delivery_options", {}, "operator"), {"max_parallel": 4})
        for value in [{}, {"max_parallel": True}, {"max_parallel": 0}, {"max_parallel": 33},
                      {"max_parallel": 2.0}, {"max_parallel": 2, "extra": 1}]:
            with self.subTest(value=value), self.assertRaises(PolicyError):
                self.broker.dispatch("delivery_options", {"value": value}, "operator")
        for role in ["agent", "helper", "activity"]:
            with self.subTest(role=role), self.assertRaises(PolicyError):
                self.broker.dispatch("delivery_options", {"value": {"max_parallel": 2}}, key, role)
        self.assertEqual(self.broker.dispatch("delivery_options", {"value": {"max_parallel": 2}}, "operator"), {"max_parallel": 2})
        self.assertEqual(self.broker.store.get_setting("delivery_options"), {"max_parallel": 2})

    def test_slow_target_deletion_does_not_hold_healthy_messages_or_policy(self):
        slow, healthy = self.recipient("Delete fixture"), self.recipient("Healthy fixture")
        self.send(slow, "Already admitted")
        self.send(healthy, "Independent approved request")
        entered, release, waiting, accepted, toggled = [threading.Event() for _ in range(5)]
        self.broker.delivery_targets[slow] = ObservedLock(waiting)

        def deliver(session, message):
            if session["id"] == slow:
                entered.set()
                release.wait(6)
            else:
                accepted.set()
            return "delivered", "Synthetic acceptance"

        def delete_provider(session, before):
            before([session["native_id"]])
            return [session["native_id"]]

        def policy():
            self.broker.dispatch("toggle", {"enabled": False}, "operator")
            toggled.set()

        workers = [threading.Thread(target=self.broker.deliver_session, args=(slow,)),
                   threading.Thread(name="delete-fixture", target=self.broker.dispatch,
                       args=("delete_session", {"session": slow, "confirmed": True}, "operator")),
                   threading.Thread(target=self.broker.deliver_session, args=(healthy,)),
                   threading.Thread(target=policy)]
        with patch("visor.broker.deliver_codex", side_effect=deliver), \
                patch("visor.broker.delete_provider_session", side_effect=delete_provider), \
                patch.object(self.broker, "close_for_delete"):
            try:
                workers[0].start()
                self.assertTrue(entered.wait(2))
                workers[1].start()
                self.assertTrue(waiting.wait(2))
                self.assertIsNone(self.broker.dispatch("receive", {}, slow))
                workers[2].start()
                workers[3].start()
                self.assertTrue(accepted.wait(2))
                self.assertTrue(toggled.wait(2))
                self.assertFalse(release.is_set())
                self.assertTrue(workers[1].is_alive())
            finally:
                release.set()
                for worker in workers:
                    if worker.ident:
                        worker.join(4)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(self.broker.deleting_targets, set())

    def test_configured_capacity_bounds_concurrent_provider_calls(self):
        self.broker.dispatch("delivery_options", {"value": {"max_parallel": 2}}, "operator")
        keys = [self.recipient("Capacity fixture") for _ in range(3)]
        for key in keys:
            self.send(key, "Preserve queued work")
        two_started, third_started, release = [threading.Event() for _ in range(3)]
        guard, active, peak, calls = threading.Lock(), [0], [0], []

        def deliver(session, message):
            with guard:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
                calls.append(message["id"])
                if len(calls) == 2:
                    two_started.set()
                if len(calls) == 3:
                    third_started.set()
            release.wait(5)
            with guard:
                active[0] -= 1
            return "delivered", "Synthetic acceptance"

        worker = threading.Thread(target=self.broker.delivery_loop)
        with patch("visor.broker.deliver_codex", side_effect=deliver):
            worker.start()
            try:
                self.assertTrue(two_started.wait(3))
                self.assertFalse(third_started.is_set())
                self.assertEqual(peak[0], 2)
                release.set()
                self.assertTrue(third_started.wait(3))
            finally:
                release.set()
                self.broker.stop.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(set(calls)), 3)
        self.assertEqual(peak[0], 2)

    def test_full_queue_admission_is_not_limited_to_recent_ui_messages(self):
        key = self.recipient("Old queued fixture")
        original = self.send(key, "Original queued work")
        unrelated = self.recipient("Other fixture")
        for index in range(205):
            message = self.send(unrelated, str(index))
            with self.broker.store.connect(True) as db:
                db.execute("UPDATE messages SET state='completed' WHERE id=?", (message["id"],))
        self.assertNotIn(original["id"], [item["id"] for item in self.broker.store.messages(visible=True)])
        self.assertEqual(self.broker.store.queued_recipients(), [key])
        with patch("visor.broker.deliver_codex", return_value=("delivered", "Synthetic acceptance")):
            self.broker.deliver_session(key)
        self.assertEqual(self.broker.store.message(original["id"])["state"], "delivered")
