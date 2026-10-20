import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer

from visor.instance import SupervisorInstance

root = Path(sys.argv[1])
app = QCoreApplication([])
instance = SupervisorInstance(root)
if not instance.start():
    print("forwarded", flush=True)
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
