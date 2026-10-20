"""Keep environment, dependency lock, and caches in the repository."""
import argparse
import os
import shutil
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
uv = shutil.which("uv")
if not uv:
    raise SystemExit("Install uv first: https://docs.astral.sh/uv/getting-started/installation/")
env = {**os.environ, "UV_CACHE_DIR": str(root / ".cache" / "uv")}
parser = argparse.ArgumentParser()
parser.add_argument("--offline", action="store_true", help="Use already cached packages only")
args = parser.parse_args()
options = ["--offline"] if args.offline else []
if not (root / "uv.lock").exists():
    subprocess.run([uv, "lock", "--python", "3.12", *options], cwd=root, env=env, check=True)
subprocess.run([uv, "sync", "--frozen", "--python", "3.12", "--extra", "dev", *options],
               cwd=root, env=env, check=True)
print("Environment ready. Run scripts/run.py with the .venv Python.")

