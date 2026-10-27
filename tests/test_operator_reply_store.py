import json
import tempfile
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from visor.state_backup import restore, snapshot
from visor.store import ACKNOWLEDGEABLE_STATES, OPERATOR_REPLY_PREFIX, PolicyError, Store


def uid():
    return str(uuid.uuid4())


class OperatorReplyStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)
        self.agent = self.store.upsert_session('codex', uid(), 'Synthetic recipient', str(self.root))
        self.other = self.store.upsert_session('claude', uid(), 'Other recipient', str(self.root))

    def parent(self, state='sending', **kwargs):
        result = self.store.enqueue('operator', self.agent, 'Operator question', uid(), operator=True, **kwargs)
        with self.store.connect(True) as db:
            db.execute('UPDATE messages SET state=? WHERE id=?', (state, result['id']))
        return result['id']

    def reply(self, parent, body='A final answer', request_id=None, **kwargs):
        return self.store.operator_reply(parent, body, request_id or uid(), identity=kwargs.get('identity', self.agent), role=kwargs.get('role', 'agent'))

    def get(self, parent):
        return self.store.message_get(parent, identity='operator', role='operator')

    def stored(self, parent):
        with self.store.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (OPERATOR_REPLY_PREFIX + parent,)).fetchone()
        return json.loads(row[0]) if row else None

    def test_reply_exact_retry_and_restart_are_immutable_without_ack_fabrication(self):
        parent = self.parent()
        request = uid()
        first = self.reply(parent, '\x1c  Final answer \u2003', request)
        self.assertEqual(first['body'], 'Final answer')
        self.assertEqual(first['sender'], self.agent)
        self.assertEqual(first['recipient'], 'operator')
        self.assertEqual(first['parent_message_id'], parent)
        self.assertEqual(len(first['parent_sha256']), 64)
        self.store = Store(self.root)
        self.assertEqual(self.reply(parent, 'Final answer', request), first)
        self.assertEqual(self.get(parent)['reply'], first)
        self.assertEqual(self.get(parent)['state'], 'sending')
        for body, new_request in [('Changed answer', request), ('Final answer', uid())]:
            with self.subTest(body=body), self.assertRaisesRegex(PolicyError, 'One final reply'):
                self.reply(parent, body, new_request)
        self.store.acknowledge(self.agent, parent, completed=True)
        self.assertEqual(self.get(parent)['reply'], first)
        self.assertEqual(self.get(parent)['state'], 'completed')

    def test_all_acknowledgeable_states_allow_reply_without_changing_state(self):
        for state in ACKNOWLEDGEABLE_STATES:
            with self.subTest(state=state):
                parent = self.parent(state)
                self.reply(parent)
                self.assertEqual(self.get(parent)['state'], state)
        for state in ('queued', 'held', 'failed', 'deleted'):
            with self.subTest(state=state), self.assertRaises(PolicyError):
                self.reply(self.parent(state))

    def test_roles_identity_parent_kind_and_peer_policy_are_separate(self):
        parent = self.parent()
        for role, identity in [('operator', 'operator'), ('helper', self.agent), ('activity', self.agent),
                               (None, self.agent), ('agent', 'operator'), ('agent', self.other), ('agent', None)]:
            with self.subTest(role=role, identity=identity), self.assertRaises(PolicyError):
                self.reply(parent, identity=identity, role=role)
        self.store.toggle(False)
        self.store.participate(self.agent, False)
        self.assertEqual(self.reply(parent)['recipient'], 'operator')
        with self.assertRaisesRegex(PolicyError, 'No approved direct connection'):
            self.store.enqueue(self.agent, self.other, 'Not an operator reply', uid())
        with self.assertRaises(PolicyError):
            self.reply(self.parent(kind='wrap_up'))
        agent_parent = self.parent()
        with self.store.connect(True) as db:
            db.execute("UPDATE messages SET sender=?,origin='agent' WHERE id=?", (self.other, agent_parent))
        with self.assertRaises(PolicyError):
            self.reply(agent_parent)

    def test_direct_store_rejects_invalid_fields_ids_types_and_unicode(self):
        parent = self.parent()
        for bad in ('', 'not-a-uuid', parent.upper(), parent.replace('-', ''), 1, None, []):
            with self.subTest(parent=bad), self.assertRaises(PolicyError):
                self.reply(bad)
        for bad in ('short', uid().upper(), 1, None):
            with self.subTest(request=bad), self.assertRaises(PolicyError):
                self.store.operator_reply(parent, 'answer', bad, identity=self.agent, role='agent')
        for bad in ('', ' \x1c\u2003', 'x' * 16001, '\ud800', b'bytes', 5, None, [], {}):
            with self.subTest(body=repr(bad)[:40]), self.assertRaises(PolicyError):
                self.reply(parent, bad)
        with self.assertRaises(TypeError):
            self.store.operator_reply(parent, 'answer', uid(), identity=self.agent, role='agent', recipient='operator')
        text = '\ufeff' + '😀' * 15999
        self.assertEqual(self.reply(parent, text)['body'], text)

    def test_generic_settings_cannot_read_overwrite_or_probe_reserved_records(self):
        parent = self.parent()
        first = self.reply(parent)
        for key in (OPERATOR_REPLY_PREFIX + parent, OPERATOR_REPLY_PREFIX + uid(), OPERATOR_REPLY_PREFIX):
            with self.subTest(key=key):
                with self.assertRaises(PolicyError):
                    self.store.get_setting(key, 'fallback')
                with self.assertRaises(PolicyError):
                    self.store.set_setting(key, {})
                with self.store.connect() as db, self.assertRaises(PolicyError):
                    self.store.setting(db, key, 'fallback')
        self.assertEqual(self.get(parent)['reply'], first)

    def test_exact_lookup_survives_global_history_eviction_and_is_operator_only(self):
        parent = self.parent()
        first = self.reply(parent)
        for _ in range(205):
            self.store.enqueue('operator', self.other, 'New unrelated event', uid(), operator=True)
        self.assertNotIn(parent, {m['id'] for m in self.store.messages()})
        self.assertEqual(self.get(parent)['reply'], first)
        for identity, role in [(self.agent, 'agent'), (self.agent, 'helper'), (self.agent, 'activity'), ('operator', None), (self.agent, 'operator')]:
            with self.subTest(role=role), self.assertRaises(PolicyError):
                self.store.message_get(parent, identity=identity, role=role)

    def test_parent_tamper_and_malformed_reserved_payload_cannot_expose_shadow_reply(self):
        parent = self.parent()
        original = self.reply(parent)
        with self.store.connect(True) as db:
            db.execute("UPDATE messages SET body='Changed parent' WHERE id=?", (parent,))
        with self.assertRaisesRegex(PolicyError, 'binding'):
            self.get(parent)
        with self.assertRaises(PolicyError):
            self.reply(parent, original['body'], original['request_id'])
        with self.store.connect(True) as db:
            db.execute("UPDATE messages SET body='Operator question' WHERE id=?", (parent,))
        for bad in ({**original, 'sender': self.other}, {**original, 'extra': True}, {**original, 'created': float('inf')},
                    {**original, 'body': '\ud800'}, [], 'broken'):
            with self.store.connect(True) as db:
                db.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(bad), OPERATOR_REPLY_PREFIX + parent))
            with self.subTest(payload=type(bad).__name__), self.assertRaises(PolicyError):
                self.get(parent)

    def test_delete_parent_and_session_remove_reply_in_same_lifecycle(self):
        parent = self.parent()
        first = self.reply(parent)
        self.store.delete_messages([parent])
        self.assertIsNone(self.stored(parent))
        with self.assertRaises(PolicyError):
            self.get(parent)
        with self.assertRaises(PolicyError):
            self.reply(parent, first['body'], first['request_id'])
        another = self.parent()
        self.reply(another)
        self.store.delete_session(self.agent)
        self.assertIsNone(self.stored(another))
        self.assertIsNone(self.store.message(another))

    def test_retention_cutoff_removes_completed_failed_replies_but_retains_unknown_pending(self):
        now = time.time()
        rows = {}
        for state, age in [('completed', 40), ('failed', 40), ('completed', 5), ('delivery_unknown', 40), ('sending', 40)]:
            parent = self.parent('sending')
            self.reply(parent)
            with self.store.connect(True) as db:
                db.execute('UPDATE messages SET state=?,created=? WHERE id=?', (state, now-age*86400, parent))
            rows[parent] = state in ('completed', 'failed') and age > 30
        self.assertEqual(self.store.clear_history(30), 2)
        for parent, removed in rows.items():
            self.assertEqual(self.stored(parent) is None, removed)
            if removed:
                with self.assertRaises(PolicyError):
                    self.get(parent)
            else:
                self.assertIsNotNone(self.get(parent)['reply'])

    def test_reply_delete_and_clear_races_leave_no_orphan(self):
        for cleanup in ('delete', 'clear'):
            parent = self.parent('completed')
            entered, release = threading.Event(), threading.Event()
            original = self.store._operator_parent_hash
            def blocked(row, entered=entered, release=release, original=original):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('Fixture release timed out')
                return original(row)
            with ThreadPoolExecutor(max_workers=2) as workers, patch.object(self.store, '_operator_parent_hash', side_effect=blocked):
                posting = workers.submit(self.reply, parent)
                self.assertTrue(entered.wait(2))
                removing = workers.submit(self.store.delete_messages, [parent]) if cleanup == 'delete' else workers.submit(self.store.clear_history)
                release.set()
                posting.result(timeout=5)
                removing.result(timeout=5)
            self.assertIsNone(self.stored(parent))
            with self.assertRaises(PolicyError):
                self.get(parent)
            with self.assertRaises(PolicyError):
                self.reply(parent)

    def test_two_simultaneous_final_replies_accept_exactly_one(self):
        parent = self.parent()
        barrier = threading.Barrier(2)
        def post(body):
            barrier.wait(timeout=3)
            try:
                return self.reply(parent, body)
            except PolicyError:
                return None
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(post, body) for body in ('One', 'Two')]
            outcomes = [f.result(timeout=5) for f in futures]
        accepted = [r for r in outcomes if r]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(self.get(parent)['reply'], accepted[0])

    def test_private_backup_restore_preserves_binding_and_invalid_orphan_is_not_readable(self):
        parent = self.parent()
        reply = self.reply(parent)
        backup = Path(snapshot(self.root))
        if __import__('os').name != 'nt':
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.store.delete_messages([parent])
        restore(self.root, backup)
        self.store = Store(self.root)
        self.assertEqual(self.get(parent)['reply'], reply)
        self.assertEqual(self.get(parent)['state'], 'delivery_unknown')
        self.assertFalse(self.store.get_setting('agent_enabled'))
        orphan = uid()
        with self.store.connect(True) as db:
            db.execute('INSERT INTO settings VALUES(?,?)', (OPERATOR_REPLY_PREFIX + orphan, json.dumps({**reply, 'parent_message_id': orphan})))
            db.execute("UPDATE messages SET body='Wrong restored parent' WHERE id=?", (parent,))
        malformed_backup = snapshot(self.root)
        restore(self.root, malformed_backup)
        for mid in (parent, orphan):
            with self.subTest(mid=mid), self.assertRaises(PolicyError):
                self.get(mid)
