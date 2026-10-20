"""Verify broker relocation with a still-running old-install helper; no terminal UI or models."""
import argparse
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psutil

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from visor.broker import Client
from visor.paths import write_private
from visor.process import hidden_options
from visor.store import Store


def wait_for(predicate, message, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--previous-executable", type=Path,
                        help="Use a previously built helper instead of a relocated copy")
    args = parser.parse_args()
    exe = args.executable.resolve()
    work = root / ".cache/upgrade-acceptance" / uuid.uuid4().hex
    work.mkdir(parents=True)
    if args.previous_executable:
        old_exe = args.previous_executable.resolve()
        if not old_exe.is_file() or old_exe == exe:
            raise SystemExit("Previous helper must exist at a different installation path")
    else:
        previous = work / "previous installation"
        previous.mkdir()
        if sys.platform == "darwin":
            # A signed Mac helper needs its complete Frameworks/Resources layout.
            bundle = exe.parent.parent.parent
            if bundle.suffix != ".app" or exe.parent.name != "MacOS":
                raise SystemExit("Expected a bundled Mac console helper")
            relocated = previous / bundle.name
            shutil.copytree(bundle, relocated, symlinks=True)
            old_exe = relocated / exe.relative_to(bundle)
        else:
            old_exe = previous / exe.name
            shutil.copy2(exe, old_exe)
            shutil.copytree(exe.parent / "_internal", previous / "_internal", symlinks=True)
    data = work / "isolated data"
    empty_path = work / "empty-path"
    empty_path.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    env.update(VISOR_SHELL_PATH="0", PATH=str(empty_path), CLAUDE_CONFIG_DIR=str(work / "empty-claude"))
    client = Client(data)
    brokers = []
    helper = None
    children = []

    def start(program):
        process = subprocess.Popen([str(program), "--broker", "--data-dir", str(data)], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, **hidden_options())
        brokers.append(process)
        def ready():
            if process.poll() is not None:
                raise RuntimeError("Test broker exited: " + process.communicate()[1].decode(errors="replace")[:2000])
            try:
                client.call("ping")
                return True
            except (OSError, ValueError):
                return False
        wait_for(ready, "Test broker startup timed out")
        return process

    try:
        first = start(old_exe)
        key = client.call("register", provider="claude", native_id=str(uuid.uuid4()),
                          name="Upgrade fixture", cwd=str(work))
        store = Store(data)
        store.update_session(key, managed=True)
        token = store.issue_credential(key, role="helper")
        ticket = data / "launch-upgrade-fixture.json"
        write_private(ticket, {"root": str(data), "helper_token": token, "cwd": str(work),
                               "command": [sys.executable, "-c", "import time; time.sleep(30)"]})
        helper = subprocess.Popen([str(old_exe), "--terminal", str(ticket)], env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  **hidden_options())
        wait_for(lambda: bool(store.session(key)["heartbeat"]), "Old-install helper never registered")
        saved = store.session(key)["helper_identity"]
        wait_for(lambda: bool(psutil.Process(helper.pid).children()), "Fixture worker did not start")
        children = psutil.Process(helper.pid).children(recursive=True)
        client.call("stop")
        first.wait(timeout=10)
        first.communicate()
        restarted_at = time.time()
        second = start(exe)
        wait_for(lambda: (store.session(key)["heartbeat"] or 0) >= restarted_at,
                 "Verified old-install helper did not reconnect to relocated broker")
        current = store.session(key)
        if current["process_id"] != helper.pid or current["helper_identity"] != saved or helper.poll() is not None:
            raise RuntimeError("Upgrade did not preserve the original live helper identity")
        if not all(child.is_running() for child in children):
            raise RuntimeError("Fixture worker stopped during broker relocation")
        client.call("stop")
        second.wait(timeout=10)
        second.communicate()
        print("PASS: relocated broker reconnected the same verified old-install helper without replacing its worker")
    finally:
        if helper and helper.poll() is None:
            # psutil process objects check PID reuse before signalling; only this fixture's children.
            children.extend(psutil.Process(helper.pid).children(recursive=True))
            for child in set(children):
                try:
                    child.terminate()
                    child.wait(timeout=5)
                except psutil.NoSuchProcess:
                    pass
            try:
                helper.communicate(input=b"\n", timeout=5)
            except subprocess.TimeoutExpired:
                helper.terminate()
                helper.communicate(timeout=5)
        for process in brokers:
            if process.poll() is None:
                try:
                    client.call("stop")
                    process.wait(timeout=10)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    process.terminate()
                    process.wait(timeout=5)
            process.communicate()
    print(f"Isolated upgrade test files: {work}")


if __name__ == "__main__":
    main()
