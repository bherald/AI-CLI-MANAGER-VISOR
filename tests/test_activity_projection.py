"""Durable acceptance and short publication fences around recoverable file caches."""
import base64
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from visor.activity_store import ActivityService
from visor.broker import Broker
from visor.store import PolicyError, Store


class ActivityProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.broker = Broker(self.root)
        self.service = self.broker.activities
        self.owner = self.broker.store.upsert_session('codex', 'projection-owner', 'Owner', str(self.root))
        self.document = {'schema_version': 1, 'id': 'board', 'title': 'Board', 'revision': 1,
                         'cards': [{'id': 'text', 'kind': 'text', 'title': 'Text', 'html': 'Original'},
                                   {'id': 'question', 'kind': 'question', 'title': 'Question', 'prompt': 'Next?'}]}

    def publish(self):
        return self.service.publish(self.owner, self.document, 'publish')

    @staticmethod
    def receipt(result):
        return {key: value for key, value in result.items() if key != 'projection'}

    def test_committed_projection_failure_lost_response_restart_retry_has_one_receipt(self):
        with patch.object(self.service, '_stage', side_effect=OSError('synthetic disk full')):
            first = self.publish()
        self.assertTrue(first['accepted'])
        self.assertEqual(first['request_id'], 'publish')
        self.assertEqual(first['projection']['state'], 'pending')
        self.assertIn('disk full', first['projection']['error'])
        with self.broker.store.connect() as db:
            before = dict(db.execute("SELECT * FROM activity_requests WHERE request_id='publish'").fetchone())
        restarted = ActivityService(Store(self.root))
        # Treat the first response as lost: recover only from the original request.
        retry = restarted.publish(self.owner, self.document, 'publish')
        self.assertEqual(self.receipt(first), self.receipt(retry))
        self.assertEqual(retry['projection']['state'], 'ready')
        with self.broker.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM activity_revisions').fetchone()[0], 1)
            self.assertEqual(dict(db.execute("SELECT * FROM activity_requests WHERE request_id='publish'").fetchone()), before)
        self.assertEqual(restarted.get(None, 'board')['view_state']['presented_revision'], 0)
        self.assertNotEqual(restarted.get(None, 'board')['view_state']['presentation'], 'visible')

    def test_accepted_retry_is_recoverable_even_when_current_policy_is_invalid(self):
        accepted = self.publish()
        self.broker.store.set_setting('activity_policy', {'max_cards': 0})
        retry = self.publish()
        self.assertEqual(self.receipt(retry), self.receipt(accepted))
        self.assertEqual(retry['projection']['state'], 'pending')
        self.assertIn('invalid', retry['projection']['error'])

    def test_policy_change_during_asset_preflight_rejects_before_canonical_commit(self):
        self.publish()
        original = self.service._assets
        def validate_then_change(*args, **kwargs):
            result = original(*args, **kwargs)
            self.broker.store.set_setting('activity_policy', {'max_cards': 1})
            return result
        with patch.object(self.service, '_assets', side_effect=validate_then_change), self.assertRaisesRegex(PolicyError, 'policy changed'):
            self.service.publish(self.owner, {**self.document, 'revision': 2}, 'policy-race', 1)
        with self.broker.store.connect() as db:
            self.assertEqual(db.execute("SELECT revision FROM activity_boards WHERE id='board'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM activity_requests WHERE request_id='policy-race'").fetchone()[0], 0)

    def test_other_service_revision_invalidates_cache_once(self):
        self.publish()
        other = ActivityService(Store(self.root))
        other.edit('board', 'text', 1, 'New content', 'edit')
        with patch.object(self.service, '_projection', wraps=self.service._projection) as project:
            first = self.service.list(None)
            second = self.service.list(None)
        self.assertEqual(project.call_count, 1)
        self.assertEqual(first[0]['revision'], 2)
        self.assertEqual(second[0]['projection']['revision'], 2)

    def test_edit_accepted_despite_projection_failure_and_precommit_failure_remains_rejected(self):
        self.publish()
        with patch.object(self.service, '_stage', side_effect=OSError('write failed')):
            result = self.service.edit('board', 'text', 1, 'Saved canonically', 'edit')
        self.assertTrue(result['accepted'])
        self.assertEqual(result['revision'], 2)
        self.assertEqual(result['projection']['state'], 'pending')
        self.assertEqual(self.receipt(self.service.edit('board', 'text', 1, 'Saved canonically', 'edit')), self.receipt(result))
        invalid = {**self.document, 'id': 'invalid', 'cards': [{'id': 'bad', 'kind': 'unsupported', 'title': 'Invalid'}]}
        with self.assertRaises(ValueError):
            self.service.publish(self.owner, invalid, 'bad-input')
        with self.broker.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM activity_requests WHERE request_id='bad-input'").fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM activity_revisions').fetchone()[0], 2)

    def test_unchanged_snapshot_is_metadata_only_with_no_hash_files_or_writer_transaction(self):
        self.publish()
        self.broker.activity_snapshot()
        with patch('visor.activity_store._hash', side_effect=AssertionError('unexpected hash')), \
                patch.object(self.service, '_projection', side_effect=AssertionError('unexpected materialization')), \
                patch.object(self.service, '_same_content', side_effect=AssertionError('unexpected file read')), \
                patch.object(self.service, '_stage', side_effect=AssertionError('unexpected write')), \
                patch.object(self.broker.store, 'connect', wraps=self.broker.store.connect) as connect:
            for _ in range(5):
                result = self.broker.activity_snapshot()
                self.assertEqual(result['activities'][0]['projection']['state'], 'ready')
            self.assertFalse(any(call.args and call.args[0] is True for call in connect.call_args_list))

    def test_failed_projection_is_not_retried_on_each_snapshot_but_explicit_open_repairs(self):
        with patch.object(self.service, '_stage', side_effect=OSError('disk full')):
            self.publish()
        with patch.object(self.service, '_projection', side_effect=AssertionError('unchanged error was retried')):
            snapshot = self.broker.activity_snapshot()
            self.assertIn('projection_error', snapshot['activities'][0])
        opened = self.broker.dispatch('activity_control', {'id': 'board', 'action': 'open', 'request_id': 'open'}, 'operator', 'operator')
        self.assertEqual(opened['projection']['state'], 'ready')
        self.assertNotIn('projection_error', self.broker.activity_snapshot()['activities'][0])

    def test_explicit_read_repairs_tamper_and_rejects_symlink_even_with_cached_metadata(self):
        self.publish()
        path = Path(self.service.get(None, 'board')['path'])
        path.write_text('tampered')
        self.service.list(None)  # Metadata polling deliberately does not scan files.
        self.assertEqual(path.read_text(), 'tampered')
        self.service.get(None, 'board')
        self.assertEqual(json.loads(path.read_text())['id'], 'board')
        outside = self.root / 'outside'
        outside.write_text('preserve')
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(PolicyError):
            self.service.get(None, 'board')
        self.assertEqual(outside.read_text(), 'preserve')

    def blocking_stage(self):
        entered, release = threading.Event(), threading.Event()
        original = self.service._stage
        first = threading.Lock()
        def stage(path, content):
            temporary = original(path, content)
            if path.name == 'board.json' and first.acquire(blocking=False):
                entered.set()
                if not release.wait(8):
                    temporary.unlink(missing_ok=True)
                    raise TimeoutError('test did not release projection')
            return temporary
        return entered, release, stage

    def test_slow_postcommit_projection_does_not_block_reply_publish_or_delete_and_cannot_resurrect(self):
        self.publish()
        self.service.publish(self.owner, {**self.document, 'id': 'other'}, 'other')
        entered, release, stage = self.blocking_stage()
        with ThreadPoolExecutor(max_workers=3) as workers, patch.object(self.service, '_stage', side_effect=stage):
            editing = workers.submit(self.service.edit, 'board', 'text', 1, 'New revision', 'edit')
            self.assertTrue(entered.wait(3))
            try:
                reply = workers.submit(self.service.reply, 'board', 'question', 2, 'Reply while staging', 'reply').result(timeout=2)
                self.assertEqual(reply['revision'], 2)
                update = workers.submit(self.service.publish, self.owner, {**self.document, 'id': 'other', 'revision': 2}, 'other-two', 1).result(timeout=2)
                self.assertTrue(update['accepted'])
                deletion = workers.submit(self.service.delete, 'board', 2, 'delete', confirmed=True).result(timeout=2)
                self.assertTrue(deletion['deleted'])
            finally:
                release.set()
            accepted = editing.result(timeout=3)
        self.assertTrue(accepted['accepted'])
        self.assertEqual(accepted['projection']['state'], 'pending')
        self.assertFalse(any(self.root.glob('activities/*/board.json')))
        self.assertFalse(any(self.root.glob('activities/*/.activity-*')))
        self.assertEqual([board['id'] for board in self.service.list(None)], ['other'])

    def test_stale_staged_read_cannot_overwrite_newer_projection(self):
        self.publish()
        path = Path(self.service.get(None, 'board')['path'])
        path.unlink()
        entered, release, stage = self.blocking_stage()
        with ThreadPoolExecutor(max_workers=2) as workers, patch.object(self.service, '_stage', side_effect=stage):
            reading = workers.submit(self.service.get, None, 'board')
            self.assertTrue(entered.wait(3))
            try:
                newer = workers.submit(self.service.edit, 'board', 'text', 1, 'Latest', 'edit').result(timeout=2)
                self.assertEqual(newer['projection']['revision'], 2)
            finally:
                release.set()
            with self.assertRaises(PolicyError):
                reading.result(timeout=3)
        self.assertEqual(json.loads(path.read_text())['revision'], 2)
        self.assertEqual(self.service.list(None)[0]['projection']['revision'], 2)

    def test_asset_validation_precedes_writer_transaction_and_repeated_cards_are_deduplicated(self):
        self.publish()
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1XcAAAAASUVORK5CYII=')
        asset = self.service.asset(self.owner, 'image.png', base64.b64encode(png).decode())
        candidate = {**self.document, 'revision': 2, 'cards': [*self.document['cards'],
                     *[{'id': f'image-{index}', 'kind': 'image', 'title': 'Image', 'path': asset['path']} for index in range(10)]]}
        entered, release = threading.Event(), threading.Event()
        original = self.service._check_image
        first = threading.Lock()
        def check_image(*args):
            if first.acquire(blocking=False):
                entered.set()
                if not release.wait(8):
                    raise TimeoutError('test did not release asset check')
            return original(*args)
        with ThreadPoolExecutor(max_workers=2) as workers, patch.object(self.service, '_check_image', side_effect=check_image) as checked:
            publishing = workers.submit(self.service.publish, self.owner, candidate, 'image-update', 1)
            self.assertTrue(entered.wait(3))
            try:
                reply = workers.submit(self.service.reply, 'board', 'question', 1, 'Concurrent reply', 'during-validation').result(timeout=2)
                self.assertEqual(reply['revision'], 1)
            finally:
                release.set()
            self.assertTrue(publishing.result(timeout=3)['accepted'])
            self.assertEqual(checked.call_count, 2)  # Once precommit, once for the projection; not twenty.


if __name__ == '__main__':
    unittest.main()
