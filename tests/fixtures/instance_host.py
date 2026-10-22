import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer

import visor.instance as instance_module
from visor.instance import SupervisorInstance

root = Path(sys.argv[1])
app = QCoreApplication([])
instance = SupervisorInstance(root)
mode = sys.argv[2] if len(sys.argv) > 2 else ""
if mode == "old-version":
    instance_module.__version__ = "0.0.0-test"
if mode == "legacy":
    def legacy(socket):
        if socket.canReadLine():
            if bytes(socket.readLine(64)) == b"activate\n":
                instance.activated.emit()
                socket.write(b"ok\n")
                socket.flush()
            socket.disconnectFromServer()
    instance.read_activation = legacy
if not instance.start():
    print("forwarded", flush=True)
    print(instance.notice, flush=True)
    raise SystemExit(0)
instance.activated.connect(lambda: (root / "activated").write_text("yes"))
(root / "ready").write_text("yes")
timer = QTimer()
timer.timeout.connect(lambda: app.quit() if (root / "stop").exists() else None)
timer.start(50)
try:
    app.exec()
finally:
    instance.close()
