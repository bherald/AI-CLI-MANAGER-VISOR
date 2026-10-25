"""The operator GUI polls board-scoped status without exposing reply bodies."""
import json
import tempfile
import unittest
from pathlib import Path

from visor.broker import Broker
from visor.store import PolicyError


class ActivityReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.broker = Broker(Path(self.temporary.name))
        self.owner = self.broker.store.upsert_session('codex', 'receipt-owner', 'Owner', self.temporary.name)
        self.peer = self.broker.store.upsert_session('claude', 'receipt-peer', 'Peer', self.temporary.name)
        self.publish('board')
        self.event = self.operator('activity_reply', {'id': 'board', 'card_id': 'question', 'revision': 1,
                                                     'answer': 'PRIVATE ANSWER SENT TO OWNER', 'request_id': 'answer-one'})

    def operator(self, action, params):
        return self.broker.dispatch(action, params, 'operator', role='operator')

    def publish(self, identity):
        document = {'schema_version': 1, 'id': identity, 'title': 'Receipt fixture', 'revision': 1,
                    'cards': [{'id': 'question', 'kind': 'question', 'title': 'Question', 'prompt': 'What next?'}]}
        self.broker.dispatch('activity_publish', {'document': document, 'request_id': identity + '-publish'}, self.owner)

    def receipts(self):
        # Exact action/parameter shape used by ActivityWindow.refresh_receipts.
        return self.operator('activity_replies', {'board_id': 'board'})

    def test_gui_poll_tracks_pending_to_read_with_metadata_only(self):
        initial = self.receipts()
        expected_fields = {'id', 'board_id', 'card_id', 'revision', 'state', 'recipient', 'created'}
        self.assertEqual(len(initial), 1)
        self.assertEqual(set(initial[0]), expected_fields)
        self.assertEqual(initial[0]['id'], self.event['id'])
        self.assertEqual(initial[0]['state'], 'pending')
        self.assertEqual(initial[0]['recipient'], self.owner)
        self.assertNotIn(self.event['answer'], json.dumps(initial))
        self.assertNotIn('prompt_sha256', initial[0])
        agent_events = self.broker.dispatch('activity_replies', {'board_id': 'board'}, self.owner, role='activity')
        self.assertEqual(agent_events, [self.event])
        self.assertEqual(self.receipts()[0]['state'], 'pending')
        self.broker.dispatch('activity_acknowledge', {'event_id': self.event['id']}, self.owner, role='activity')
        self.assertEqual(self.receipts()[0]['state'], 'read')

    def test_operator_must_name_one_board(self):
        for params in ({}, {'board_id': None}, {'board_id': ''}, {'board_id': 7}, {'board_id': 'missing'}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.operator('activity_replies', params)
        self.publish('another')
        second = self.operator('activity_reply', {'id': 'another', 'card_id': 'question', 'revision': 1,
                                                  'answer': 'SECOND PRIVATE ANSWER', 'request_id': 'answer-two'})
        self.assertEqual([event['id'] for event in self.receipts()], [self.event['id']])
        self.assertEqual([event['id'] for event in self.operator('activity_replies', {'board_id': 'another'})], [second['id']])

    def test_agents_remain_recipient_scoped_and_helpers_denied(self):
        for role in ('agent', 'activity'):
            with self.subTest(role=role):
                self.assertEqual(self.broker.dispatch('activity_replies', {}, self.owner, role), [self.event])
                self.assertEqual(self.broker.dispatch('activity_replies', {}, self.peer, role), [])
                with self.assertRaises(PolicyError):
                    self.broker.dispatch('activity_replies', {'board_id': 'board'}, self.peer, role)
        with self.assertRaises(PolicyError):
            self.broker.dispatch('activity_replies', {'board_id': 'board'}, self.owner, role='helper')
        self.broker.store.toggle(True)
        for actor in (self.owner, self.peer):
            self.broker.store.participate(actor, True)
        self.broker.store.pair(self.owner, self.peer)
        self.broker.dispatch('activity_share', {'id': 'board', 'members': [self.peer], 'request_id': 'share'}, self.owner)
        self.assertEqual(self.broker.dispatch('activity_replies', {'board_id': 'board'}, self.peer), [])
        with self.assertRaises(PolicyError):
            self.broker.dispatch('activity_acknowledge', {'event_id': self.event['id']}, self.peer)
        self.broker.store.pair(self.owner, self.peer, False)
        with self.assertRaises(PolicyError):
            self.broker.dispatch('activity_replies', {'board_id': 'board'}, self.owner)
        self.assertEqual(self.receipts()[0]['state'], 'pending')


if __name__ == '__main__':
    unittest.main()
