"""Extract and verify a Windows beta without launching or messaging a provider."""
import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--archive", type=Path, default=root / "dist" / "visor-0.1.0b1-windows-amd64.zip")
archive = parser.parse_args().archive.resolve()
with archive.open("rb") as stream:
    assert hashlib.file_digest(stream, "sha256").hexdigest() == archive.with_suffix(".zip.sha256").read_text().split()[0]
work = root / ".cache" / "package-acceptance" / uuid.uuid4().hex
work.mkdir(parents=True)
with zipfile.ZipFile(archive) as bundle:
    assert all((work / name).resolve().is_relative_to(work.resolve()) for name in bundle.namelist())
    bundle.extractall(work)
exe = work / "visor" / "visor-console.exe"
data = work / "data"
env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
env["QT_QPA_PLATFORM"] = "offscreen"
subprocess.run([str(exe), "--smoke-test", "--data-dir", str(data)], cwd=work, env=env, check=True, timeout=45)
assert (data / "ui-smoke.png").is_file()


def call(action, **params):
    endpoint = json.loads((data / "broker.json").read_text())
    request = urllib.request.Request(
        f"http://127.0.0.1:{endpoint['port']}/rpc",
        data=json.dumps({"action": action, "params": params}).encode(),
        headers={"Authorization": f"Bearer {endpoint['token']}", "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        value = json.load(response)
    if "error" in value:
        raise RuntimeError(value["error"])
    return value["result"]


message_id = None
for iteration in range(2):
    process = subprocess.Popen([str(exe), "--broker", "--data-dir", str(data)], cwd=work,
                               env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        for attempt in range(100):
            try:
                call("ping")
                break
            except (OSError, ValueError):
                if process.poll() is not None:
                    raise RuntimeError("Packaged broker exited during startup")
                time.sleep(0.1)
        else:
            raise RuntimeError("Packaged broker startup timed out")
        if iteration == 0:
            key = call("register", provider="claude", native_id=str(uuid.uuid4()), name="Package test", cwd=str(work))
            call("toggle", enabled=False)
            message_id = call("send", recipient=key, body="Isolated package test", request_id="package-once")["id"]
        else:
            snapshot = call("snapshot")
            assert not snapshot["agent_enabled"]
            assert any(m["id"] == message_id and m["state"] == "queued" for m in snapshot["messages"])
        call("stop")
        process.wait(timeout=10)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
print("PASS: archive checksum, extracted UI, packaged broker startup/restart, durable operator queue")
print(f"Isolated acceptance files: {work}")
