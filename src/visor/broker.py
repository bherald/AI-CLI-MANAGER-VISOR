from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer

from .claude import OPTIONS as CLAUDE_OPTIONS
from .claude import discover as discover_claude
from .claude import hook_event, launch_settings, validate_options
from .layout import Rect, arrange
from .paths import own_command, write_private
from .platforms import WindowNotFound, terminal_launch, windows
from .process import hidden_options
from .process_identity import descendants, expected_helper_image, process_identity, retired
from .providers import (
    codex_peer_tools,
    create_session,
    delete_provider_session,
    deliver_codex,
    discover_codex,
    ensure_codex_daemon,
    scan_codex_receipts,
    session_command,
)
from .store import PolicyError, Store

log = logging.getLogger(__name__)
BUILD = "2026-10-21-beta-2"

WRAP_UP = ("Finish your current safe step, save context, report validation and remaining work, "
           "and stop taking new work. Do not terminate unrelated processes. Report any owned "
           "background work still running. Acknowledge this VISOR message as completed only "
           "when your wrap-up is complete. Existing approval rules still apply.")


class Client:
    def __init__(self, root: Path, token=None):
        self.root = root
        self.token = token

    def call(self, action, **params):
        endpoint = json.loads((self.root / "broker.json").read_text(encoding="utf-8"))
        # Broker endpoint is always local, even if a saved file is modified.
        port = int(endpoint["port"])
        token = self.token or endpoint["token"]
        request = urllib.request.Request(f"http://127.0.0.1:{port}/rpc",
                                         data=json.dumps({"action": action, "params": params}).encode(),
                                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=40) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                message = json.load(exc).get("error", "Broker request rejected")
            except ValueError:
                message = "Broker request rejected"
            raise PolicyError(message) from exc
        return result["result"]


