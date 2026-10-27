import io
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from visor import mcp
from visor.broker import Broker


class OperatorReplyMCPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.broker = Broker(self.root)
        self.agent = self.broker.store.upsert_session('claude', str(uuid.uuid4()), 'Synthetic', str(self.root))
        self.parent = self.broker.store.enqueue('operator', self.agent, 'Question', str(uuid.uuid4()), operator=True)
        self.broker.store.claim(self.agent)
        self.client = Mock()
        self.client.call.side_effect = lambda action, **args: self.broker.dispatch(action, args, self.agent, 'agent')

    def request(self, body='Final answer'):
        return {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'operator_reply', 'arguments': {
            'message': self.parent['id'], 'body': body, 'request_id': str(uuid.uuid4())}}}

    def test_discovery_instructions_and_reply_preserve_ack_completion_separation(self):
        tools = mcp.handle({'method': 'tools/list'}, self.client)['tools']
        schema = next(t for t in tools if t['name']=='operator_reply')['inputSchema']
        self.assertEqual(set(schema['properties']), {'message', 'body', 'request_id'})
        self.assertEqual(set(schema['required']), set(schema['properties']))
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['properties']['body']['maxLength'], 16000)
        self.assertNotIn('message_get', {t['name'] for t in tools})
        instructions = mcp.handle({'method': 'initialize'}, self.client)['instructions']
        for phrase in ('one final reply', 'original recipient credential', 'never substitute operator credentials', 'completed=true only'):
            self.assertIn(phrase, instructions)
        result = mcp.handle(self.request(), self.client)
        reply = json.loads(result['content'][0]['text'])
        self.assertEqual(reply['sender'], self.agent)
        self.assertEqual(self.broker.store.message(self.parent['id'])['state'], 'sending')

    def test_schema_and_actual_store_reject_wrong_payload_without_tool_trust(self):
        request = self.request()
        request['params']['arguments']['recipient'] = 'operator'
        with self.assertRaises(ValueError):
            mcp.handle(request, self.client)
        for key, value in [('body', None), ('request_id', 'bad'), ('message', 12)]:
            request = self.request()
            request['params']['arguments'][key] = value
            self.assertTrue(mcp.handle(request, self.client)['isError'])
        missing = Mock()
        missing.call.side_effect = ValueError('Unknown operator_reply capability')
        self.assertTrue(mcp.handle(self.request(), missing)['isError'])
        self.assertEqual(missing.call.call_count, 1)  # No fallback send or operator substitution.

    def test_actual_mcp_byte_frames_keep_full_unicode_or_precisely_refuse(self):
        request = self.request('😀'*16000)
        accepted = json.dumps(request, ensure_ascii=False).encode()+b'\n'
        rejected = json.dumps(request).encode()+b'\n'
        self.assertLess(len(accepted), 65536)
        self.assertGreater(len(rejected), 65536)
        for raw, succeeds in [(rejected, False), (accepted, True)]:
            output = []
            with patch('sys.stdin', io.TextIOWrapper(io.BytesIO(raw), encoding='utf-8')):
                mcp.serve_requests(self.client, False, output.append, threading.Event())
            self.assertEqual(len(output), 1)
            if succeeds:
                self.assertEqual(json.loads(output[0]['result']['content'][0]['text'])['body'], '😀'*16000)
            else:
                self.assertIn('65536', output[0]['error']['message'])
                self.assertIn('retain', output[0]['error']['message'])
        self.assertEqual(self.broker.store.message(self.parent['id'])['state'], 'sending')

    def test_serve_existing_agent_credential_has_no_default_operator_fallback(self):
        token = self.broker.store.issue_credential(self.agent, 'agent')
        credential = self.root/'existing-agent.json'
        credential.write_text(json.dumps({'root':str(self.root), 'token':token, 'session':self.agent}))
        with patch('visor.mcp.Client') as client, patch('visor.mcp.serve_requests') as serve:
            mcp.serve(credential)
        client.assert_called_once_with(self.root, token)
        self.assertIs(serve.call_args.args[0], client.return_value)
        self.assertEqual(self.broker.store.authenticate_principal(token), (self.agent, 'agent'))

    def test_invalid_credential_file_never_constructs_operator_client(self):
        credential = self.root/'invalid-agent.json'
        for token in (None, '', '  ', False, 17, []):
            credential.write_text(json.dumps({'root': str(self.root), 'token': token}))
            with self.subTest(token=token), patch('visor.mcp.Client') as client, self.assertRaises(ValueError):
                mcp.serve(credential)
            client.assert_not_called()
