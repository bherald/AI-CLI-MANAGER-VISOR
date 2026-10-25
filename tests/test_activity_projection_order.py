"""A late failure cannot replace a concurrent successful repair at the same revision."""
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from visor.activity_store import ActivityService
from visor.store import Store


class ProjectionOutcomeOrderTests(unittest.TestCase):
    def test_late_snapshot_failure_keeps_newer_same_revision_ready_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary))
            owner = store.upsert_session('codex', 'cache-order', 'Owner', temporary)
            service = ActivityService(store)
            service.publish(owner, {'schema_version': 1, 'id': 'board', 'title': 'Board', 'revision': 1,
                                    'cards': [{'id': 'text', 'kind': 'text', 'title': 'Text', 'html': 'Content'}]}, 'publish')
            # New broker/cache: an initial snapshot starts a slow failing attempt.
            service = ActivityService(store)
            entered, release = threading.Event(), threading.Event()
            first = threading.Lock()
            original = service._owner_dir
            def delayed_failure(actor):
                if first.acquire(blocking=False):
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError('test repair did not complete')
                    raise OSError('older attempt failed late')
                return original(actor)
            with ThreadPoolExecutor(max_workers=1) as workers, patch.object(service, '_owner_dir', side_effect=delayed_failure):
                snapshot = workers.submit(service.list, None)
                self.assertTrue(entered.wait(2))
                try:
                    ready = service.get(None, 'board')
                    self.assertEqual(ready['projection']['state'], 'ready')
                finally:
                    release.set()
                rows = snapshot.result(timeout=2)
            self.assertEqual(rows[0]['projection']['state'], 'ready')
            self.assertNotIn('projection_error', rows[0])
            self.assertTrue(Path(rows[0]['path']).is_file())
            with patch.object(service, '_projection', side_effect=AssertionError('ready cache must remain metadata-only')):
                self.assertEqual(service.list(None)[0]['projection']['state'], 'ready')


if __name__ == '__main__':
    unittest.main()
