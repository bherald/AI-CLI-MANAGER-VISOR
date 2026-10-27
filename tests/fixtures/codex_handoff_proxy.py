"""Provider-free, stateful Codex handoff fixture across independent proxy processes."""
import json
import sys
from pathlib import Path

from websockets.frames import Frame, Opcode
from websockets.http11 import Request
from websockets.server import ServerProtocol

state_path = Path(sys.argv[1])
protocol = ServerProtocol()
while chunk := sys.stdin.buffer.read1(65536):
    protocol.receive_data(chunk)
    for event in protocol.events_received():
        if isinstance(event, Request):
            protocol.send_response(protocol.accept(event))
        elif isinstance(event, Frame) and event.opcode == Opcode.TEXT:
            request = json.loads(event.data)
            if "id" not in request:
                continue
            state = json.loads(state_path.read_text())
            method, params = request["method"], request.get("params", {})
            state.setdefault("calls", []).append(method)
            response = {"id": request["id"], "result": {}}
            if method == "thread/start":
                state.update(id="exact-thread", durable=False, turns=[])
                response["result"] = {"thread": {"id": state["id"], "sessionId": "tree-id"}}
            elif method == "thread/name/set":
                assert params["threadId"] == state["id"]
                state["name"] = params["name"]
                state["durable"] = not state.get("drop_persistence", False)
            elif method == "thread/resume":
                if state.get("durable") and params["threadId"] == state["id"]:
                    response["result"] = {"thread": {"id": state["id"]}}
                else:
                    response = {"id": request["id"], "error": {"message": "no rollout found"}}
            state_path.write_text(json.dumps(state))
            protocol.send_text(json.dumps(response).encode())
    for payload in protocol.data_to_send():
        sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
