"""Isolated control peer that retains stdout after failure; no provider access."""
import json
import sys
import time

from websockets.frames import Frame, Opcode
from websockets.http11 import Request
from websockets.server import ServerProtocol

protocol = ServerProtocol()
while chunk := sys.stdin.buffer.read1(65536):
    protocol.receive_data(chunk)
    delay = False
    handshake = False
    for event in protocol.events_received():
        if isinstance(event, Request):
            protocol.send_response(protocol.accept(event))
            handshake = True
        elif isinstance(event, Frame) and event.opcode == Opcode.TEXT:
            request = json.loads(event.data)
            if "id" not in request:
                continue
            if request["method"] == "initialize":
                protocol.send_text(json.dumps({"id": request["id"], "result": {}}).encode())
            elif request["method"] == "fixture/close":
                protocol.send_close(1001)
                delay = True
            elif request["method"] == "fixture/oversized":
                protocol.send_text(b"x" * (8 * 1024 * 1024 + 1))
                delay = True
            else:
                delay = True
    for payload in protocol.data_to_send():
        if handshake:
            # A partial HTTP handshake must not be mistaken for a closed socket.
            sys.stdout.buffer.write(payload[:7])
            sys.stdout.buffer.flush()
            time.sleep(0.03)
            payload = payload[7:]
        sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
    if delay:
        time.sleep(60)
