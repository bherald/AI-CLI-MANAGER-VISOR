import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from visor.broker import Broker


class ActivityIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.broker = Broker(Path(self.temporary.name))
        self.owner = self.broker.store.upsert_session('codex', 'synthetic', 'Synthetic', self.temporary.name)

    def publish(self, identity, cards=None):
        document = {'schema_version': 1, 'id': identity, 'title': identity, 'revision': 1,
                    'cards': cards or [{'id': 'text', 'kind': 'text', 'title': 'Text', 'html': 'Content'}]}
        self.broker.activities.publish(self.owner, document, identity + '-publish')

    def snapshot(self):
        with patch.object(self.broker, 'wrap_up_progress', return_value={}):
            return self.broker.dispatch('snapshot', {}, 'operator')

    def test_corrupt_board_and_missing_asset_leave_healthy_board_and_sessions(self):
        self.publish('healthy')
        self.publish('corrupt')
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image = QImage(1, 1, QImage.Format.Format_RGB32)
        image.fill(0)
        self.assertTrue(image.save(buffer, 'PNG'))
        asset = self.broker.activities.asset(self.owner, 'tiny.png', base64.b64encode(bytes(data)).decode())
        self.publish('missing-asset', [{'id': 'image', 'kind': 'image', 'title': 'Picture', 'path': asset['path']}])
        with self.broker.store.connect(True) as db:
            db.execute("UPDATE activity_revisions SET document='corrupt' WHERE board_id='corrupt'")
            db.execute('DELETE FROM activity_assets WHERE owner=?', (self.owner,))
        # A fresh broker verifies uncached projections; unchanged polls use metadata only.
        self.broker = Broker(Path(self.temporary.name))
        snapshot = self.snapshot()
        boards = {board['id']: board for board in snapshot['activities']}
        self.assertEqual(snapshot['sessions'][0]['id'], self.owner)
        self.assertTrue(Path(boards['healthy']['path']).is_file())
        for identity in ('corrupt', 'missing-asset'):
            self.assertIn('projection_error', boards[identity])
            self.assertNotIn('path', boards[identity])
        self.assertEqual(snapshot['activity_error'], '')
        self.assertIsNotNone(snapshot['activity_policy'])

    def test_invalid_activity_policy_keeps_ordinary_supervisor_snapshot(self):
        self.publish('healthy')
        self.broker.store.set_setting('activity_policy', {'max_cards': 0})
        snapshot = self.snapshot()
        self.assertEqual(snapshot['sessions'][0]['id'], self.owner)
        self.assertEqual(snapshot['activities'], [])
        self.assertIsNone(snapshot['activity_policy'])
        self.assertIn('invalid', snapshot['activity_error'])
        self.assertIn('messages', snapshot)

    def test_missing_activity_table_keeps_ordinary_supervisor_snapshot(self):
        with self.broker.store.connect(True) as db:
            db.execute('DROP TABLE activity_members')
        # A board forces a member lookup against the unavailable activity table.
        with self.broker.store.connect(True) as db:
            db.execute("INSERT INTO activity_boards VALUES('board',?,'Board',1,'now','open',1)", (self.owner,))
        snapshot = self.snapshot()
        self.assertEqual(snapshot['sessions'][0]['id'], self.owner)
        self.assertEqual(snapshot['activities'], [])
        self.assertIn('activity_members', snapshot['activity_error'])


if __name__ == '__main__':
    unittest.main()
