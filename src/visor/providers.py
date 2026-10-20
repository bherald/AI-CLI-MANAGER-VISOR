from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from websockets.client import ClientProtocol
from websockets.frames import Frame, Opcode
from websockets.http11 import Response
from websockets.protocol import OPEN
from websockets.uri import parse_uri

from .process import hidden_options, run


class ProviderUnavailable(RuntimeError):
    pass


def executable(provider: str) -> str:
    if provider not in ("codex", "claude"):
        raise ProviderUnavailable("Unknown provider")
    found = shutil.which(provider)
    if not found:
        raise ProviderUnavailable(f"Install {provider} and sign in using its supported login")
    if Path(found).suffix.lower() in (".bat", ".cmd"):
        raise ProviderUnavailable("A native provider executable is required; shell launchers are not yet supported")
    return found


class CodexRPC:
    """Attach to the existing daemon. Never start an unrelated server or approve its requests."""

    def __init__(self, *, experimental=False):
        self.process = subprocess.Popen([executable("codex"), "app-server", "proxy"],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL,
                                        **hidden_options())
        self.responses = queue.Queue()
        self.counter = 0
        self.protocol = ClientProtocol(parse_uri("ws://localhost/"), max_size=8 * 1024 * 1024)
        self.wire_lock = threading.RLock()
        self.ready = threading.Event()
        self.connection_error = None
        self.protocol.send_request(self.protocol.connect())
        self._flush()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            if not self.ready.wait(10) or self.protocol.state != OPEN:
                raise ProviderUnavailable(self.connection_error or "Codex control socket handshake timed out")
            self.call("initialize", {"clientInfo": {"name": "visor", "title": "VISOR", "version": "1.2.0"},
                                     "capabilities": {"experimentalApi": experimental}})
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def _read(self):
        fragments = bytearray()
        try:
            while data := self.process.stdout.read1(65536):
                with self.wire_lock:
                    self.protocol.receive_data(data)
                    events = self.protocol.events_received()
                    self._flush()
                for event in events:
                    if isinstance(event, Response):
                        if self.protocol.handshake_exc:
                            self.connection_error = str(self.protocol.handshake_exc)
                        self.ready.set()
                    elif isinstance(event, Frame) and event.opcode in (Opcode.TEXT, Opcode.CONT):
                        fragments.extend(event.data)
                        if len(fragments) > 8 * 1024 * 1024:
                            raise ValueError("Codex response exceeds message limit")
                        if event.fin:
                            msg = json.loads(fragments)
                            fragments.clear()
                            if "id" in msg and "method" not in msg:
                                self.responses.put(msg)
        except (OSError, ValueError) as exc:
            self.connection_error = str(exc)
        finally:
            self.ready.set()
            self.responses.put({"error": {"message": "Codex daemon connection closed"}})

    def _flush(self):
        for data in self.protocol.data_to_send():
            if data:
                self.process.stdin.write(data)
        self.process.stdin.flush()

    def send(self, msg):
        with self.wire_lock:
            self.protocol.send_text(json.dumps(msg).encode())
            self._flush()

    def call(self, method, params, timeout=10):
        self.counter += 1
        request_id = self.counter
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            try:
                response = self.responses.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise ProviderUnavailable("Codex daemon did not respond; no session was resumed") from exc
            if "id" not in response or response.get("id") == request_id:
                if "error" in response:
                    raise ProviderUnavailable(str(response["error"].get("message", "Codex request failed")))
                return response.get("result", {})

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()  # Only this short-lived proxy, never the daemon.
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.reader.join(timeout=2)
        self.process.stdin.close()
        self.process.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def codex_status(value):
    kind = (value or {}).get("type", "unknown")
    if kind == "active":
        flags = value.get("activeFlags", [])
        return "waiting" if any("waiting" in str(x).lower() for x in flags) else "working"
    return {"idle": "idle", "notLoaded": "disconnected", "systemError": "unknown"}.get(kind, "unknown")


def discover_codex():
    sessions = []
    with CodexRPC() as rpc:
        cursor = None
        seen = set()
        while True:
            result = rpc.call("thread/list", {"cursor": cursor, "limit": 100, "sortKey": "updated_at"})
            for item in result.get("data", []):
                sessions.append({"provider": "codex", "native_id": item["id"],
                                 "name": item.get("name") or item["id"][:12],
                                 "cwd": item.get("cwd", ""),
                                 "status": codex_status(item.get("status")),
                                 "latest": "Runtime status from Codex daemon",
                                 "updated": item.get("updatedAt", 0)})
            cursor = result.get("nextCursor")
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
    return sessions


def create_session(provider, cwd):
    executable(provider)
    if not Path(cwd).is_dir():
        raise ProviderUnavailable("Choose an existing project directory")
    if provider == "claude":
        return str(uuid.uuid4())
    ensure_codex_daemon()
    with CodexRPC() as rpc:
        result = rpc.call("thread/start", {"cwd": str(Path(cwd).resolve())})
    # thread.id, not sessionId (which may name a shared session tree).
    return result["thread"]["id"]


