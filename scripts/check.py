"""Run isolated core, transport, broker and Qt tests without provider model calls."""
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
env = {**os.environ, "PYTHONPATH": str(root / "src")}
raise SystemExit(subprocess.call([sys.executable, "-X", "faulthandler", "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=root, env=env))

