import json
import shutil
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from visor.broker import Broker
from visor.claude import delete_transcript
from visor.platforms import WindowUnavailable, wait_window_closed
from visor.providers import ProviderUnavailable, delete_provider_session
from visor.store import PolicyError


class DeleteCase(unittest.TestCase):
    def setUp(self):
        self.test_root = Path.cwd() / '.cache' / 'delete-tests'
        self.root = self.test_root / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.broker = Broker(self.root)
        self.store = self.broker.store
        self.sid = str(uuid.uuid4())
        self.key = self.store.upsert_session('claude', self.sid, 'A', str(self.root))
        self.other = self.store.upsert_session('codex', 'other', 'B', str(self.root))

    def tearDown(self):
        assert self.root.resolve().is_relative_to(self.test_root.resolve())
        shutil.rmtree(self.root)

    def test_store_purges_related_data_and_blocks_stale_refresh_after_restart(self):
        credential = self.store.issue_credential(self.key)
        self.store.pair(self.key, self.other)
        self.store.enqueue('operator', self.key, 'pending', 'one', operator=True)
        self.store.set_setting('shutdown_targets', [self.key, self.other])
        self.store.set_setting('claude_runtime:' + self.key, {'status': 'idle'})
        self.store.delete_session(self.key)
        self.assertIsNone(self.store.authenticate(credential))
        self.assertEqual(self.store.messages(), [])
        self.assertEqual(self.store.pairs(), [])
        self.assertEqual(self.store.get_setting('shutdown_targets'), [self.other])
        self.assertIsNone(self.store.get_setting('claude_runtime:' + self.key))
        store = Broker(self.root).store
        store.upsert_session('claude', self.sid, 'stale', str(self.root))
        self.assertEqual([s['id'] for s in store.sessions()], [self.other])

    def test_confirmation_and_operator_only(self):
        with patch('visor.broker.delete_provider_session') as delete:
            with self.assertRaises(PolicyError):
                self.broker.dispatch('delete_session', {'session': self.key}, 'operator')
            with self.assertRaises(PolicyError):
                self.broker.dispatch('delete_session', {'session': self.key, 'confirmed': True}, self.key)
            delete.assert_not_called()

    def test_real_claude_history_delete_preserves_adjacent_session_and_project(self):
        project = self.root / 'claude-config' / 'projects' / 'encoded-project'
        project.mkdir(parents=True)
        transcript = project / (self.sid + '.jsonl')
        transcript.write_text('{}\n')
        adjacent = project / (str(uuid.uuid4()) + '.jsonl')
        adjacent.write_text('{}\n')
        artifacts = project / self.sid / 'subagents'
        artifacts.mkdir(parents=True)
        (artifacts / 'child.jsonl').write_text('{}\n')
        with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': str(self.root / 'claude-config')}):
            self.broker.dispatch('delete_session', {'session': self.key, 'confirmed': True}, 'operator')
        self.assertFalse(transcript.exists())
        self.assertFalse(artifacts.exists())
        self.assertTrue(adjacent.exists())
        self.assertTrue(project.is_dir())
        with self.assertRaises(PolicyError):
            self.store.session(self.key)

    def test_provider_failure_preserves_visor_record(self):
        with patch('visor.broker.delete_provider_session', side_effect=ProviderUnavailable('failed')), self.assertRaises(ProviderUnavailable):
            self.broker.dispatch('delete_session', {'session': self.key, 'confirmed': True}, 'operator')
        self.assertEqual(self.store.session(self.key)['name'], 'A')

    def test_close_verified_terminal_before_provider_delete(self):
        self.store.update_session(self.key, managed=True, window_token='unique', heartbeat=time.time(),
                                  window_identity=json.dumps({'hwnd': 42}),
                                  helper_identity=json.dumps({'pid': 42, 'created': 1}))
        calls = []
        adapter = MagicMock()
        adapter.close.side_effect = lambda *a, **k: calls.append('close')
        def delete(session, before):
            before([session['native_id']])
            calls.append('delete')
            return [session['native_id']]
        with patch('visor.broker.windows', return_value=adapter), patch.object(self.broker, 'verified_window'), patch.object(self.broker, 'retired_windows_helper', return_value=True), patch('visor.broker.delete_provider_session', side_effect=delete):
            self.broker.dispatch('delete_session', {'session': self.key, 'confirmed': True}, 'operator')
        self.assertEqual(calls, ['close', 'delete'])
        adapter.close.assert_called_once_with('unique', expected_identity={'hwnd': 42})

    def test_close_failure_prevents_provider_and_registry_delete(self):
        self.store.update_session(self.key, managed=True, window_token='unique', heartbeat=time.time())
        adapter = MagicMock()
        adapter.close.side_effect = WindowUnavailable('still open')
        with patch('visor.broker.windows', return_value=adapter), patch.object(self.broker, 'verified_window'), patch('visor.broker.delete_provider_session', side_effect=lambda session, before: before([session['native_id']])), self.assertRaises(WindowUnavailable):
            self.broker.dispatch('delete_session', {'session': self.key, 'confirmed': True}, 'operator')
        self.assertEqual(self.store.session(self.key)['name'], 'A')

    def test_unassociated_running_session_is_not_guessed(self):
        self.store.update_session(self.key, status='working')
        with self.assertRaisesRegex(PolicyError, 'no verified terminal'):
            self.broker.close_for_delete(self.store.session(self.key))

    def test_observed_orphan_worker_blocks_history_delete(self):
        self.store.set_setting('owned_work:' + self.key, [{'pid': 42, 'created': 1}])
        with patch('visor.broker.retired', return_value=False), self.assertRaisesRegex(PolicyError, 'workers have not stopped'):
            self.broker.wait_owned_stopped(self.store.session(self.key), timeout=0)
        with patch('visor.broker.retired', return_value=True):
            self.broker.close_for_delete(self.store.session(self.key))

    def test_unix_closed_terminal_recovers_only_with_retired_helper(self):
        self.store.update_session(self.key, managed=True, window_token='unique',
                                  helper_identity=json.dumps({'pid': 42, 'created': 1}))
        from visor.platforms import WindowNotFound
        with patch('visor.broker.windows') as adapter, patch('visor.broker.sys.platform', 'linux'), \
                patch('visor.broker.process_identity', side_effect=ProcessLookupError(3, 'gone')):
            adapter.return_value.find.side_effect = WindowNotFound('gone')
            self.broker.close_for_delete(self.store.session(self.key))
        with patch('visor.broker.windows') as adapter, patch('visor.broker.sys.platform', 'linux'), \
                patch('visor.broker.process_identity', side_effect=PermissionError(13, 'denied')):
            adapter.return_value.find.side_effect = WindowNotFound('gone')
            with self.assertRaisesRegex(PolicyError, 'Cannot prove'):
                self.broker.close_for_delete(self.store.session(self.key))

    def test_claude_rejects_non_uuid_before_touching_disk(self):
        with self.assertRaises(ValueError):
            delete_transcript('../other', self.root)

    def test_window_close_timeout_is_not_success(self):
        with self.assertRaises(WindowUnavailable):
            wait_window_closed(lambda: True, timeout=0)

    def test_codex_descendants_include_archived_and_close_before_delete(self):
        events = []
        rpc = MagicMock()
        rpc.call.side_effect = lambda method, params, **kw: (
            events.append(method) or
            ({'data': [{'id': 'grandchild' if params['archived'] else 'child',
                        'parentThreadId': 'child' if params['archived'] else 'root'}]}
             if method == 'thread/list' else {}))
        with patch('visor.providers.CodexRPC') as cls:
            cls.return_value.__enter__.return_value = rpc
            result = delete_provider_session({'provider': 'codex', 'native_id': 'root'},
                                             lambda ids: events.append(('close', ids)))
        self.assertEqual(result, ['child', 'grandchild', 'root'])
        self.assertEqual(events[-2:], [('close', result), 'thread/delete'])

    def test_codex_ignored_filter_cannot_delete_unrelated_records(self):
        rpc = MagicMock()
        rpc.call.return_value = {'data': [{'id': 'unrelated', 'parentThreadId': None}]}
        before = MagicMock()
        with patch('visor.providers.CodexRPC') as cls:
            cls.return_value.__enter__.return_value = rpc
            with self.assertRaisesRegex(ProviderUnavailable, 'ancestry'):
                delete_provider_session({'provider': 'codex', 'native_id': 'root'}, before)
        before.assert_not_called()
        self.assertTrue(all(call.args[0] == 'thread/list' for call in rpc.call.call_args_list))


if __name__ == '__main__':
    unittest.main()