def ensure_codex_daemon():
    """Operator launch preflight. Start if absent; never restart or replace live work."""
    try:
        result = run([executable("codex"), "app-server", "daemon", "start"], timeout=20)
        if result.returncode:
            raise ProviderUnavailable("Codex shared server could not start; run `codex app-server daemon start` in a terminal for diagnostics. No session was opened.")
        # A successful launcher exit is not proof the control socket is ready.
        with CodexRPC():
            pass
    except (OSError, subprocess.TimeoutExpired, ProviderUnavailable) as exc:
        raise ProviderUnavailable("Codex shared server is unavailable; no session was opened. " + str(exc)) from exc


def delete_provider_session(session, before_delete):
    if session["provider"] == "claude":
        from .claude import delete_transcript
        before_delete([session["native_id"]])
        return delete_transcript(session["native_id"])
    with CodexRPC(experimental=True) as rpc:
        ids = {session["native_id"]}
        parents = {}
        # Both active and archived descendants are part of Codex's permanent-delete contract.
        for archived in (False, True):
            cursor, seen = None, set()
            while True:
                result = rpc.call("thread/list", {"ancestorThreadId": session["native_id"],
                    "archived": archived, "sourceKinds": ["cli", "vscode", "exec", "appServer",
                        "subAgent", "subAgentReview", "subAgentCompact", "subAgentThreadSpawn",
                        "subAgentOther", "unknown"], "limit": 100, "cursor": cursor})
                parents.update((item["id"], item.get("parentThreadId")) for item in result.get("data", []))
                cursor = result.get("nextCursor")
                if not cursor:
                    break
                if cursor in seen:
                    raise ProviderUnavailable("Codex descendant listing repeated its cursor; deletion cancelled")
                seen.add(cursor)
        pending = set(parents) - ids
        while pending:
            children = {sid for sid in pending if parents[sid] in ids}
            if not children:
                raise ProviderUnavailable("Cannot verify Codex descendant ancestry; deletion cancelled")
            ids.update(children)
            pending -= children
        before_delete(sorted(ids))
        rpc.call("thread/delete", {"threadId": session["native_id"]}, timeout=20)
        return sorted(ids)


def session_command(session, mcp_command, *, new=False, claude_options=None, settings_file=None):
    exe = executable(session["provider"])
    if session["provider"] == "codex":
        config = ["-c", "mcp_servers.visor.command=" + json.dumps(mcp_command[0]),
                  "-c", "mcp_servers.visor.args=" + json.dumps(mcp_command[1:])]
        return [exe, "--remote", "unix://", *config, "-C", session["cwd"], "resume", session["native_id"]]
    config = json.dumps({"mcpServers": {"visor": {"command": mcp_command[0], "args": mcp_command[1:]}}})
    from .claude import validate_options
    options = validate_options(claude_options or {})
    command = [exe, "--session-id" if new else "--resume", session["native_id"]]
    if options["mode"] != "plugin":
        command += ["--mcp-config", config]
    if settings_file:
        command += ["--settings", str(settings_file)]
    if options["mode"] == "development":
        command += ["--dangerously-load-development-channels", "server:visor"]
    elif options["mode"] == "plugin":
        command += ["--channels", options["plugin"]]
    return command


def codex_message_text(message):
    return (f"[VISOR message {message['id']} from {message['sender']}]\n{message['body']}\n"
            "Use VISOR acknowledge with this message ID after reading; mark completed only when done. "
            "If that tool is unavailable, include exactly one of these lines, unquoted and outside code blocks, "
            "in your final reply: VISOR_ACK " + message['id'] + " READ or VISOR_ACK " + message['id'] +
            " COMPLETED. Use COMPLETED only when the requested work is actually complete; otherwise use READ. "
            "This acknowledgment does not authorize additional work or override approval rules.")


def codex_reply_receipts(turns, messages):
    """Accept an explicit final reply only after our exact envelope in the same turn."""
    receipts = {}
    envelopes = {codex_message_text(m): m["id"] for m in messages}
    for turn in turns:
        if not isinstance(turn, dict) or not isinstance(turn.get("items", []), list):
            continue
        targets = set()
        for item in turn.get("items", []):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "userMessage":
                if not isinstance(item.get("content", []), list):
                    continue
                for content in item.get("content", []):
                    if isinstance(content, dict) and content.get("type") == "text" and isinstance(content.get("text"), str) and content["text"] in envelopes:
                        targets.add(envelopes[content["text"]])
            elif item.get("type") == "agentMessage" and item.get("phase") == "final_answer":
                if not isinstance(item.get("text"), str):
                    continue
                fenced = False
                for line in item.get("text", "").splitlines():
                    if line.lstrip().startswith(("```", "~~~")):
                        fenced = not fenced
                        continue
                    if fenced:
                        continue
                    for mid in targets:
                        if line == f"VISOR_ACK {mid} READ":
                            receipts.setdefault(mid, False)
                        elif line == f"VISOR_ACK {mid} COMPLETED" and turn.get("status") == "completed":
                            receipts[mid] = True
    return receipts


