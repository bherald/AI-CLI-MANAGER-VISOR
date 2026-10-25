import base64
import copy
import hashlib
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from visor.activity_store import ACTIVITY_TABLES, ActivityService
from visor.store import PolicyError, Store


def document(id='board', revision=1):
    return {'schema_version': 1, 'id': id, 'title': 'Prism', 'revision': revision,
            'cards': [{'id': 'text', 'kind': 'text', 'title': 'Notes', 'html': '<p>Hello</p>'},
                      {'id': 'ask', 'kind': 'question', 'title': 'Question', 'prompt': 'What next?', 'choices': ['Yes', 'No']}]}


class ActivityStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        self.a = self.store.upsert_session('codex', 'a', 'Alpha', '/tmp')
        self.b = self.store.upsert_session('claude', 'b', 'Beta', '/tmp')
        self.c = self.store.upsert_session('codex', 'c', 'Gamma', '/tmp')
        self.service = ActivityService(self.store)

    def publish(self):
        return self.service.publish(self.a, document(), 'pub1')

    def shared(self):
        self.publish()
        self.store.toggle(True)
        for actor in (self.a, self.b, self.c):
            self.store.participate(actor, True)
        self.store.pair(self.a, self.b)
        self.service.share(self.a, 'board', [self.b], 'share1')

    def image(self):
        image = QImage(2, 3, QImage.Format.Format_RGB32)
        image.fill(0xff448899)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(image.save(buffer, 'PNG'))
        return bytes(data)

    def test_authenticated_identity_private_projection_and_schema(self):
        doc = document()
        doc['session_link'] = {'id': self.b, 'label': 'Impersonated'}
        doc['cards'][0]['contributor'] = self.b
        self.service.publish(self.a, doc, 'pub1')
        actual = self.service.get(self.a, 'board')
        self.assertNotIn('path', actual)
        self.assertEqual(actual['document']['session_link'], {'id': self.a, 'label': 'Alpha'})
        self.assertEqual(actual['document']['contributors'], {'text': self.a, 'ask': self.a})
        self.assertEqual(self.service.list(self.b), [])
        with self.assertRaises(PolicyError):
            self.service.get(self.b, 'board')
        path = Path(self.service.get(None, 'board')['path'])
        self.assertEqual(json.loads(path.read_text()), actual['document'])
        if os.name != 'nt':
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        with self.store.connect() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(ACTIVITY_TABLES <= tables)

    def test_revisions_dedupe_and_concurrency(self):
        first = self.publish()
        self.assertEqual(first, self.publish())
        changed = document()
        changed['title'] = 'Different'
        with self.assertRaises(PolicyError):
            self.service.publish(self.a, changed, 'pub1')
        def update(request):
            try:
                return self.service.publish(self.a, document(revision=2), request, 1)
            except PolicyError:
                return None
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(update, ['update1', 'update2']))
        self.assertEqual(sum(r is not None for r in results), 1)
        retried = self.publish()
        self.assertEqual({k: v for k, v in retried.items() if k != "projection"},
                         {k: v for k, v in first.items() if k != "projection"})
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM activity_revisions').fetchone()[0], 2)
        self.assertEqual(self.service.get(None, 'board')['revision'], 2)

    def test_projection_skips_unchanged_and_repairs(self):
        self.publish()
        path = Path(self.service.get(None, 'board')['path'])
        stamp = path.stat().st_mtime_ns
        self.service.list(None)
        self.assertEqual(path.stat().st_mtime_ns, stamp)
        path.write_text('bad')
        self.assertEqual(json.loads(Path(self.service.get(None, 'board')['path']).read_text())['id'], 'board')
        path.unlink()
        self.service.get(None, 'board')
        self.assertTrue(path.is_file())

    def test_projection_failure_is_durably_retryable(self):
        with patch.object(self.service, '_stage', side_effect=OSError('disk full')):
            accepted = self.publish()
        self.assertTrue(accepted['accepted'])
        self.assertEqual(accepted['projection']['state'], 'pending')
        result = self.publish()
        self.assertEqual(result['revision'], 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM activity_revisions').fetchone()[0], 1)

    def test_assets_canonical_backup_projection_and_tamper_repair(self):
        content = self.image()
        asset = self.service.asset(self.a, 'example.png', base64.b64encode(content).decode())
        self.assertEqual(asset, self.service.asset(self.a, 'again.png', base64.b64encode(content).decode()))
        doc = document()
        doc['cards'].append({'id': 'picture', 'kind': 'image', 'title': 'Picture', 'path': asset['path']})
        self.service.publish(self.a, doc, 'pub1')
        path = Path(self.service.get(None, 'board')['path']).parent / asset['path']
        path.unlink()
        self.service.get(None, 'board')
        self.assertEqual(path.read_bytes(), content)
        path.write_bytes(b'corrupt')
        self.service.get(None, 'board')
        self.assertEqual(path.read_bytes(), content)
        other = copy.deepcopy(doc)
        other['id'] = 'other'
        with self.assertRaises(PolicyError):
            self.service.publish(self.b, other, 'pub-other')
        with self.store.connect() as db:
            self.assertEqual(bytes(db.execute('SELECT content FROM activity_assets').fetchone()[0]), content)

    def test_assets_reject_bad_format_limits_and_symlinks(self):
        content = self.image()
        encoded = base64.b64encode(content).decode()
        for name, data in [('../x.png', encoded), ('x.jpg', encoded), ('x.svg', encoded), ('x.png', '!'), ('x.png', '')]:
            with self.subTest(name=name, data=data[:8]), self.assertRaises(PolicyError):
                self.service.asset(self.a, name, data)
        self.store.set_setting('activity_policy', {'max_image_pixels': 1})
        with self.assertRaises(PolicyError):
            self.service.asset(self.a, 'x.png', encoded)
        self.store.set_setting('activity_policy', {'max_image_bytes': 1})
        with self.assertRaises(PolicyError):
            self.service.asset(self.a, 'x.png', encoded)
        self.store.set_setting('activity_policy', {})
        asset = self.service.asset(self.a, 'x.png', encoded)
        path = self.service._owner_dir(self.a) / asset['path']
        path.unlink()
        path.symlink_to(Path(self.tmp.name) / 'outside')
        with self.assertRaises(PolicyError):
            self.service.asset(self.a, 'x.png', encoded)

    def test_shared_contributors_ownership_and_current_permissions(self):
        self.shared()
        doc = self.service.get(self.b, 'board')['document']
        doc['revision'] = 2
        doc['cards'].append({'id': 'beta', 'kind': 'question', 'title': 'Beta', 'prompt': 'Beta question?'})
        accepted = self.service.publish(self.b, doc, 'beta1', 1)
        self.assertEqual(accepted['owner'], self.a)
        current = self.service.get(self.a, 'board')['document']
        self.assertEqual(current['cards'][-1]['contributor'], self.b)
        self.assertEqual(current['session_link']['id'], self.a)
        malicious = copy.deepcopy(current)
        malicious['revision'] = 3
        malicious['cards'][-1]['prompt'] = 'Changed by owner'
        with self.assertRaises(PolicyError):
            self.service.publish(self.a, malicious, 'bad1', 2)
        malicious = copy.deepcopy(current)
        malicious['revision'] = 3
        malicious['cards'] = malicious['cards'][1:]
        with self.assertRaises(PolicyError):
            self.service.publish(self.b, malicious, 'bad2', 2)
        self.store.pair(self.a, self.b, False)
        self.assertEqual(self.service.list(self.b), [])
        with self.assertRaises(PolicyError):
            self.service.get(self.b, 'board')
        with self.assertRaises(PolicyError):
            self.service.publish(self.b, doc, 'beta1', 1)
        self.assertEqual(len(self.service.list(None)), 1)
        self.service.share(self.a, 'board', [], 'unshare')
        self.store.toggle(False)
        self.assertEqual(len(self.service.list(self.a)), 1)

    def test_sharing_rejects_transitive_connections_and_nonowner(self):
        self.shared()
        self.store.pair(self.b, self.c)
        with self.assertRaises(PolicyError):
            self.service.share(self.a, 'board', [self.b, self.c], 'transitive')
        with self.assertRaises(PolicyError):
            self.service.share(self.b, 'board', [self.c], 'nonowner')
        self.store.pair(self.a, self.c)
        shared = self.service.share(self.a, 'board', [self.b, self.c], 'allpairs')
        self.assertEqual(len(shared['members']), 3)
        self.store.participate(self.c, False)
        self.assertEqual(self.service.list(self.b), [])

    def test_replies_route_only_to_question_contributor_and_ack_read(self):
        self.shared()
        doc = self.service.get(self.b, 'board')['document']
        doc['revision'] = 2
        doc['cards'].append({'id': 'beta', 'kind': 'question', 'title': 'Beta', 'prompt': 'Beta question?'})
        self.service.publish(self.b, doc, 'beta1', 1)
        reply = self.service.reply('board', 'beta', 2, 'A private answer', 'answer1')
        self.assertEqual(reply['recipient'], self.b)
        self.assertEqual(reply['prompt_sha256'], hashlib.sha256(b'Beta question?').hexdigest())
        self.assertEqual(self.service.replies(self.a), [])
        self.assertEqual(self.service.replies(self.b), [reply])
        with self.assertRaises(PolicyError):
            self.service.acknowledge(self.a, reply['id'])
        self.assertEqual(self.service.acknowledge(self.b, reply['id'])['state'], 'read')
        with self.store.connect(True) as db:
            db.execute("UPDATE activity_replies SET state='completed' WHERE id=?", (reply['id'],))
        self.assertEqual(self.service.acknowledge(self.b, reply['id'])['state'], 'completed')
        self.store.toggle(False)
        self.assertEqual(self.service.replies(self.b), [])
        with self.assertRaises(PolicyError):
            self.service.acknowledge(self.b, reply['id'])

    def test_reply_revision_dedupe_and_freeform(self):
        self.publish()
        reply = self.service.reply('board', 'ask', 1, 'Yes\nWith notes', 'answer1')
        self.service.publish(self.a, document(revision=2), 'pub2', 1)
        self.assertEqual(self.service.reply('board', 'ask', 1, 'Yes\nWith notes', 'answer1'), reply)
        for card, revision, answer, request in [('ask', 1, 'Later', 'stale'), ('text', 2, 'Wrong', 'wrong'), ('ask', 2, 'Different', 'answer1'), ('ask', 2, ' ', 'blank')]:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.service.reply('board', card, revision, answer, request)
        self.assertEqual(len(self.service.replies(self.a)), 1)

    def test_control_dedupe_and_operator_edit_preserve_contributor(self):
        self.publish()
        closed = self.service.control(self.a, 'board', 'close', 'close1')
        self.assertEqual(closed, self.service.control(self.a, 'board', 'close', 'close1'))
        self.assertEqual(closed['view_seq'], 2)
        with self.assertRaises(PolicyError):
            self.service.control(self.b, 'board', 'open', 'bad')
        edited = self.service.edit('board', 'text', 1, '<p>Operator notes</p>', 'edit1')
        self.assertEqual(edited, self.service.edit('board', 'text', 1, '<p>Operator notes</p>', 'edit1'))
        current = self.service.get(self.a, 'board')
        self.assertEqual(current['revision'], 2)
        self.assertEqual(current['view_action'], 'close')
        self.assertEqual(current['document']['last_editor'], 'operator')
        self.assertEqual(current['document']['cards'][0]['contributor'], self.a)
        with self.assertRaises(PolicyError):
            self.service.edit('board', 'text', 1, 'stale', 'edit2')

    def test_deleted_session_history_remains_operator_only(self):
        self.publish()
        self.store.delete_session(self.a)
        self.assertEqual(self.service.list(None)[0]['owner'], self.a)
        with self.assertRaises(PolicyError):
            self.service.list(self.a)

    def test_shared_asset_projects_from_contributor_and_sqlite_restore(self):
        self.shared()
        image = self.image()
        asset = self.service.asset(self.b, 'beta.png', base64.b64encode(image).decode())
        doc = self.service.get(self.b, 'board')['document']
        doc['revision'] = 2
        doc['cards'].append({'id': 'beta-picture', 'kind': 'image', 'title': 'Beta picture', 'path': asset['path']})
        self.service.publish(self.b, doc, 'beta-image', 1)
        projected = Path(self.service.get(None, 'board')['path']).parent / asset['path']
        self.assertEqual(projected.read_bytes(), image)
        with tempfile.TemporaryDirectory() as restored_root:
            restored_store = Store(Path(restored_root))
            with self.store.connect() as source, restored_store.connect() as destination:
                source.backup(destination)
            restored = ActivityService(restored_store)
            path = Path(restored.get(None, 'board')['path'])
            self.assertEqual((path.parent / asset['path']).read_bytes(), image)
            self.assertEqual(restored.get(self.b, 'board')['document']['cards'][-1]['contributor'], self.b)

    def test_forged_asset_path_and_projection_symlink_are_rejected(self):
        doc = document()
        doc['cards'].append({'id': 'outside', 'kind': 'image', 'title': 'Outside', 'path': '../private.png'})
        with self.assertRaises(ValueError):
            self.service.publish(self.a, doc, 'outside')
        self.publish()
        path = Path(self.service.get(None, 'board')['path'])
        path.unlink()
        target = Path(self.tmp.name) / 'target'
        target.write_text('preserved')
        path.symlink_to(target)
        with self.assertRaises(PolicyError):
            self.service.get(None, 'board')
        self.assertEqual(target.read_text(), 'preserved')

    def test_card_identity_cannot_change_kind(self):
        self.publish()
        candidate = document(revision=2)
        candidate['cards'][0] = {'id': 'text', 'kind': 'question', 'title': 'Question', 'prompt': 'Changed kind'}
        with self.assertRaises(PolicyError):
            self.service.publish(self.a, candidate, 'change-kind', 1)
        self.assertEqual(self.service.get(self.a, 'board')['revision'], 1)

    def test_invalid_json_policy_and_oversize(self):
        self.store.set_setting('activity_policy', {'unknown': 3})
        with self.assertRaises(PolicyError):
            self.publish()
        self.store.set_setting('activity_policy', {'max_document_bytes': 1})
        with self.assertRaises(PolicyError):
            self.publish()
        self.store.set_setting('activity_policy', {})
        invalid = document()
        invalid['extra'] = float('nan')
        with self.assertRaises(PolicyError):
            self.service.publish(self.a, invalid, 'bad')


if __name__ == '__main__':
    unittest.main()
