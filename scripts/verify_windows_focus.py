"""Visible native acceptance: actual Focus button and a disposable Windows Terminal.

No provider is launched or messaged. Only the terminal created here is closed.
"""
import json
import os
import sys
import time
import uuid
from ctypes import wintypes
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
os.environ["QT_QPA_PLATFORM"] = "windows"

from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from visor.broker import Broker
from visor.platforms import WindowNotFound, Windows, terminal_launch
from visor.ui import Window
from visor.win_identity import process_identity, window_identity


def wait_for(predicate, message, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QTest.qWait(30)
        if predicate():
            return
    raise AssertionError(message)


app = QApplication([])
data = root / ".cache" / "native-focus" / uuid.uuid4().hex
data.mkdir(parents=True)
broker = Broker(data)
token = "VISOR-focus-test-" + uuid.uuid4().hex
adapter = Windows()
hwnd = None
identity = None
window = Window(data)
window.timer.stop()


class Client:
    def call(self, action, **params):
        return broker.dispatch(action, params, "operator")


try:
    fixture = data / "terminal.py"
    fixture.write_text("import time\ntime.sleep(900)\n")
    terminal_launch([sys.executable, str(fixture)], str(root), token)

    def find_terminal():
        global hwnd
        try:
            hwnd = adapter.find(token)
            return True
        except WindowNotFound:
            return False

    wait_for(find_terminal, "Disposable Windows Terminal did not open")
    identity = window_identity(hwnd)
    key = broker.store.upsert_session("codex", str(uuid.uuid4()), "Native focus acceptance", str(root))
    broker.store.update_session(key, managed=1, window_token=token, heartbeat=time.time(),
                                process_id=os.getpid(), helper_identity=json.dumps(process_identity(os.getpid())),
                                status="idle")
    window.client = Client()
    window.snapshot = window.client.call("snapshot")
    window.render_sessions()
    window.setWindowTitle("VISOR focus acceptance - click this window once")
    window.status.setText("FOCUS TEST: Click this window once. It will close automatically after testing.")
    window.show()
    print("Click the VISOR focus acceptance window once to begin.", flush=True)
    results = []
    for mode, show in (("normal", 9), ("minimized", 6), ("maximized", 3)):
        print("Testing " + mode, flush=True)
        adapter.api.ShowWindow(hwnd, show)
        QTest.qWait(150)
        window.raise_()
        window.activateWindow()
        adapter.api.SetForegroundWindow(int(window.winId()))
        wait_for(lambda: adapter.api.GetForegroundWindow() == int(window.winId()),
                 "Test supervisor could not obtain foreground for a genuine activation test", timeout=600)
        broker.store.update_session(key, heartbeat=time.time())
        button = next(b for b in window.findChildren(QPushButton) if b.text() == "Focus")
        errors = []
        with patch("visor.ui.QMessageBox.warning", side_effect=lambda *args, errors=errors: errors.append(args[-1])):
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
            wait_for(lambda errors=errors: adapter.api.GetForegroundWindow() == hwnd or errors, "Focus button did not activate exact terminal")
        assert not errors, errors
        assert window_identity(hwnd) == identity
        assert not adapter.api.IsIconic(hwnd)
        if mode == "maximized":
            adapter.api.IsZoomed.argtypes = adapter.api.IsIconic.argtypes
            assert adapter.api.IsZoomed(hwnd), "Focus lost maximized state"
        results.append(mode)
        print("Passed " + mode, flush=True)
        QTest.qWait(150)
    print(json.dumps({"passed": results, "hwnd": hwnd, "real_foreground_verified": True}))
finally:
    window.allow_close = True
    window.close()
    window.pool.waitForDone(5000)
    app.processEvents()
    if hwnd and identity and window_identity(hwnd) == identity:
        adapter.api.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        adapter.api.PostMessageW(hwnd, 0x10, 0, 0)
