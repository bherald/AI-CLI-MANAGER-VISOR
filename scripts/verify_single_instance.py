"""Check two launches of a packaged UI, using isolated data and no provider on PATH."""
import argparse
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from visor.broker import Client
from visor.paths import data_dir
from visor.process import hidden_options

parser = argparse.ArgumentParser()
parser.add_argument("executable", type=Path)
exe = parser.parse_args().executable.resolve()
data = root / ".cache" / "single-instance-acceptance" / uuid.uuid4().hex
data = data_dir(str(data))
empty_path = data / "empty-path"
empty_path.mkdir()
env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PATH": str(empty_path), "VISOR_SHELL_PATH": "0"}
for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
    env.pop(name, None)
command = [str(exe), "--data-dir", str(data)]
first = subprocess.Popen(command, env=env, **hidden_options())
try:
    for _ in range(100):
        if (data / "supervisor.lock").exists():
            break
        if first.poll() is not None:
            raise RuntimeError("First UI exited during startup")
        time.sleep(0.1)
    for _ in range(100):
        try:
            Client(data).call("ping")
            break
        except (OSError, ValueError):
            time.sleep(0.1)
    else:
        raise RuntimeError("Isolated broker did not start")
    second = subprocess.run(command, env=env, check=False, timeout=12, **hidden_options())
    assert second.returncode == 0
    assert first.poll() is None
    print("PASS: second packaged UI activated the first and exited")
finally:
    if first.poll() is None:
        first.terminate()  # This isolated test UI only; the OS releases its lock.
    first.wait(timeout=10)
    try:
        Client(data).call("stop")
    except (OSError, ValueError):
        pass
print(f"Isolated test data: {data}")
