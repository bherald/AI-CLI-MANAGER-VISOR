"""Isolated WebSocket-over-stdio peer; never starts a provider or uses credentials."""
import json
import sys

from websockets.frames import Frame, Opcode
from websockets.http11 import Request
from websockets.server import ServerProtocol

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
            protocol.send_text(json.dumps({"method": "test/notification", "params": {}}).encode())
            if request["method"] == "initialize":
                result = {"initialized": True}
            else:
                result = {"data": [{"id": "fixture-id", "name": "Unicode café", "cwd": "C:/project with spaces"}], "nextCursor": None}
            response = json.dumps({"id": request["id"], "result": result}, ensure_ascii=False).encode()
            protocol.send_text(response[:7], fin=False)
            protocol.send_continuation(response[7:], fin=True)
    for payload in protocol.data_to_send():
        sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
