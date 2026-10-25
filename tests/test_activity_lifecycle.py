"""Durable operator dismissal and actual presentation are separate from content/read state."""
import tempfile
import unittest
from pathlib import Path

from visor.activity_store import ACTIVITY_TABLES, ActivityService
from visor.broker import Broker
from visor.state_backup import restore, snapshot
from visor.store import PolicyError, Store


class ActivityLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.broker = Broker(self.root)
        self.owner = self.broker.store.upsert_session('codex', 'view-owner', 'Owner', str(self.root))
        self.peer = self.broker.store.upsert_session('claude', 'view-peer', 'Peer', str(self.root))
        self.document = {'schema_version': 1, 'id': 'board', 'title': 'Board', 'revision': 1,
                         'cards': [{'id': 'question', 'kind': 'question', 'title': 'Question', 'prompt': 'Next?'}]}
        self.broker.activities.publish(self.owner, self.document, 'publish-one')

    def board(self):
        return self.broker.activities.get(None, 'board')

    def state(self):
        return self.board()['view_state']

    def report(self, state, revision=1, sequence=None, request=None):
        return self.broker.dispatch('activity_view', {'id': 'board', 'state': state, 'revision': revision,
                                  'view_seq': self.board()['view_seq'] if sequence is None else sequence,
                                  'request_id': request or state + '-report'}, 'operator', role='operator')

    def control(self, action, *, actor=None, request=None, **extra):
        return self.broker.dispatch('activity_control', {'id': 'board', 'action': action,
                                   'request_id': request or action + '-request', **extra},
                                   actor or 'operator', role='agent' if actor else 'operator')

    def test_close_publish_restart_and_quiet_reopen_until_operator_reopens(self):
        self.assertEqual(self.state()['presentation'], 'requested')
        self.assertEqual(self.state()['unshown_revision'], 1)
        self.report('visible')
        self.assertIsNone(self.state()['unshown_revision'])
        closed = self.report('closed')
        self.assertTrue(closed['accepted'])
        self.assertEqual(closed['view_seq'], 2)
        self.broker.activities.publish(self.owner, {**self.document, 'revision': 2}, 'publish-two', 1)
        request = self.control('focus', actor=self.owner)
        self.assertEqual(request['view_seq'], 2)
        self.assertEqual(request['view_action'], 'close')
        self.assertTrue(request['view_state']['operator_dismissed'])
        self.assertTrue(request['view_state']['reopen_requested'])
        self.assertEqual(request['view_state']['requested_by'], self.owner)
        self.assertEqual(request['view_state']['requested_revision'], 2)
        self.broker = Broker(self.root)
        state = self.state()
        self.assertEqual(state['presentation'], 'closed')
        self.assertEqual(state['presented_revision'], 1)
        self.assertEqual(state['unshown_revision'], 2)
        self.assertTrue(state['reopen_requested'])
        reopened = self.control('open')
        self.assertEqual(reopened['view_seq'], 3)
        self.assertFalse(reopened['view_state']['operator_dismissed'])
        self.assertFalse(reopened['view_state']['reopen_requested'])
        self.assertIsNone(reopened['view_state']['requested_by'])
        self.assertEqual(reopened['view_state']['requested_revision'], 0)
        self.assertEqual(reopened['view_state']['presentation'], 'requested')
        self.report('visible', revision=2, request='visible-two')
        self.assertIsNone(self.state()['unshown_revision'])

    def test_repeated_agent_requests_coalesce_and_cannot_supply_approval_bypass(self):
        self.report('closed')
        for index in range(3):
            result = self.control('open', actor=self.owner, request='quiet-' + str(index), approved=True, operator=True)
            self.assertEqual(result['view_seq'], 2)
            self.assertEqual(result['view_action'], 'close')
            self.assertEqual(result['view_state']['presentation'], 'closed')
        self.assertTrue(self.state()['operator_dismissed'])
        self.assertTrue(self.state()['reopen_requested'])

    def test_late_closed_or_visible_report_cannot_override_new_operator_reopen(self):
        self.report('closed', sequence=1)
        self.control('open')
        for state in ('closed', 'visible', 'hidden'):
            result = self.report(state, sequence=2, request='late-' + state)
            self.assertFalse(result['accepted'])
            self.assertEqual(result['view_seq'], 3)
        self.assertFalse(self.state()['operator_dismissed'])
        self.assertEqual(self.state()['presentation'], 'requested')
        self.assertEqual(self.state()['presented_revision'], 0)
        self.assertFalse(self.report('visible', revision=2, sequence=2, request='stale-future-revision')['accepted'])

    def test_exact_request_retries_do_not_repeat_close_or_revert_newer_state(self):
        closed = self.report('closed', sequence=1, request='close-once')
        self.control('open')
        self.assertEqual(self.report('closed', sequence=1, request='close-once'), closed)
        self.assertEqual(self.board()['view_seq'], 3)
        self.assertFalse(self.state()['operator_dismissed'])
        with self.assertRaises(PolicyError):
            self.report('hidden', sequence=1, request='close-once')

    def test_hidden_reports_and_broker_restart_do_not_claim_a_revision_was_shown(self):
        self.report('hidden')
        self.assertFalse(self.state()['operator_dismissed'])
        self.assertEqual(self.state()['presentation'], 'hidden')
        self.assertEqual(self.state()['presented_revision'], 0)
        self.assertEqual(self.state()['unshown_revision'], 1)
        self.broker = Broker(self.root)
        self.assertEqual(self.state()['presentation'], 'unknown')
        self.assertFalse(self.state()['operator_dismissed'])
        self.report('visible')
        observed = self.state()['observed_at']
        self.broker = Broker(self.root)
        self.assertEqual(self.state()['presentation'], 'unknown')
        self.assertEqual(self.state()['observed_at'], observed)
        self.assertEqual(self.state()['presented_revision'], 1)

    def test_agent_close_is_programmatic_and_does_not_latch_dismissal(self):
        closed = self.control('close', actor=self.owner)
        self.assertFalse(closed['view_state']['operator_dismissed'])
        self.report('hidden', sequence=closed['view_seq'])
        reopened = self.control('focus', actor=self.owner)
        self.assertEqual(reopened['view_seq'], closed['view_seq'] + 1)
        self.assertEqual(reopened['view_action'], 'focus')
        self.assertFalse(reopened['view_state']['reopen_requested'])

    def test_operator_close_latches_before_actual_report_and_keeps_new_reopen_request(self):
        closed = self.control('close')
        self.assertTrue(closed['view_state']['operator_dismissed'])
        self.assertEqual(closed['view_state']['presentation'], 'requested')
        for state in ('visible', 'hidden'):
            self.assertFalse(self.report(state, sequence=closed['view_seq'])['accepted'])
        self.control('focus', actor=self.owner)
        actual = self.report('closed', sequence=closed['view_seq'])
        self.assertTrue(actual['accepted'])
        self.assertEqual(actual['view_state']['presentation'], 'closed')
        self.assertTrue(actual['view_state']['reopen_requested'])

    def test_reports_are_operator_only_and_validate_revision_and_sequence(self):
        params = {'id': 'board', 'state': 'visible', 'revision': 1, 'view_seq': 1, 'request_id': 'forged'}
        for role in ('agent', 'activity', 'helper'):
            with self.subTest(role=role), self.assertRaises(PolicyError):
                self.broker.dispatch('activity_view', params, self.owner, role)
        for changes in ({'state': 'read'}, {'revision': 2}, {'revision': True}, {'view_seq': 0}, {'view_seq': True}):
            with self.subTest(changes=changes), self.assertRaises(PolicyError):
                self.broker.dispatch('activity_view', {**params, **changes}, 'operator', 'operator')
        self.assertIsNone(self.state()['observed_at'])

    def test_presented_revision_is_monotonic_and_old_revision_close_is_valid(self):
        self.broker.activities.publish(self.owner, {**self.document, 'revision': 2}, 'publish-two', 1)
        self.report('visible', revision=2, request='new-visible')
        self.report('visible', revision=1, request='old-visible')
        self.assertEqual(self.state()['presented_revision'], 2)
        self.report('closed', revision=1)
        self.assertTrue(self.state()['operator_dismissed'])
        self.assertIsNone(self.state()['unshown_revision'])

    def test_lifecycle_metadata_obeys_existing_member_permissions(self):
        self.report('closed')
        self.assertEqual(self.broker.activities.list(self.peer), [])
        with self.assertRaises(PolicyError):
            self.broker.activities.get(self.peer, 'board')
        self.broker.store.toggle(True)
        for actor in (self.owner, self.peer):
            self.broker.store.participate(actor, True)
        self.broker.store.pair(self.owner, self.peer)
        self.broker.activities.share(self.owner, 'board', [self.peer], 'share')
        self.assertTrue(self.broker.activities.get(self.peer, 'board')['view_state']['operator_dismissed'])
        self.broker.store.pair(self.owner, self.peer, False)
        self.assertEqual(self.broker.activities.list(self.peer), [])

    def test_deletion_removes_child_state_and_backup_restore_preserves_dismissal(self):
        self.report('visible')
        self.report('closed')
        self.control('open', actor=self.owner, request='quiet')
        backup = snapshot(self.root)
        with tempfile.TemporaryDirectory() as restored_root:
            root = Path(restored_root)
            restore(root, backup)
            broker = Broker(root)
            state = broker.activities.get(None, 'board')['view_state']
            self.assertTrue(state['operator_dismissed'])
            self.assertTrue(state['reopen_requested'])
            self.assertEqual(state['presented_revision'], 1)
            self.assertEqual(state['presentation'], 'closed')
        self.broker.activities.delete('board', 1, 'delete', confirmed=True)
        with self.broker.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM activity_lifecycle').fetchone()[0], 0)
        self.assertIn('activity_lifecycle', ACTIVITY_TABLES)
        with self.assertRaises(PolicyError):
            self.report('closed')

    def test_existing_boards_get_unknown_presentation_without_false_dismissal(self):
        with self.broker.store.connect(True) as db:
            db.execute('DROP TABLE activity_lifecycle')
        service = ActivityService(Store(self.root))
        state = service.get(None, 'board')['view_state']
        self.assertEqual(state['presentation'], 'unknown')
        self.assertFalse(state['operator_dismissed'])
        self.assertEqual(state['presented_revision'], 0)
        self.assertEqual(state['unshown_revision'], 1)


if __name__ == '__main__':
    unittest.main()