def read_codex_receipts(session, messages):
    if not messages:
        return {}
    receipts, seen, cursor = {}, set(), None
    with CodexRPC() as rpc:
        while True:
            result = rpc.call("thread/turns/list", {"threadId": session["native_id"], "limit": 50,
                "itemsView": "full", "sortDirection": "desc", "cursor": cursor})
            for mid, completed in codex_reply_receipts(result.get("data", []), messages).items():
                receipts[mid] = receipts.get(mid, False) or completed
            if len(receipts) == len(messages):
                return receipts
            cursor = result.get("nextCursor")
            if not cursor:
                return receipts
            if cursor in seen:
                raise ProviderUnavailable("Codex receipt history repeated its pagination cursor")
            seen.add(cursor)


def scan_codex_receipts(session, messages, state=None, *, now=None):
    """One recent page plus at most one historical page; persist backfill across polls."""
    now = time.time() if now is None else now
    previous = state or {}
    state = dict(previous)
    ids = {m["id"] for m in messages}
    cursor = state.get("cursor")
    seen = set(state.get("seen", []))
    receipts = {}
    state.update(known_ids=sorted(ids), last_checked=now, warning=previous.get("warning", ""))
    if not ids:
        return {}, {}
    with CodexRPC() as rpc:
        def page(at):
            result = rpc.call("thread/turns/list", {"threadId": session["native_id"], "limit": 50,
                "itemsView": "full", "sortDirection": "desc", "cursor": at})
            if not isinstance(result, dict) or not isinstance(result.get("data", []), list):
                raise ProviderUnavailable("Codex receipt history returned an invalid page")
            following = result.get("nextCursor")
            if following is not None and not isinstance(following, str):
                raise ProviderUnavailable("Codex receipt history returned an invalid cursor")
            return result
        recent = page(None)
        receipts.update(codex_reply_receipts(recent.get("data", []), messages))
        state["pages_checked"] = state.get("pages_checked", 0) + 1
        if not recent.get("nextCursor") or ids <= receipts.keys():
            state.update(cursor=None, seen=[], backfill_after=now + 300, warning="")
            return receipts, state
        new_messages = ids - set(previous.get("known_ids", []))
        if not cursor and not new_messages and now < state.get("backfill_after", 0):
            return receipts, state
        at = cursor or recent["nextCursor"]
        try:
            historical = page(at)
            for mid, completed in codex_reply_receipts(historical.get("data", []), messages).items():
                receipts[mid] = receipts.get(mid, False) or completed
            state["pages_checked"] += 1
            following = historical.get("nextCursor")
            if following and (following == at or following in seen):
                raise ProviderUnavailable("Codex receipt history repeated its pagination cursor")
            seen.add(at)
            state.update(cursor=following, seen=sorted(seen) if following else [],
                         backfill_after=0 if following else now + 300, warning="")
        except (OSError, RuntimeError, ValueError) as exc:
            # Preserve valid recent receipts even if an older cursor became invalid.
            state.update(cursor=None, seen=[], backfill_after=now + 60, warning=str(exc))
    return receipts, state


def codex_peer_tools(session):
    """Inspect this thread's existing MCP connection without global reload/config writes."""
    expected = {"send", "receive", "acknowledge", "peers"}
    tools, states, seen, cursor = set(), set(), set(), None
    with CodexRPC() as rpc:
        while True:
            result = rpc.call("mcpServerStatus/list", {"threadId": session["native_id"],
                "serverName": "visor", "detail": "toolsAndAuthOnly", "cursor": cursor, "limit": 100})
            for server in result.get("data", []):
                if server.get("name") != "visor":
                    continue
                states.add(server.get("runtimeStatus") or "unknown")
                for key, tool in server.get("tools", {}).items():
                    tools.add(tool.get("name", key).removeprefix("mcp__visor__"))
            cursor = result.get("nextCursor")
            if not cursor:
                break
            if cursor in seen:
                raise ProviderUnavailable("Codex MCP inventory repeated its pagination cursor")
            seen.add(cursor)
    return {"advertised": sorted(tools & expected), "missing": sorted(expected - tools),
            "runtime": sorted(states) or ["not listed"],
            "note": "Inventory is not an end-to-end tool call test. Operator receipt fallback remains available. "
                    "An already-loaded thread can lack VISOR peer tools; global provider configuration was not changed."}


def deliver_codex(session, message):
    text = codex_message_text(message)
    result = run([executable("codex"), "queue", "--remote", "unix://", "--thread", session["native_id"],
                  "--message", text, "-C", session["cwd"]], cwd=session["cwd"], timeout=30)
    # A nonzero command may still have transmitted: do not claim safe retry.
    if result.returncode:
        return "delivery_unknown", "Codex queue did not confirm acceptance; inspect the target session"
    return "delivered", "Codex queue accepted; agent acknowledgment pending"

