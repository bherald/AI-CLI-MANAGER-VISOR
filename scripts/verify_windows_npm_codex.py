"""Check a real Windows npm Codex installation in source and the frozen app; no login/model calls."""
import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from visor.providers import provider_command


def main():
    if sys.platform != "win32":
        raise SystemExit("Run this acceptance check on Windows")
    prefix = root / ".cache/npm codex fixture"
    os.environ["PATH"] = str(prefix) + os.pathsep + os.environ.get("PATH", "")
    os.environ["CODEX_HOME"] = str(prefix / "isolated provider config")
    command = provider_command("codex")
    if len(command) != 2 or Path(command[0]).name.lower() != "node.exe" or Path(command[1]).name != "codex.js":
        raise RuntimeError("npm Codex did not resolve through its Node entry point")
    version = subprocess.check_output([*command, "--version"], text=True, encoding="utf-8", timeout=30).strip()
    if version != "codex-cli 0.163.0":
        raise RuntimeError("Unexpected Codex fixture version: " + version)
    latest = json.loads((root / "dist/latest-build.json").read_text())
    exe = root / "dist" / latest["bundle"] / "visor-console.exe"
    result = subprocess.check_output([str(exe), "--doctor"], text=True, encoding="utf-8", timeout=30)
    provider = json.loads(result)["providers"]["codex"]
    if provider.get("command") != command or provider["version"] != version:
        raise RuntimeError("Frozen app does not resolve/run the same npm Codex installation")
    print("PASS: real npm Codex 0.163.0 resolves and reports version in source and frozen Windows app; no authentication or models")


if __name__ == "__main__":
    main()
