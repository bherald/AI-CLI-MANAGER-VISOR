"""One supervisor window per canonical data directory, with local activation IPC."""
from __future__ import annotations

import hashlib
import os
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .broker import BrokerLock
from .platforms import allow_foreground


class SupervisorInstance(QObject):
    activated = Signal()

    def __init__(self, root):
        super().__init__()
        self.root = root.resolve()
        identity = os.path.normcase(str(self.root)).encode()
        self.name = "visor-ui-" + hashlib.sha256(identity).hexdigest()[:32]
        self.lock = None
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
        while time.monotonic() < deadline:
            socket = QLocalSocket()
            socket.connectToServer(self.name)
            if socket.waitForConnected(250):
                hello = self.read_line(socket, 2000)
                if not hello.strip().isdigit():
                    break
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
            if remaining <= 0 or socket.bytesAvailable() >= 64 or not socket.waitForReadyRead(remaining):
                return b""
        return bytes(socket.readLine(64))

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
        if bytes(socket.readLine(64)) == b"activate\n":
            self.activated.emit()
            socket.write(b"ok\n")
            socket.flush()
        socket.disconnectFromServer()

    def close(self):
        self.server.close()
        if self.lock is not None:
            self.lock.close()
            self.lock = None
