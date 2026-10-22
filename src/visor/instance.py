"""One supervisor window per canonical data directory, with local activation IPC."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import __version__
from .broker import BUILD, BrokerLock
from .platforms import allow_foreground


class SupervisorInstance(QObject):
    activated = Signal()

    def __init__(self, root):
        super().__init__()
        self.root = root.resolve()
        identity = os.path.normcase(str(self.root)).encode()
        self.name = "visor-ui-" + hashlib.sha256(identity).hexdigest()[:32]
        self.lock = None
        self.notice = ""
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self.accept)

    def start(self):
        try:
            self.lock = BrokerLock(self.root / "supervisor.lock")
        except RuntimeError:
            self.notify_existing()
            return False
        # Only the lock owner may clear an endpoint left behind by a crash.
        QLocalServer.removeServer(self.name)
        if not self.server.listen(self.name):
            self.close()
            raise RuntimeError("Cannot start supervisor activation service: " + self.server.errorString())
        return True

    def notify_existing(self):
        deadline = time.monotonic() + 5
        probe = True
        while time.monotonic() < deadline:
            socket = QLocalSocket()
            socket.connectToServer(self.name)
            if socket.waitForConnected(250):
                hello = self.read_line(socket, 2000)
                if not hello.strip().isdigit():
                    break
                if probe:
                    socket.write(b"info\n")
                    socket.flush()
                    info = self.read_line(socket, 1000)
                    if not info:
                        # Older owners support activation only. Reconnect using that protocol.
                        probe = False
                        self.notice = f"An older VISOR supervisor is already running. Its window was activated. Exit it and reopen VISOR {__version__} to load this download."
                        socket.abort()
                        continue
                    try:
                        running = json.loads(info)
                    except (ValueError, TypeError):
                        raise RuntimeError("Existing VISOR returned invalid version information") from None
                    if running.get("version") != __version__ or running.get("build") != BUILD:
                        self.notice = f"VISOR {running.get('version', 'unknown')} ({running.get('build', 'unknown')}) is already running. Its window was activated; this download ({__version__}, {BUILD}) is not loaded. Exit the existing supervisor and reopen the new executable."
                allow_foreground(int(hello))
                socket.write(b"activate\n")
                socket.flush()
                if self.read_line(socket, 4000) == b"ok\n":
                    socket.disconnectFromServer()
                    return
                break
            time.sleep(0.05)
        raise RuntimeError("VISOR is already running but did not respond. Check its existing window; a second supervisor was not opened.")

    @staticmethod
    def read_line(socket, timeout):
        deadline = time.monotonic() + timeout / 1000
        while not socket.canReadLine():
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0 or socket.bytesAvailable() >= 8192 or not socket.waitForReadyRead(remaining):
                return b""
        return bytes(socket.readLine(8192))

    def accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.setReadBufferSize(64)
            socket.disconnected.connect(socket.deleteLater)
            socket.write(f"{os.getpid()}\n".encode("ascii"))
            socket.flush()
            self.read_activation(socket)
            socket.readyRead.connect(lambda s=socket: self.read_activation(s))

    def read_activation(self, socket):
        if not socket.canReadLine():
            if socket.bytesAvailable() >= 64:
                socket.disconnectFromServer()
            return
        request = bytes(socket.readLine(64))
        if request == b"info\n":
            payload = {"version": __version__, "build": BUILD, "executable": sys.executable}
            socket.write((json.dumps(payload) + "\n").encode())
            socket.flush()
            return
        if request == b"activate\n":
            self.activated.emit()
            socket.write(b"ok\n")
            socket.flush()
        socket.disconnectFromServer()

    def close(self):
        self.server.close()
        if self.lock is not None:
            self.lock.close()
            self.lock = None
