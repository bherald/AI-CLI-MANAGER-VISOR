"""Explicit saved-board deletion preserves unrelated sessions, assets and backups."""
import base64
import copy
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from visor.activity_store import ACTIVITY_TABLES, ActivityService
from visor.broker import Broker
from visor.state_backup import restore, snapshot
from visor.store import PolicyError, Store


class ActivityDeleteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.broker = Broker(self.root)
        self.service = self.broker.activities
        self.owner = self.broker.store.upsert_session('codex', 'delete-owner', 'Owner', str(self.root))
        self.peer = self.broker.store.upsert_session('claude', 'delete-peer', 'Peer', str(self.root))
        self.document = {'schema_version': 1, 'id': 'board', 'title': 'Saved board', 'revision': 1,
                         'cards': [{'id': 'ask', 'kind': 'question', 'title': 'Question', 'prompt': 'Saved prompt'}]}
        self.service.publish(self.owner, self.document, 'publish-board')
        self.event = self.service.reply('board', 'ask', 1, 'Private saved answer', 'answer-board')

    def delete(self, **changes):
        params = {'id': 'board', 'expected_revision': 1, 'request_id': 'delete-board', 'confirmed': True, **changes}
        return self.broker.dispatch('activity_delete', params, 'operator', role='operator')

    def test_delete_clears_active_content_keeps_request_tombstones_and_sessions(self):
        message = self.broker.store.enqueue('operator', self.owner, 'Unrelated message', 'ordinary', operator=True)
        before_sessions = self.broker.store.sessions()
        path = Path(self.service.get(None, 'board')['path'])
        result = self.delete()
        self.assertTrue(result['deleted'])
        self.assertTrue(result['retained_assets'])
        self.assertTrue(result['retained_backups'])
        self.assertEqual(result['projection_cleanup'], 'complete')
        self.assertFalse(path.exists())
        self.assertEqual(self.service.list(None), [])
        self.assertEqual(self.broker.store.sessions(), before_sessions)
        self.assertEqual(self.broker.store.message(message['id']), message)
        with self.broker.store.connect() as db:
            for table in ('activity_boards', 'activity_revisions', 'activity_replies', 'activity_members'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
            requests = [dict(row) for row in db.execute('SELECT * FROM activity_requests')]
            self.assertEqual(len(requests), 3)
            self.assertNotIn('Private saved answer', json.dumps(requests))
            self.assertNotIn('Saved prompt', json.dumps(requests))
            tombstone = db.execute('SELECT * FROM activity_deleted WHERE id=?', ('board',)).fetchone()
            self.assertEqual(tombstone['revision'], 1)
        self.assertIn('activity_deleted', ACTIVITY_TABLES)

    def test_confirmation_roles_and_exact_revision_are_required(self):
        for confirmed in (False, None, 1, 'true'):
            with self.subTest(confirmed=confirmed), self.assertRaises(PolicyError):
                self.delete(confirmed=confirmed)
        for role in ('agent', 'activity', 'helper'):
            with self.subTest(role=role), self.assertRaises(PolicyError):
                self.broker.dispatch('activity_delete', {'id': 'board', 'expected_revision': 1,
                                                        'request_id': 'delete-board', 'confirmed': True}, self.owner, role)
        for revision in (0, -1, True, '1'):
            with self.subTest(revision=revision), self.assertRaises(PolicyError):
                self.delete(expected_revision=revision)
        next_document = {**self.document, 'revision': 2}
        self.service.publish(self.owner, next_document, 'second-revision', 1)
        with self.assertRaisesRegex(PolicyError, 'Activity delete revision conflict; current revision is 2; review and confirm again'):
            self.delete()
        self.assertEqual(self.service.get(None, 'board')['revision'], 2)

    def test_tombstone_blocks_queued_actions_republish_and_survives_restart(self):
        result = self.delete()
        reopened = ActivityService(Store(self.root))
        self.assertEqual(reopened.delete('board', 1, 'delete-board', confirmed=True), result)
        with self.assertRaises(PolicyError):
            reopened.delete('board', 2, 'delete-board', confirmed=True)
        for actor, request in ((self.owner, 'publish-board'), (self.owner, 'new-publish'), (self.peer, 'new-owner')):
            with self.subTest(actor=actor, request=request), self.assertRaisesRegex(PolicyError, 'was deleted'):
                reopened.publish(actor, self.document, request)
        operations = [lambda: reopened.get(self.owner, 'board'),
                      lambda: reopened.control(self.owner, 'board', 'open', 'late-open'),
                      lambda: reopened.reply('board', 'ask', 1, 'Private saved answer', 'answer-board'),
                      lambda: reopened.reply('board', 'ask', 1, 'Late answer', 'late-answer'),
                      lambda: reopened.acknowledge(self.owner, self.event['id']),
                      lambda: reopened.share(self.owner, 'board', [self.peer], 'late-share')]
        for operation in operations:
            with self.assertRaises(PolicyError):
                operation()
        self.assertEqual(reopened.replies(self.owner), [])

    def test_same_asset_other_board_and_existing_backup_are_preserved(self):
        content = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1XcAAAAASUVORK5CYII=')
        asset = self.service.asset(self.owner, 'shared.png', base64.b64encode(content).decode())
        revised = {**self.document, 'revision': 2, 'cards': [*self.document['cards'],
                   {'id': 'picture', 'kind': 'image', 'title': 'Picture', 'path': asset['path']}]}
        self.service.publish(self.owner, revised, 'image-revision', 1)
        other = {**revised, 'id': 'other', 'revision': 1}
        self.service.publish(self.owner, other, 'other-board')
        backup = snapshot(self.root)
        backup_bytes = Path(backup).read_bytes()
        result = self.delete(expected_revision=2)
        self.assertTrue(result['retained_assets'])
        remaining = self.service.get(None, 'other')
        self.assertEqual((Path(remaining['path']).parent / asset['path']).read_bytes(), content)
        with self.broker.store.connect() as db:
            self.assertEqual(bytes(db.execute('SELECT content FROM activity_assets').fetchone()[0]), content)
        self.assertEqual(Path(backup).read_bytes(), backup_bytes)

    def test_deleted_backup_restores_tombstone_without_resurrection(self):
        deleted = self.delete()
        backup = snapshot(self.root)
        with tempfile.TemporaryDirectory() as restored_root:
            root = Path(restored_root)
            restore(root, backup)
            restored = ActivityService(Store(root))
            self.assertEqual(restored.list(None), [])
            self.assertEqual(restored.delete('board', 1, 'delete-board', confirmed=True), deleted)
            with self.assertRaisesRegex(PolicyError, 'was deleted'):
                restored.publish(self.owner, self.document, 'publish-again')

    def test_projection_failure_reports_committed_delete_and_dedupes(self):
        with patch.object(self.service, '_delete_projection', side_effect=OSError('cache unavailable')):
            result = self.delete()
        self.assertTrue(result['deleted'])
        self.assertEqual(result['projection_cleanup'], 'pending')
        self.assertEqual(result['projection_error'], 'cache unavailable')
        self.assertEqual(self.delete(), result)
        self.assertEqual(self.service.list(None), [])

    def test_symlink_projection_never_touches_external_target(self):
        path = Path(self.service.get(None, 'board')['path'])
        outside = self.root / 'outside.json'
        outside.write_text('Preserve unrelated file')
        path.unlink()
        path.symlink_to(outside)
        result = self.delete()
        self.assertEqual(result['projection_cleanup'], 'pending')
        self.assertIn('symlink', result['projection_error'])
        self.assertEqual(outside.read_text(), 'Preserve unrelated file')
        self.assertTrue(path.is_symlink())
        self.assertEqual(self.service.list(None), [])

    def test_delete_and_publish_cas_serialize_without_recreation(self):
        barrier = threading.Barrier(2)
        def deletion():
            barrier.wait()
            try:
                return self.delete()
            except PolicyError:
                return None
        def publish():
            barrier.wait()
            try:
                return self.service.publish(self.owner, {**self.document, 'revision': 2}, 'racing-publish', 1)
            except PolicyError:
                return None
        with ThreadPoolExecutor(max_workers=2) as workers:
            delete_future = workers.submit(deletion)
            publish_future = workers.submit(publish)
            deleted, published = delete_future.result(), publish_future.result()
        self.assertEqual(sum(result is not None for result in (deleted, published)), 1)
        if deleted:
            self.assertEqual(self.service.list(None), [])
        else:
            self.assertEqual(self.service.get(None, 'board')['revision'], 2)

    def test_reply_result_with_same_id_as_another_board_is_not_redacted(self):
        collision = copy.deepcopy(self.document)
        collision['id'] = self.event['id']
        self.service.publish(self.owner, collision, 'collision-board')
        self.delete(id=collision['id'])
        self.assertEqual(self.service.reply('board', 'ask', 1, 'Private saved answer', 'answer-board'), self.event)


if __name__ == '__main__':
    unittest.main()
