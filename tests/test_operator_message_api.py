import io
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from visor.broker import Broker, Client, handler_for
from visor.store import PolicyError


class OperatorMessageAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.broker = Broker(self.root)
        self.agent = self.broker.store.upsert_session('codex', str(uuid.uuid4()), 'Synthetic', str(self.root))
        self.other = self.broker.store.upsert_session('claude', str(uuid.uuid4()), 'Other', str(self.root))
        self.tokens = {role: self.broker.store.issue_credential(self.agent, role) for role in ('agent', 'helper', 'activity')}
        self.tokens['operator'] = self.broker.token
        self.tokens['other'] = self.broker.store.issue_credential(self.other, 'agent')
        self.parent = self.broker.store.enqueue('operator', self.agent, 'Question', str(uuid.uuid4()), operator=True)
        self.broker.store.claim(self.agent)

    def http(self, action, params, role='operator', *, body=None, hint=None, length=None, stream=None):
        handler = handler_for(self.broker).__new__(handler_for(self.broker))
        raw = json.dumps({'action': action, 'params': params}).encode() if body is None else body
        handler.path = '/rpc'
        handler.headers = {'Authorization': 'Bearer ' + self.tokens.get(role, 'invalid'),
                           'Content-Length': str(len(raw) if length is None else length)}
        if hint:
            handler.headers['X-Visor-Action'] = hint
        handler.rfile = io.BytesIO(raw) if stream is None else stream
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_POST()
        return handler.send_response.call_args.args[0], json.loads(handler.wfile.getvalue())

    def args(self, body='Recipient answer'):
        return {'message': self.parent['id'], 'body': body, 'request_id': str(uuid.uuid4())}

    def test_actual_http_auth_roles_reply_and_exact_lookup_contract(self):
        params = self.args()
        for role in ('operator', 'helper', 'activity', 'other', 'invalid'):
            status, result = self.http('operator_reply', params, role)
            self.assertEqual(status, 400, (role, result))
        status, result = self.http('operator_reply', params, 'agent')
        self.assertEqual(status, 200, result)
        reply = result['result']
        status, found = self.http('message_get', {'message': self.parent['id']})
        self.assertEqual(status, 200, found)
        self.assertEqual(found['result']['reply'], reply)
        self.assertEqual(found['result']['state'], 'sending')
        self.assertEqual(self.http('operator_reply', params, 'agent')[1]['result'], reply)
        for role in ('agent', 'helper', 'activity', 'other', 'invalid'):
            self.assertEqual(self.http('message_get', {'message': self.parent['id']}, role)[0], 400)
        status, ping = self.http('ping', {})
        self.assertEqual(status, 200)
        self.assertEqual(ping['result']['capabilities'], ['message_get', 'operator_reply'])
        self.assertEqual(ping['result']['limits'], {'message_max_chars': 16000, 'max_rpc_bytes': 65536})

    def test_new_actions_reject_extra_fields_wrong_types_and_inferred_roles(self):
        for params in [self.args() | {'sender': self.other}, self.args() | {'recipient': 'operator'},
                       self.args() | {'role': 'operator'}, self.args() | {'request_id': 'not-canonical'},
                       self.args() | {'body': 42}, {}, [], None]:
            with self.subTest(params=params), self.assertRaises(PolicyError):
                self.broker.dispatch('operator_reply', params, self.agent, 'agent')
        for params in [{'message': self.parent['id'], 'extra': True}, {'message': None}, {}]:
            with self.subTest(params=params), self.assertRaises(PolicyError):
                self.broker.dispatch('message_get', params, 'operator', 'operator')
        for action, params, identity in [('message_get', {'message': self.parent['id']}, 'operator'),
                                          ('operator_reply', self.args(), self.agent)]:
            with self.subTest(action=action), self.assertRaises(PolicyError):
                self.broker.dispatch(action, params, identity)
        for body in (b'[]', b'null', b'{"action":"operator_reply","params":null}'):
            self.assertEqual(self.http('', {}, 'agent', body=body)[0], 400)

    def test_reply_never_leaks_through_snapshot_fixed_settings_or_other_roles(self):
        secret = 'PRIVATE FINAL REPLY SENTINEL'
        self.http('operator_reply', self.args(secret), 'agent')
        with patch.object(self.broker, 'wrap_up_progress', return_value=[]):
            status, result = self.http('snapshot', {})
        self.assertEqual(status, 200)
        self.assertNotIn(secret, json.dumps(result))
        for action in ('ui_state', 'message_draft', 'terminal_appearance', 'claude_options', 'backup_options'):
            status, result = self.http(action, {})
            self.assertEqual(status, 200, result)
            self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(self.http('get_setting', {'key': 'operator_reply:' + self.parent['id']})[0], 400)

    def test_actual_unicode_serialization_boundary_rejects_without_losing_draft(self):
        body = '😀' * 16000
        params = self.args(body)
        envelope = {'action': 'operator_reply', 'params': params}
        escaped = json.dumps(envelope).encode()
        literal = json.dumps(envelope, ensure_ascii=False).encode()
        self.assertGreater(len(escaped), 65536)
        self.assertLess(len(literal), 65536)
        status, rejected = self.http('operator_reply', params, 'agent', body=escaped)
        self.assertEqual(status, 400)
        self.assertIn('65536', rejected['error'])
        self.assertIn('retain', rejected['error'])
        self.assertIsNone(self.broker.store.message_get(self.parent['id'], identity='operator', role='operator')['reply'])
        self.assertEqual(params['body'], body)
        status, result = self.http('operator_reply', params, 'agent', body=literal)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['result']['body'], body)
        # Exactly the declared ordinary RPC limit is accepted; one extra byte fails.
        exact = literal + b' ' * (65536-len(literal))
        self.assertEqual(self.http('operator_reply', params, 'agent', body=exact)[0], 200)
        self.assertEqual(self.http('operator_reply', params, 'agent', body=exact+b' ')[0], 400)
        stream = Mock()
        self.assertEqual(self.http('operator_reply', {}, 'agent', length=65537, stream=stream)[0], 400)
        stream.read.assert_not_called()
        # Pretending to be a large Prism operation does not lift the reply limit.
        self.assertEqual(self.http('operator_reply', params, 'agent', body=escaped, hint='activity_asset')[0], 400)

    def test_same_identity_explicit_client_fallback_never_uses_operator_token(self):
        (self.root/'broker.json').write_text(json.dumps({'port': 12345, 'token': self.broker.token}))
        params = self.args()
        opener = Mock()
        def open_request(request, **kwargs):
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + self.tokens['agent'])
            wire = json.loads(request.data)
            principal = self.broker.store.authenticate_principal(request.get_header('Authorization').removeprefix('Bearer '))
            result = self.broker.dispatch(wire['action'], wire['params'], *principal)
            response = Mock()
            response.__enter__ = Mock(return_value=io.BytesIO(json.dumps({'result': result}).encode()))
            response.__exit__ = Mock(return_value=False)
            return response
        opener.open.side_effect = open_request
        with patch('visor.broker.urllib.request.build_opener', return_value=opener):
            result = Client(self.root, token=self.tokens['agent']).call('operator_reply', **params)
        self.assertEqual(result['sender'], self.agent)
        self.assertEqual(result['recipient'], 'operator')

    def test_invalid_explicit_client_tokens_cannot_select_operator_fallback(self):
        for token in ('', ' ', False, 0, []):
            with self.subTest(token=token), self.assertRaises(PolicyError):
                Client(self.root, token=token)
