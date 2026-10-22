"""Exercise a frozen console's private backup/recovery CLI with disposable state only."""
import argparse
import os
import subprocess
import sys
import uuid
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from visor.broker import BrokerLock
from visor.paths import data_dir
from visor.process import hidden_options
from visor.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    work = root / ".cache/recovery-acceptance" / uuid.uuid4().hex
    data = data_dir(str(work / "isolated data"))
    store = Store(data)
    key = store.upsert_session("codex", "fixture", "Recovery fixture", str(work))
    message = store.enqueue("operator", key, "Synthetic private test message", "stable-id", operator=True)
    store.set_setting("message_draft", {"text": "Unfinished synthetic draft", "recipient": key})
    env = {**os.environ, "VISOR_SHELL_PATH": "0"}
    command = [str(args.executable.resolve()), "--data-dir", str(data)]
    backup = subprocess.run([*command, "--backup-state"], capture_output=True, text=True,
                            env=env, check=True, timeout=30, **hidden_options())
    path = Path(backup.stdout.strip())
    assert path.is_file() and path.is_relative_to(data / "backups")
    store.set_setting("message_draft", {"text": "Changed after snapshot", "recipient": key})
    lock = BrokerLock(data / "broker.lock")
    try:
        busy = subprocess.run([*command, "--restore-state", str(path)], capture_output=True,
                              text=True, env=env, check=False, timeout=30, **hidden_options())
        assert busy.returncode != 0, "Recovery ignored an active broker lock"
        assert store.get_setting("message_draft")["text"] == "Changed after snapshot"
    finally:
        lock.close()
    subprocess.run([*command, "--restore-state", str(path)], env=env, check=True,
                   timeout=30, **hidden_options())
    recovered = Store(data)
    assert recovered.get_setting("message_draft")["text"] == "Unfinished synthetic draft"
    assert recovered.message(message["id"])["state"] == "held"
    assert not recovered.get_setting("agent_enabled")
    print("PASS: frozen backup/restore, busy-lock refusal, draft recovery and pending-message hold")
    print(f"Isolated recovery evidence: {work}")


if __name__ == "__main__":
    main()
