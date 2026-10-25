"""An offline operator close can only be superseded by another operator reopen."""
import tempfile
import unittest
from pathlib import Path

from visor.activity_store import ActivityService
from visor.broker import Broker
from visor.state_backup import restore, snapshot
from visor.store import Store


class OfflineOperatorCloseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.broker = Broker(self.root)
        self.owner = self.broker.store.upsert_session('codex', 'offline-close', 'Owner', str(self.root))
        self.broker.activities.publish(self.owner, {'schema_version': 1, 'id': 'board', 'title': 'Board', 'revision': 1,
                                       'cards': [{'id': 'text', 'kind': 'text', 'title': 'Text', 'html': 'Notes'}]}, 'publish')

    def control(self, actor, action, request):
        return self.broker.dispatch('activity_control', {'id': 'board', 'action': action, 'request_id': request},
                                   actor, 'operator' if actor == 'operator' else 'agent')

    def close(self, seq=1, request='offline-close'):
        return self.broker.dispatch('activity_view', {'id': 'board', 'state': 'closed', 'revision': 1,
                                   'view_seq': seq, 'request_id': request}, 'operator', 'operator')

    def test_offline_close_survives_newer_agent_focus(self):
        focused = self.control(self.owner, 'focus', 'agent-focus')
        self.assertEqual(focused['view_seq'], 2)
        self.assertEqual(focused['view_state']['last_operator_open_seq'], 0)
        closed = self.close()
        self.assertTrue(closed['accepted'])
        self.assertEqual(closed['view_seq'], 3)
        self.assertTrue(closed['view_state']['operator_dismissed'])
        quiet = self.control(self.owner, 'open', 'agent-again')
        self.assertEqual(quiet['view_seq'], 3)
        self.assertTrue(quiet['view_state']['reopen_requested'])
        self.assertEqual(self.close(), closed)

    def test_only_newer_operator_open_or_focus_supersedes_close(self):
        reopened = self.control('operator', 'open', 'operator-open')
        self.assertEqual(reopened['view_state']['last_operator_open_seq'], 2)
        self.control(self.owner, 'focus', 'agent-focus')
        ignored = self.close()
        self.assertFalse(ignored['accepted'])
        self.assertFalse(ignored['view_state']['operator_dismissed'])
        self.assertEqual(ignored['view_state']['last_operator_open_seq'], 2)
        # Closing the operator-reopened view itself remains valid after later agent actions.
        accepted = self.close(seq=2, request='newer-user-close')
        self.assertTrue(accepted['accepted'])

    def test_visible_and_hidden_reports_still_require_exact_sequence(self):
        self.control(self.owner, 'focus', 'agent-focus')
        for state in ('visible', 'hidden'):
            result = self.broker.dispatch('activity_view', {'id': 'board', 'state': state, 'revision': 1,
                                         'view_seq': 1, 'request_id': state}, 'operator', 'operator')
            self.assertFalse(result['accepted'])
            self.assertEqual(result['view_state']['presented_revision'], 0)
        self.assertFalse(self.close(seq=3)['accepted'])

    def test_operator_order_survives_broker_restart_and_backup_restore(self):
        self.control('operator', 'focus', 'operator-focus')
        self.broker = Broker(self.root)
        self.assertFalse(self.close()['accepted'])
        backup = snapshot(self.root)
        with tempfile.TemporaryDirectory() as restored_root:
            root = Path(restored_root)
            restore(root, backup)
            restored = Broker(root)
            result = restored.activities.view('board', 'closed', 1, 1, 'another-old-close')
            self.assertFalse(result['accepted'])
            self.assertEqual(result['view_state']['last_operator_open_seq'], 2)

    def test_migration_recovers_operator_open_sequence_from_receipts_only(self):
        self.control('operator', 'open', 'operator-open')
        self.control(self.owner, 'focus', 'agent-focus')
        with self.broker.store.connect(True) as db:
            db.execute('ALTER TABLE activity_lifecycle DROP COLUMN last_operator_open_seq')
        service = ActivityService(Store(self.root))
        state = service.get(None, 'board')['view_state']
        self.assertEqual(state['last_operator_open_seq'], 2)
        self.assertFalse(service.view('board', 'closed', 1, 1, 'migrated-old-close')['accepted'])
        self.assertTrue(service.view('board', 'closed', 1, 2, 'migrated-current-close')['accepted'])


if __name__ == '__main__':
    unittest.main()
