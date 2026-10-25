"""Removed Prism widgets must never depend on the thread that collects Python cycles."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


class ActivityQtLifetimeCase(unittest.TestCase):
    def test_retired_widgets_are_deleted_on_gui_thread_before_late_callbacks(self):
        script = r'''
import gc, json, tempfile, threading, weakref
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QLabel
from shiboken6 import isValid
from visor.activity import ActivityManager
from visor.activity_document import ActivityPolicy
app = QApplication([])
main_thread = threading.get_ident()
gc.disable()
destroyed_on = []
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    source = root / 'board.json'
    source.write_text(json.dumps({'schema_version': 1, 'id': 'board', 'title': 'Fixture', 'revision': 1,
                                 'cards': [{'id': 'q', 'kind': 'question', 'title': 'Question', 'prompt': 'Next?'}]}))
    supervisor = SimpleNamespace(root=root, client=None, status=QLabel())
    manager = ActivityManager(supervisor)
    metadata = {'id': 'board', 'title': 'Fixture', 'path': str(source), 'revision': 1, 'view_seq': 1, 'view_action': 'open'}
    manager.update([metadata], asdict(ActivityPolicy()))
    app.processEvents()
    window = manager.windows['board']
    window.destroyed.connect(lambda: destroyed_on.append(threading.get_ident()), Qt.ConnectionType.DirectConnection)
    reference = weakref.ref(window)
    manager.remove_window('board')
    del window
    # Reproduce the dangerous ordering: collection happens on a worker before
    # the GUI has had a chance to process its deferred-delete event.
    worker = threading.Thread(target=gc.collect)
    worker.start()
    worker.join()
    assert not destroyed_on, ('widget destruction ran before GUI deferred delete', destroyed_on, main_thread)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    assert destroyed_on == [main_thread], (destroyed_on, main_thread)
    assert reference() is None or not isValid(reference())
    # A retained asynchronous reply callback may outlive all C++ child widgets.
    window = manager.open(metadata)
    app.processEvents()
    callbacks = []
    window.submit = lambda operation, params, callback: callbacks.append(callback)
    window.cards['q'].reply.setPlainText('Retained draft')
    window.cards['q'].save_answer()
    window.save_layout()
    layout = window.layout_path
    assert layout.exists()
    manager.deleted('board')
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    assert not isValid(window)
    callbacks[0]({'id': 'late-event'}, None)
    window.schedule_layout_save()
    window.save_layout()
    app.processEvents()
    assert not layout.exists()
    supervisor.status.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
print(json.dumps({'gui_thread': main_thread, 'destroyed_on': destroyed_on,
                  'late_callback_ignored': True, 'deleted_draft_remains_absent': True}))
'''
        # Match whichever source tree is under test, including isolated overlays.
        import visor.activity
        source = Path(visor.activity.__file__).resolve().parents[1]
        environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(source)}
        process = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script], capture_output=True,
                                 text=True, check=False, timeout=20, env=environment)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["destroyed_on"], [result["gui_thread"]])
        self.assertTrue(result["late_callback_ignored"])
        self.assertTrue(result["deleted_draft_remains_absent"])
