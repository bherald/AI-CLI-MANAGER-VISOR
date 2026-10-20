"""Live opt-in acceptance: disposable Codex thread, no project edits or real shutdowns."""
import argparse
import json
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from visor.broker import Broker, Client
from visor.process import hidden_options
from visor.providers import CodexRPC
from visor.store import Store

data = root / ".cache" / "codex-delivery-acceptance" / uuid.uuid4().hex
data.mkdir(parents=True)
parser = argparse.ArgumentParser()
parser.add_argument("--broker-executable", type=Path)
exe = parser.parse_args().broker_executable
process = None
if exe:
    process = subprocess.Popen([str(exe.resolve()), "--broker", "--data-dir", str(data)], **hidden_options())
    client = Client(data)
    for _ in range(100):
        try:
            client.call("ping")
            break
        except (OSError, ValueError):
            time.sleep(.1)
    store = Store(data)
    def dispatch(action, params):
        return client.call(action, **params)
else:
    broker = Broker(data)
    store = broker.store
    worker = threading.Thread(target=broker.delivery_loop, daemon=True)
    def dispatch(action, params):
        return broker.dispatch(action, params, "operator")
results = []
with CodexRPC() as rpc:
    tid = rpc.call("thread/start", {"cwd": str(data)})["thread"]["id"]
    (data / "thread-id.txt").write_text(tid)
    key = store.upsert_session("codex", tid, "VISOR isolated delivery acceptance", str(data), status="idle")
    store.update_session(key, managed=True)
    if not exe:
        worker.start()
    try:
        for action in ("send", "wrap_up"):
            if action == "send":
                message = dispatch("send", {"recipient": key, "body":
                    "VISOR acceptance test only. Reply with a short confirmation. Do not use tools, modify files, "
                    "or start background work. Follow the explicit receipt instructions in this message.",
                    "request_id": uuid.uuid4().hex})
            else:
                message = dispatch("wrap_up", {"sessions": [key], "request_id": uuid.uuid4().hex})[0]
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                time.sleep(2)
                if not exe:
                    broker.check_receipts()
                current = next(m for m in store.messages() if m["id"] == message["id"])
                if current["state"] == "completed":
                    break
            print(action, current["state"], current["detail"], flush=True)
            assert current["state"] == "completed", current
            results.append(action)
        assert store.session(key)["shutdown"].startswith("confirmed")
        print(json.dumps({"passed": results, "shutdown": store.session(key)["shutdown"], "packaged": bool(exe), "data": str(data)}))
    finally:
        if exe:
            client.call("stop")
            process.wait(35)
        else:
            broker.stop.set()
            worker.join(35)
        rpc.call("thread/archive", {"threadId": tid})
