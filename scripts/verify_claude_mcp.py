"""Live Claude cooperative inbox/acknowledgment test using an isolated broker."""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from visor.broker import Client
from visor.paths import write_private
from visor.process import hidden_options
from visor.providers import executable
from visor.store import Store

data = root / ".cache" / "claude-mcp-acceptance" / uuid.uuid4().hex
data.mkdir(parents=True)
env = {**os.environ, "PYTHONPATH": str(root / "src")}
process = subprocess.Popen([sys.executable, "-m", "visor", "--broker", "--data-dir", str(data)], env=env, **hidden_options())
client = Client(data)
try:
    for _ in range(100):
        try:
            client.call("ping")
            break
        except (OSError, ValueError):
            time.sleep(.1)
    native_id = str(uuid.uuid4())
    key = client.call("register", provider="claude", native_id=native_id, name="VISOR isolated MCP acceptance", cwd=str(data))
    store = Store(data)
    store.update_session(key, managed=True, status="idle")
    credential = data / "credential.json"
    write_private(credential, {"root": str(data), "token": store.issue_credential(key), "session": key})
    config = data / "mcp.json"
    write_private(config, {"mcpServers": {"visor": {"command": sys.executable,
                  "args": ["-m", "visor", "--mcp", "--credential", str(credential)],
                  "env": {"PYTHONPATH": str(root / "src")}}}})
    client.call("send", recipient=key, body="Isolated acceptance test. Reading and acknowledging this message completes its task. Do not change any files.", request_id="normal")
    client.call("wrap_up", sessions=[key], request_id="wrap")
    result = subprocess.run([executable("claude"), "--print", "--session-id", native_id,
        "--output-format", "json", "--strict-mcp-config", "--mcp-config", str(config),
        "--allowedTools", "mcp__visor__receive,mcp__visor__acknowledge", "--",
        ("VISOR acceptance test. Use only visor receive and acknowledge. Receive the two queued messages, "
        "acknowledge each with completed=true after following its instruction. This isolated session has no "
        "project work or background jobs. Do not use any other tools or modify files. Then stop and report.")],
        check=False, cwd=data, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120, **hidden_options())
    (data / "result.json").write_text(result.stdout, encoding="utf-8")
    states = [(m["kind"], m["state"]) for m in store.messages()]
    print(json.dumps({"returncode": result.returncode, "messages": states, "data": str(data)}))
    assert result.returncode == 0 and len(states) == 2 and all(s == "completed" for _, s in states)
finally:
    client.call("stop")
    process.wait(15)
