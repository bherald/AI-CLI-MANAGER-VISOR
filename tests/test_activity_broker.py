import base64
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from visor.broker import Broker, Client, activity_request_limit, handler_for
from visor.store import PolicyError


def document():
    return {'schema_version': 1, 'id': 'prism', 'title': 'Prism', 'revision': 1,
            'cards': [{'id': 'ask', 'kind': 'question', 'title': 'Question', 'prompt': 'Next?'}]}


class ActivityBrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.broker = Broker(self.root)
        self.session = self.broker.store.upsert_session('codex', 'example', 'Example', str(self.root))
        self.tokens = {role: self.broker.store.issue_credential(self.session, role) for role in ('agent', 'helper', 'activity')}

    def rpc(self, action, params, role='activity'):
        identity = 'operator' if role == 'operator' else self.session
        return self.broker.dispatch(action, params, identity, role)

    def http(self, action, params=None, *, token=None, hint=None, body=None, declared=None, stream=None):
        body = json.dumps({'action': action, 'params': params or {}}).encode() if body is None else body
        handler = handler_for(self.broker).__new__(handler_for(self.broker))
        handler.path = '/rpc'
        handler.headers = {'Authorization': 'Bearer ' + (self.tokens['activity'] if token is None else token),
                           'Content-Length': str(len(body) if declared is None else declared)}
        if hint is not None:
            handler.headers['X-Visor-Action'] = hint
        handler.rfile = io.BytesIO(body) if stream is None else stream
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_POST()
        return handler.send_response.call_args.args[0], json.loads(handler.wfile.getvalue())

    def test_activity_roles_and_identity_come_from_credential(self):
        for role in ('agent', 'activity'):
            self.assertIn('max_document_bytes', self.rpc('activity_limits', {}, role))
        with self.assertRaises(PolicyError):
            self.rpc('activity_limits', {}, 'helper')
        for action in ('snapshot', 'send', 'receive', 'stop', 'heartbeat', 'activity_bind', 'activity_reply', 'activity_edit'):
            with self.subTest(action=action), self.assertRaises(PolicyError):
                self.rpc(action, {})
        for action in ('activity_publish', 'activity_asset', 'activity_acknowledge'):
            with self.subTest(action=action), self.assertRaises(PolicyError):
                self.rpc(action, {}, 'operator')
        published = self.rpc('activity_publish', {'document': document(), 'request_id': 'pub1', 'actor': 'claude:other'})
        self.assertEqual(published['owner'], self.session)
        with patch.object(self.broker, 'wrap_up_progress', return_value=[]):
            snapshot = self.rpc('snapshot', {}, 'operator')
        self.assertEqual(snapshot['activities'][0]['id'], 'prism')
        self.assertIn('path', snapshot['activities'][0])
        self.assertNotIn('path', self.rpc('activity_get', {'id': 'prism'}))

    def test_scoped_binding_reuse_and_revocation_preserve_other_credentials(self):
        with self.assertRaises(PolicyError):
            self.rpc('activity_bind', {'session': self.session}, 'operator')
        params = {'session': self.session, 'confirmed': True}
        bound = self.rpc('activity_bind', params, 'operator')
        path = Path(bound['credential_file'])
        config = json.loads(path.read_text())
        self.assertNotIn('token', bound)
        self.assertNotIn(config['token'], json.dumps(bound))
        self.assertEqual(self.broker.store.authenticate_principal(config['token']), (self.session, 'activity'))
        stamp = path.stat().st_mtime_ns
        self.assertEqual(self.rpc('activity_bind', params, 'operator'), bound)
        self.assertEqual(path.stat().st_mtime_ns, stamp)
        if os.name != 'nt':
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        revoked = self.rpc('activity_revoke', params, 'operator')
        self.assertTrue(revoked['revoked'])
        self.assertFalse(path.exists())
        self.assertIsNone(self.broker.store.authenticate_principal(config['token']))
        for role in ('agent', 'helper'):
            self.assertEqual(self.broker.store.authenticate_principal(self.tokens[role]), (self.session, role))

    def test_binding_refuses_symlink(self):
        (self.root / 'activity-access').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(PolicyError):
            self.rpc('activity_bind', {'session': self.session, 'confirmed': True}, 'operator')

    def test_http_authentication_and_size_precede_body_read(self):
        stream = Mock()
        for token, hint, declared in [('invalid', 'activity_asset', 100000),
                                      (self.tokens['helper'], 'activity_asset', 100000),
                                      (self.tokens['activity'], 'send', 100000),
                                      (self.tokens['activity'], 'activity_asset', activity_request_limit(self.broker.activities.policy()) + 1)]:
            with self.subTest(token=token[:4], hint=hint):
                status, value = self.http('activity_asset', token=token, hint=hint, declared=declared, stream=stream)
                self.assertEqual(status, 400)
                self.assertIn('error', value)
                stream.read.assert_not_called()

    def test_http_large_image_and_nonactivity_action_rejection(self):
        raw = os.urandom(256 * 256 * 4)
        image = QImage(raw, 256, 256, QImage.Format.Format_RGBA8888)
        output = QByteArray()
        buffer = QBuffer(output)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(image.save(buffer, 'PNG'))
        params = {'name': 'large.png', 'data_base64': base64.b64encode(bytes(output)).decode()}
        self.assertGreater(len(params['data_base64']), 65536)
        status, result = self.http('activity_asset', params, hint='activity_asset')
        self.assertEqual(status, 200, result)
        self.assertEqual(result['result']['bytes'], len(output))
        status, _ = self.http('activity_asset', params)
        self.assertEqual(status, 400)
        status, _ = self.http('send', {'padding': 'x' * 70000}, hint='activity_asset')
        self.assertEqual(status, 400)
        self.broker.store.set_setting('activity_policy', {'max_image_bytes': 10})
        status, _ = self.http('activity_asset', params, hint='activity_asset')
        self.assertEqual(status, 400)

    def test_reply_and_control_dispatch_contract(self):
        self.rpc('activity_publish', {'document': document(), 'request_id': 'pub1'})
        controlled = self.rpc('activity_control', {'id': 'prism', 'action': 'close', 'request_id': 'close1'})
        self.assertEqual(controlled['view_action'], 'close')
        event = self.rpc('activity_reply', {'id': 'prism', 'card_id': 'ask', 'revision': 1, 'answer': 'Next step', 'request_id': 'answer1'}, 'operator')
        self.assertEqual(self.rpc('activity_replies', {}), [event])
        self.assertEqual(self.rpc('activity_acknowledge', {'event_id': event['id']})['state'], 'read')

    def test_client_supports_action_parameter_and_action_header(self):
        (self.root / 'broker.json').write_text(json.dumps({'port': 12345, 'token': 'operator-secret'}))
        response = io.BytesIO(b'{"result": true}')
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch('visor.broker.urllib.request.build_opener', return_value=opener):
            self.assertTrue(Client(self.root, token=self.tokens['activity']).call('activity_control', id='prism', action='open', request_id='req'))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header('X-visor-action'), 'activity_control')
        self.assertEqual(json.loads(request.data)['params']['action'], 'open')


if __name__ == '__main__':
    unittest.main()
