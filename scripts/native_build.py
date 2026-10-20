"""Build and verify a beta on its target OS, without provider model calls."""
import argparse
import os
import subprocess
import sys
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--skip-install", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not args.skip_install:
        subprocess.run([sys.executable, "scripts/bootstrap.py",
                        *(["--offline"] if args.offline else [])], cwd=root, check=True)
    if not python.exists():
        raise SystemExit("Project environment missing; run without --skip-install")
    for command in (["scripts/check.py"], ["-m", "ruff", "check", "src", "tests", "scripts"],
                    ["scripts/run.py", "--smoke-test", "--data-dir", f".cache/ui-smoke/{uuid.uuid4().hex}"],
                    ["scripts/build.py"], ["scripts/verify_native_package.py"]):
        subprocess.run([str(python), *command], cwd=root, check=True)
    print("Native beta built and extracted package verified. Interactive acceptance is still required.")


if __name__ == "__main__":
    main()
