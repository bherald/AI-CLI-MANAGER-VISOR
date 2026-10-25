"""Image plugins must outlive individual HTTP request threads without a Qt app."""
import json
import subprocess
import sys
import unittest


class ActivityImageThreadTests(unittest.TestCase):
    def test_successive_request_threads_keep_stable_plugins_without_application(self):
        script = r'''
import base64, json, tempfile, threading
from pathlib import Path
from PySide6.QtCore import QCoreApplication, qInstallMessageHandler
from visor.activity_store import ActivityService
from visor.store import Store
warnings, failures = [], []
qInstallMessageHandler(lambda kind, context, message: warnings.append(message))
with tempfile.TemporaryDirectory() as root:
    service = ActivityService(Store(Path(root)))
    image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1XcAAAAASUVORK5CYII=')
    def request():
        try:
            service._check_image(image, '.png', service.policy())
        except Exception as exc:
            failures.append(str(exc))
    for _ in range(3):
        worker = threading.Thread(target=request)
        worker.start()
        worker.join()
print(json.dumps({'application': QCoreApplication.instance() is not None,
                  'warnings': warnings, 'failures': failures}))
'''
        process = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, check=False, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertFalse(result['application'])
        self.assertEqual(result['failures'], [])
        self.assertFalse(any('moveToThread' in warning for warning in result['warnings']), result['warnings'])


if __name__ == '__main__':
    unittest.main()