class Broker:
    def __init__(self, root: Path):
        self.root = root
        self.store = Store(root)
        self.token = secrets.token_urlsafe(32)
        self.stop = threading.Event()
        self.delivery_lock = threading.RLock()
        self.launch_lock = threading.Lock()
        self.delivery_error = ""
        self.refresh_error = ""
        self.provider_warnings = {}
        self.receipt_errors = {}
        self.last_receipt_session = ""
        self.server = None

    def dispatch(self, action, params, identity, role="agent"):
        if role == "helper":
            return self.helper_action(action, params, identity)
        if identity != "operator":
            return self.agent_action(action, params, identity)
        if action == "snapshot":
            return {"sessions": self.store.sessions(), "pairs": self.store.pairs(),
                    "messages": self.store.messages(visible=True), "agent_enabled": self.store.get_setting("agent_enabled", False),
                    "shutdown_targets": self.store.get_setting("shutdown_targets", []),
                    "wrap_up_progress": self.wrap_up_progress(),
                    "delivery_error": self.delivery_error, "refresh_error": self.refresh_error,
                    "provider_warnings": self.provider_warnings,
                    "receipt_error": " · ".join(self.receipt_errors.copy().values()),
                    "external_focus": self.store.external_focus_links()}
        if action == "external_window_candidates":
            from .external_windows import candidates
            return candidates()
        if action == "external_focus_link":
            if params.get("confirmed") is not True:
                raise PolicyError("Confirm the selected external window link for Focus")
            from .external_windows import validate
            with self.launch_lock:
                value = validate(params["identity"])
                self.store.set_external_focus(params["session"], value)
            return True
        if action == "external_focus_unlink":
            if params.get("confirmed") is not True:
                raise PolicyError("Confirm removing this external window link")
            self.store.set_external_focus(params["session"], None)
            return True
        if action == "ping":
            return {"running": True, "version": "1.2.3", "build": BUILD}
        if action == "refresh":
            return self.refresh_providers()
        if action == "claude_options":
            if "value" in params:
                self.store.set_setting("claude_options", validate_options(params["value"]))
            return self.store.get_setting("claude_options", CLAUDE_OPTIONS)
        if action == "peer_tool_status":
            session = self.store.session(params["session"])
            if session["provider"] != "codex":
                raise PolicyError("This inventory check is for Codex sessions")
            return codex_peer_tools(session)
        if action == "register":
            native_id = str(uuid.UUID(params["native_id"]))
            cwd = str(Path(params["cwd"]).resolve())
            if not Path(cwd).is_dir():
                raise PolicyError("Project directory does not exist")
            key = self.store.upsert_session(params["provider"], native_id, params["name"], cwd)
            self.store.session(key)  # Reject re-registering a permanently deleted identity.
            return key
        if action == "delete_session":
            if params.get("confirmed") is not True:
                raise PolicyError("Confirm permanent deletion and immediate terminal closure")
            with self.launch_lock, self.delivery_lock:
                session = self.store.session(params["session"])
                def close_targets(native_ids):
                    keys = {session["provider"] + ":" + sid for sid in native_ids}
                    for target in self.store.sessions():
                        if target["id"] in keys:
                            self.close_for_delete(target)
                deleted = delete_provider_session(session, close_targets)
                for native_id in deleted:
                    key = session["provider"] + ":" + native_id
                    self.store.delete_session(key)
                return True
        if action == "new":
            provider, cwd = params["provider"], str(Path(params["cwd"]).resolve())
            with self.launch_lock:
                native_id = create_session(provider, cwd)
                key = self.store.upsert_session(provider, native_id, params.get("name") or Path(cwd).name, cwd)
                self.launch(key, new=(provider == "claude"))
            return key
        if action == "resume":
            with self.launch_lock:
                self.launch(params["session"])
            return True
        if action == "forget_closed_window":
            session = self.store.session(params["session"])
            if session["heartbeat"] and time.time() - session["heartbeat"] < 20:
                raise PolicyError("A live terminal helper still owns this session")
            if not params.get("confirmed_closed"):
                raise PolicyError("Explicit confirmation that the old terminal is closed is required")
            self.store.update_session(session["id"], window_token=None, process_id=None, heartbeat=None,
                                      helper_identity=None, window_identity=None)
            return True
        if action == "rename":
            name = params["name"].strip()
            if not name or len(name) > 160:
                raise PolicyError("Name must contain 1–160 characters")
            self.store.update_session(params["session"], name=name)
            return True
        if action == "send":
            return self.store.enqueue("operator", params["recipient"], params["body"], params["request_id"], operator=True)
        if action == "pair":
            with self.delivery_lock:
                self.store.pair(params["a"], params["b"], params.get("enabled", True))
            return True
        if action == "toggle":
            with self.delivery_lock:
                self.store.toggle(params["enabled"])
            return True
        if action == "participate":
            with self.delivery_lock:
                self.store.participate(params["session"], params["enabled"])
            return True
        if action == "release":
            self.store.release(params["message"])
            return True
        if action == "delete_messages":
            return self.store.delete_messages(params["messages"])
        if action == "clear_history":
            return self.store.clear_history()
        if action == "cancel_shutdown_tracking":
            self.store.set_setting("shutdown_targets", [])
            return True  # Delivered requests remain delivered; held messages remain held.
        if action == "wrap_up_timeout":
            key = params["session"]
            self.store.session(key)
            if "seconds" in params:
                seconds = params["seconds"]
                if isinstance(seconds, bool) or not isinstance(seconds, int) or not 30 <= seconds <= 86400:
                    raise PolicyError("Wrap-up wait must be 30–86400 seconds")
                self.store.set_setting("wrap_up_timeout:" + key, seconds)
            return self.store.get_setting("wrap_up_timeout:" + key, 300)
        if action == "wrap_up":
            with self.delivery_lock:
                keys = params.get("sessions")
                targets = [s for s in self.store.sessions() if s["managed"]
                           and (keys is None or s["id"] in keys)
                           and (s["status"] in ("working", "waiting", "idle")
                                or (s["heartbeat"] and time.time() - s["heartbeat"] < 20
                                    and s["status"] != "disconnected"))]
                if not targets:
                    raise PolicyError("Wrap-up was not sent: the selected session is not connected. Check its terminal for startup/trust prompts and reconnect it to the shared Codex server.")
                self.store.toggle(False)
                result = []
                for session in targets:
                    message = self.store.enqueue("operator", session["id"], WRAP_UP,
                                                 params["request_id"] + session["id"], operator=True, kind="wrap_up")
                    result.append(message)
                    self.store.set_setting("wrap_up_request:" + session["id"],
                        {"requested": message["created"], "message": message["id"],
                         "timeout": self.store.get_setting("wrap_up_timeout:" + session["id"], 300)})
                tracked = self.store.get_setting("shutdown_targets", [])
                self.store.set_setting("shutdown_targets", list(dict.fromkeys([*tracked, *[s["id"] for s in targets]])))
                return result
        if action in ("focus", "focus_target"):
            external = self.store.external_focus_links().get(params["session"])
            if external:
                from .external_windows import focus, validate
                validate(external["identity"])
                if action == "focus_target":
                    return {"external": True, "identity": external["identity"]}
                focus(external["identity"])
                return True
            session = self.verified_window(params["session"])
            if action == "focus_target":
                current = self.store.session(session["id"])
                return {"token": current["window_token"],
                        "identity": json.loads(current["window_identity"] or "null")}
            windows().focus(session["window_token"], expected_identity=json.loads(session["window_identity"] or "null"))
            return True
        if action in ("arrange", "save_layout", "restore_layout"):
            return self.window_action(action, params)
        if action == "ui_state":
            if "value" in params:
                self.store.set_setting("ui", params["value"])
            return self.store.get_setting("ui", {})
        if action == "stop":
            self.stop.set()
            return True
        raise PolicyError("Unknown operator action")

    def agent_action(self, action, params, identity):
        if action == "channel_written":
            message = self.store.message(params["message"])
            if not message or message["recipient"] != identity:
                raise PolicyError("Channel message does not belong to this session")
            self.store.delivery_result(message["id"], "delivery_unknown",
                "Claude channel transport wrote notification; waiting for agent acknowledgment (policy may block channels)")
            return True
        if action == "peers":
            pairs = self.store.pairs()
            keys = {b if a == identity else a for a, b in pairs if identity in (a, b)}
            return [{"id": s["id"], "name": s["name"], "participates": bool(s["participates"])}
                    for s in self.store.sessions() if s["id"] in keys]
        if action == "send":
            # Sender and origin come from the credential, never request arguments.
            return self.store.enqueue(identity, params["recipient"], params["body"], params["request_id"])
        if action == "receive":
            with self.delivery_lock:
                message = self.store.claim(identity)
            return message
        if action == "transport_received":
            self.store.transport_received(identity, params["message"])
            return True
        if action == "acknowledge":
            self.store.acknowledge(identity, params["message"], params.get("completed", False))
            return True
        raise PolicyError("This operation requires the operator or a terminal helper")

    def helper_action(self, action, params, identity):
        if action == "claude_event":
            session = self.store.session(identity)
            if session["provider"] != "claude":
                raise PolicyError("Claude hooks require a Claude session")
            state = hook_event(params["event"], identity)
            self.store.update_session(identity, status=state["status"], latest=state["latest"], last_used=time.time())
            self.store.set_setting("claude_runtime:" + identity, {**state, "observed": time.time()})
            return True
        if action == "heartbeat":
            # This only proves the helper is alive, not the provider's active turn status.
            session = self.store.session(identity)
            current = process_identity(params.get("pid"))
            previous = json.loads(session["helper_identity"]) if session["helper_identity"] else None
            if previous:
                if previous != current:
                    raise PolicyError("Terminal helper process identity changed; reconcile before resuming")
            elif os.path.normcase(current["image"]) != expected_helper_image():
                raise PolicyError("Terminal helper executable does not match this VISOR installation")
            self.store.update_session(identity, helper_identity=json.dumps(current))
            owned = self.store.get_setting("owned_work:" + identity, [])
            observed = descendants(current["pid"])
            # Keep birth identities after reparenting; never signal these processes.
            merged = {(p["pid"], p["created"]): p for p in [*owned, *observed]}
            self.store.set_setting("owned_work:" + identity, list(merged.values()))
            self.store.update_session(identity, heartbeat=time.time(), process_id=params.get("pid"))
            return True
        if action == "provider_exit":
            self.store.update_session(identity, status="disconnected",
                                      latest="Managed provider process exited; terminal may remain open")
            return True
        if action == "terminal_exit":
            self.store.update_session(identity, heartbeat=None, process_id=None, window_token=None,
                                      helper_identity=None, window_identity=None,
                                      status="disconnected", latest="Managed terminal helper exited")
            return True
        raise PolicyError("Terminal helpers cannot send messages or operate the supervisor")

    def launch(self, key, new=False):
        session = self.store.session(key)
        if key in self.store.external_focus_links():
            raise PolicyError("An external terminal window is linked. Inspect and unlink it before resuming this session.")
        if session["heartbeat"] and time.time() - session["heartbeat"] < 20 and not self.retired_windows_helper(session):
            raise PolicyError("Session already has a live managed worker; use Focus")
        if session["window_token"]:
            try:
                windows().find(session["window_token"])
            except WindowNotFound:
                if not self.retired_windows_helper(session):
                    raise PolicyError("Previous terminal ownership is uncertain; use Reconcile closed terminal after checking it") from None
                self.store.update_session(key, window_token=None, process_id=None, heartbeat=None,
                                          helper_identity=None, window_identity=None)
                session = self.store.session(key)
            except (OSError, RuntimeError, ValueError):
                # Ambiguous/stale ownership is not evidence that resuming is safe.
                raise PolicyError("Previous terminal ownership is uncertain; close/reconcile it before resuming")
            else:
                raise PolicyError("Existing terminal is still open; inspect it before resuming")
        shared_reattach = session["provider"] == "codex" and session["managed"]
        if not new and session["status"] in ("working", "waiting", "idle") and not shared_reattach:
            raise PolicyError("Provider reports a loaded session; associate its existing terminal before resuming")
        if session["provider"] == "codex":
            ensure_codex_daemon()
        token = self.store.issue_credential(key)
        credential = self.root / f"session-{uuid.uuid4().hex}.json"
        options = self.store.get_setting("claude_options", CLAUDE_OPTIONS)
        if session["provider"] == "claude" and options["mode"] == "plugin" and not getattr(sys, "frozen", False):
            raise PolicyError("Approved Claude plugin mode requires the packaged console helper; use local preview when running Python source")
        channel = session["provider"] == "claude" and options["mode"] != "inbox"
        write_private(credential, {"root": str(self.root), "token": token, "session": key, "channel": channel})
        title = "VISOR-" + uuid.uuid4().hex
        mcp_command = [*own_command(console=True), "--mcp", "--credential", str(credential)]
        helper_token = self.store.issue_credential(key, role="helper")
        settings = launch_settings(self.root, key, helper_token) if session["provider"] == "claude" else None
        command = session_command(session, mcp_command, new=new, claude_options=options, settings_file=settings)
        ticket = self.root / f"launch-{uuid.uuid4().hex}.json"
        write_private(ticket, {"command": command, "cwd": session["cwd"], "root": str(self.root),
                               "helper_token": helper_token,
                               "env": {"VISOR_EXECUTABLE": own_command(console=True)[0],
                                       "VISOR_AGENT_CREDENTIAL": str(credential)}})
        self.store.update_session(key, managed=True, window_token=title, helper_identity=None,
                                  window_identity=None, shutdown="", latest="Terminal launch requested")
        try:
            terminal_launch([*own_command(console=True), "--terminal", str(ticket)], session["cwd"], title)
            self.store.update_session(key, last_used=time.time())
        except Exception:
            self.store.update_session(key, window_token=None, latest="Terminal launch failed")
            raise

    @staticmethod
    def retired_windows_helper(session):
        if not session["helper_identity"]:
            return False
        expected = json.loads(session["helper_identity"])
        # recover() clears live heartbeat/PID fields on a broker restart. The saved
        # native identity still carries the original PID and creation time to verify.
        pid = session["process_id"] or expected.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False
        try:
            current = process_identity(pid)
            return current["created"] != expected.get("created", current["created"])
        except OSError as exc:
            # ERROR_INVALID_PARAMETER means this PID no longer exists. Access-denied or
            # other probe failures do not prove that its owner is gone.
            import errno
            return exc.errno == (87 if sys.platform == "win32" else errno.ESRCH)

    def close_for_delete(self, session):
        if session["id"] in self.store.external_focus_links():
            raise PolicyError("An external Focus link does not establish worker ownership. Close the external terminal and unlink it before deleting history.")
        if session["window_token"]:
            try:
                windows().find(session["window_token"])
                self.verified_window(session["id"])
            except WindowNotFound:
                if not self.retired_windows_helper(session):
                    raise PolicyError("Cannot prove the terminal has stopped; close it and retry")
                self.wait_owned_stopped(session)
                return
            else:
                current = self.store.session(session["id"])
                windows().close(current["window_token"],
                                expected_identity=json.loads(current["window_identity"] or "null"))
                if not current["helper_identity"]:
                    raise PolicyError("Worker identity is unavailable; history preserved. Close/reconcile the terminal and retry.")
                if current["helper_identity"]:
                    deadline = time.monotonic() + 5
                    while not self.retired_windows_helper(current):
                        if time.monotonic() >= deadline:
                            raise PolicyError("Terminal closed but its worker has not stopped; history preserved. Retry deletion shortly.")
                        time.sleep(0.05)
                self.wait_owned_stopped(current)
                return
        elif session["status"] in ("working", "waiting", "idle"):
            raise PolicyError("This running session has no verified terminal association. Close its terminal before deleting it.")
        if session["helper_identity"] and not self.retired_windows_helper(session):
            raise PolicyError("Terminal worker has not been proven stopped; history preserved")
        if session["heartbeat"] and time.time() - session["heartbeat"] < 20:
            raise PolicyError("Terminal helper is still connected; close its terminal and retry")
        self.wait_owned_stopped(session)

    def wait_owned_stopped(self, session, timeout=5):
        owned = self.store.get_setting("owned_work:" + session["id"], [])
        deadline = time.monotonic() + timeout
        while any(not retired(identity) for identity in owned):
            if time.monotonic() >= deadline:
                raise PolicyError("Observed provider/background workers have not stopped; history preserved. Close owned work and retry.")
            time.sleep(0.05)

    def wrap_up_progress(self):
        progress = {}
        for key in self.store.get_setting("shutdown_targets", []):
            request = self.store.get_setting("wrap_up_request:" + key, {})
            message = self.store.message(request.get("message")) if request else None
            elapsed = max(0, time.time() - request.get("requested", time.time()))
            completed = bool(message and message["state"] == "completed")
            owned = self.store.get_setting("owned_work:" + key, [])
            outstanding = sum(not retired(identity) for identity in owned)
            runtime = self.store.get_setting("claude_runtime:" + key, {})
            progress[key] = {"state": "completed" if completed else "wait exceeded" if elapsed >= request.get("timeout", 300) else "waiting",
                             "elapsed_seconds": int(elapsed), "timeout_seconds": request.get("timeout", 300),
                             "message_state": message["state"] if message else "unknown",
                             "observed_workers_unresolved": outstanding,
                             "reported_background": runtime.get("background_count"),
                             "reported_scheduled": runtime.get("scheduled_count"),
                             "note": "Observed workers are a snapshot; detached/scheduled work still needs review."}
        return progress

    def verified_window(self, key):
        session = self.store.session(key)
        if not session["managed"] or not session["window_token"]:
            raise PolicyError("No managed terminal association")
        # A launch title plus a fresh helper heartbeat ties actions to a managed launch.
        if not session["heartbeat"] or time.time() - session["heartbeat"] > 20:
            raise PolicyError("Terminal association is not currently verified")
        handle = windows().find(session["window_token"])
        expected = json.loads(session["helper_identity"] or "null")
        if not expected or process_identity(session["process_id"]) != expected:
            raise PolicyError("Terminal helper identity is no longer valid")
        if sys.platform == "win32":
            from .win_identity import window_identity
            current_window = window_identity(handle)
            previous = json.loads(session["window_identity"] or "null")
            if previous and previous != current_window:
                raise PolicyError("Window identity changed; explicit reassociation is required")
            if not previous:
                self.store.update_session(key, window_identity=json.dumps(current_window))
        else:
            current_window = {"handle": handle[0] if isinstance(handle, list) else handle}
            previous = json.loads(session["window_identity"] or "null")
            if previous and previous != current_window:
                raise PolicyError("Window identity changed; explicit reassociation is required")
            if not previous:
                self.store.update_session(key, window_identity=json.dumps(current_window))
        return self.store.session(key)

    def window_action(self, action, params):
        adapter = windows()
        area = Rect(**params["area"])
        if action == "restore_layout":
            saved = self.store.get_setting("layout", [])
            results = []
            for item in saved:
                try:
                    session = self.verified_window(item["session"])
                    adapter.place(session["window_token"], Rect(**item["rect"]).clamped(area),
                                  expected_identity=json.loads(session["window_identity"] or "null"))
                    results.append({"session": session["id"], "ok": True})
                except (OSError, RuntimeError, ValueError) as exc:
                    results.append({"session": item["session"], "error": str(exc)})
            return results
        sessions = [self.verified_window(key) for key in params.get("sessions", [])]
        if not sessions:
            raise PolicyError("Select managed sessions first")
        if action == "save_layout":
            saved = [{"session": s["id"], "rect": asdict(adapter.geometry(s["window_token"],
                      expected_identity=json.loads(s["window_identity"] or "null"))),
                      "monitor": params.get("monitor", "")} for s in sessions]
            self.store.set_setting("layout", saved)
            return True
        rects = arrange(len(sessions), area, params["mode"])
        for session, rect in zip(sessions, rects):
            adapter.place(session["window_token"], rect,
                          expected_identity=json.loads(session["window_identity"] or "null"))
        return True

    def delivery_loop(self):
        while not self.stop.wait(1):
            try:
                self.store.expire_sending()
                for session in self.store.sessions():
                    # Claude uses explicit MCP pull until channel push is approved and validated.
                    if session["provider"] != "codex" or session["status"] not in ("working", "waiting", "idle"):
                        continue
                    with self.delivery_lock:
                        message = self.store.claim(session["id"])
                        if not message:
                            continue
                        try:
                            state, detail = deliver_codex(session, message)
                        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
                            state, detail = "delivery_unknown", "Provider delivery did not confirm acceptance"
                        self.store.delivery_result(message["id"], state, detail)
                self.delivery_error = ""
            except Exception as exc:  # noqa: BLE001 -- delivery worker reports errors without dropping queue
                self.delivery_error = str(exc)

    def refresh_loop(self):
        while not self.stop.wait(15):
            if self.store.sessions():
                try:
                    self.dispatch("refresh", {}, "operator")
                except (OSError, RuntimeError, ValueError):
                    log.debug("Provider refresh failed; snapshot records the error", exc_info=True)

    def refresh_providers(self):
        total, failures, succeeded = 0, [], False
        try:
            sessions = discover_codex()
            for session in sessions:
                self.store.upsert_session(**session)
            total += len(sessions)
            succeeded = True
        except (OSError, RuntimeError, ValueError) as exc:
            failures.append("Codex: " + str(exc))
            for session in self.store.sessions():
                if session["provider"] == "codex":
                    self.store.update_session(session["id"], status="unknown", latest="Provider connection unavailable")
        try:
            sessions, warning = discover_claude(shutil.which("claude"))
            self.provider_warnings["claude"] = warning
            for session in sessions:
                previous = next((s for s in self.store.sessions() if s["id"] == "claude:" + session["native_id"]), None)
                if previous and previous["managed"] and session["status"] == "unknown":
                    runtime = self.store.get_setting("claude_runtime:" + previous["id"], {})
                    if previous["heartbeat"] and time.time() - previous["heartbeat"] < 20 and runtime:
                        session.pop("status", None)
                        session.pop("latest", None)
                self.store.upsert_session(**session)
            total += len(sessions)
            succeeded = succeeded or bool(sessions) or bool(shutil.which("claude"))
        except (OSError, RuntimeError, ValueError) as exc:
            failures.append("Claude: " + str(exc))
        self.refresh_error = " · ".join(failures)
        if not succeeded and failures:
            raise PolicyError(self.refresh_error)
        return total

    def check_receipts(self):
        sessions = sorted((s for s in self.store.sessions() if s["provider"] == "codex"), key=lambda s: s["id"])
        live_ids = {s["id"] for s in sessions}
        self.receipt_errors = {key: value for key, value in self.receipt_errors.items() if key in live_ids}
        active = []
        for session in sessions:
            messages = self.store.receipt_messages(session["id"])
            if not messages:
                self.store.save_receipt_scan(session["id"], {})
                self.receipt_errors.pop(session["id"], None)
                continue
            active.append((session, messages))
        # Rotate fairly, keeping provider metadata refresh independent of receipt I/O.
        active.sort(key=lambda item: (item[0]["id"] <= self.last_receipt_session, item[0]["id"]))
        for session, messages in active[:4]:
            if self.stop.is_set():
                return
            key = session["id"]
            self.last_receipt_session = key
            try:
                state = self.store.get_setting("receipt_scan:" + key, {})
                receipts, state = scan_codex_receipts(session, messages, state)
                for mid, completed in receipts.items():
                    self.store.acknowledge(key, mid, completed)
                self.store.save_receipt_scan(key, state)
                if state.get("warning"):
                    self.receipt_errors[key] = session["name"] + ": " + state["warning"]
                else:
                    self.receipt_errors.pop(key, None)
            except (OSError, RuntimeError, ValueError) as exc:
                self.receipt_errors[key] = session["name"] + ": receipt check unavailable: " + str(exc)
                log.debug("Explicit Codex reply receipts unavailable", exc_info=True)

    def receipt_loop(self):
        while not self.stop.wait(15):
            try:
                self.check_receipts()
            except (OSError, RuntimeError, ValueError):
                log.debug("Receipt reconciliation failed", exc_info=True)


class LoopbackHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        # This local RPC endpoint has no hostname requirement. HTTPServer's default
        # getfqdn() can block startup on hosts with slow or broken reverse DNS.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class BrokerLock:
    def __init__(self, path):
        self.file = open(path, "a+b")  # noqa: SIM115 -- held for broker lifetime, closed by close()
        self.file.seek(0)
        self.file.write(b"0")
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("A broker already owns this data directory") from None

    def close(self):
        self.file.close()


def serve(root: Path):
    lock = BrokerLock(root / "broker.lock")
    broker = Broker(root)
    broker.store.recover()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                if self.path != "/rpc" or self.headers.get("Origin"):
                    raise PolicyError("Unsupported request origin")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise PolicyError("Invalid request size")
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                principal = ("operator", "operator") if secrets.compare_digest(token, broker.token) else broker.store.authenticate_principal(token)
                if not principal:
                    raise PolicyError("Authentication required")
                data = json.loads(self.rfile.read(length))
                result = broker.dispatch(data["action"], data.get("params", {}), *principal)
                payload, status = {"result": result}, 200
            except Exception as exc:  # noqa: BLE001 -- HTTP request boundary returns structured errors
                payload, status = {"error": str(exc)}, 400
            content = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = LoopbackHTTPServer(("127.0.0.1", 0), Handler)
    # Complete in-flight responses before process exit releases the broker lock.
    # In particular, stop sets the event before its HTTP response has been written.
    server.daemon_threads = False
    server.timeout = 0.5
    write_private(root / "broker.json", {"port": server.server_port, "token": broker.token})
    thread = threading.Thread(target=broker.delivery_loop, daemon=True)
    thread.start()
    refresh = threading.Thread(target=broker.refresh_loop, daemon=True)
    refresh.start()
    receipts = threading.Thread(target=broker.receipt_loop, daemon=True)
    receipts.start()
    try:
        while not broker.stop.is_set():
            server.handle_request()
    finally:
        broker.stop.set()
        deadline = time.monotonic() + 35
        for worker in (thread, refresh, receipts):
            worker.join(timeout=max(0, deadline - time.monotonic()))
        server.server_close()
        lock.close()


def ensure_broker(root):
    client = Client(root)
    try:
        if client.call("ping").get("build") == BUILD:
            return client
        # Upgrade the broker as well as the window. Helpers reconnect through broker.json;
        # their provider processes and durable messages remain independent.
        client.call("stop")
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                client.call("ping")
            except (OSError, ValueError):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Previous broker is still finishing delivery; reconnect after it stops")
    except (OSError, ValueError):
        log.debug("No live broker endpoint; starting broker", exc_info=True)
    options = hidden_options()
    if os.name == "nt":
        options["creationflags"] |= subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    subprocess.Popen([*own_command(console=True), "--broker", "--data-dir", str(root)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     **options)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            client.call("ping")
            return client
        except (OSError, ValueError):
            time.sleep(0.1)
    raise RuntimeError("Broker could not start; run the console entry point with --broker for diagnostics")
