"""Optional private VISOR/Prism browser surface for an explicitly started operator.

``PrismWebServer(root).start().url`` is a capability URL for this server only.
Its fragment is never sent in HTTP requests; the browser exchanges it for bearer
headers. Default binding is loopback with an ephemeral port. An operator may
explicitly choose a particular interface IP for a trusted network; HTTP itself
does not encrypt that network connection. No broker credential reaches the page.
Call ``close()`` when its owning window exits. Static assets must be packaged
alongside this module in ``web/``. Published boards are data, never scripts.
"""
from __future__ import annotations

import copy
import hmac
import ipaddress
import json
import math
import mimetypes
import os
import re
import secrets
import socket
import stat
import threading
import uuid
from html import escape
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .activity_document import ActivityPolicy, resolve_image
from .broker import Client
from .store import PolicyError

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
_STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
           "/table.js": ("table.js", "text/javascript; charset=utf-8"),
           "/app.js": ("app.js", "text/javascript; charset=utf-8"),
           "/style.css": ("style.css", "text/css; charset=utf-8"),
           "/visor": ("visor.html", "text/html; charset=utf-8"),
           "/visor.js": ("visor.js", "text/javascript; charset=utf-8"),
           "/visor.css": ("visor.css", "text/css; charset=utf-8")}
_SESSION = re.compile(r"(?:codex|claude):[A-Za-z0-9][A-Za-z0-9._-]{0,191}\Z")
_ACTIONS = {
    "activity_control": {"id", "action", "request_id"},
    "activity_view": {"id", "state", "revision", "view_seq", "request_id"},
    "activity_reply": {"id", "card_id", "revision", "answer", "request_id"},
    "activity_edit": {"id", "card_id", "revision", "content", "request_id"},
    "activity_event": {"id", "card_id", "revision", "action_id", "value", "request_id"},
}
_SAFE_TAGS = frozenset({"p", "br", "hr", "h1", "h2", "h3", "h4", "b", "strong", "i", "em",
                        "u", "ul", "ol", "li", "table", "thead", "tbody", "tr", "td", "th",
                        "pre", "code", "blockquote", "span", "div", "small", "sub", "sup"})
_BLOCKED_TAGS = frozenset({"script", "style", "iframe", "object", "embed", "svg", "math",
                           "template", "textarea", "noscript"})


class _SafeHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.result = []
        self.blocked = []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCKED_TAGS:
            self.blocked.append(tag)
        elif not self.blocked and tag in _SAFE_TAGS:
            # Board-provided attributes, CSS, links and resource URLs are omitted.
            self.result.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if self.blocked:
            if tag == self.blocked[-1]:
                self.blocked.pop()
        elif tag in _SAFE_TAGS:
            self.result.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.blocked:
            self.result.append(escape(data))


def safe_html(value):
    """Conservative formatted text with no attributes, resources or active content."""
    parser = _SafeHTML()
    parser.feed(value)
    parser.close()
    return "".join(parser.result)


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("A safe Prism identifier is required")
    return value


def _canonical_uuid(value, *, version=None):
    if not isinstance(value, str):
        raise TypeError("A canonical UUID is required")
    parsed = uuid.UUID(value)
    if str(parsed) != value or version is not None and parsed.version != version:
        raise ValueError("A canonical UUID of the expected version is required")
    return value


def _session_names(sessions):
    names = {"operator": "You"}
    for session in sessions:
        value = session.get("name", "").strip()
        native = session.get("native_id", "")
        if not value or value in {native, session["id"]}:
            value = "Claude session" if session.get("provider") == "claude" else "Codex session"
        names[session["id"]] = value
    return names


def _safe_message(message, names, *, detail=False):
    fields = {"id", "sender", "recipient", "kind", "state", "body", "created", "origin"}
    if detail:
        fields.add("updated")
    result = {key: copy.deepcopy(value) for key, value in message.items() if key in fields}
    result["sender_name"] = names.get(message.get("sender"), "Session unavailable")
    result["recipient_name"] = names.get(message.get("recipient"), "Session unavailable")
    if detail:
        reply = message.get("reply")
        result["reply"] = None
        if isinstance(reply, dict):
            result["reply"] = {key: copy.deepcopy(value) for key, value in reply.items()
                               if key in {"id", "parent_message_id", "sender", "recipient", "body", "created"}}
            result["reply"]["sender_name"] = names.get(reply.get("sender"), "Session unavailable")
            result["reply"]["recipient_name"] = names.get(reply.get("recipient"), "Session unavailable")
    return result


def _json_constant(_value):
    raise ValueError("JSON numbers must be finite")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON fields are unsupported")
        result[key] = value
    return result


def _public_board(value):
    """Project only board data; local paths and broker/provider details stay in Python."""
    keys = {"id", "title", "revision", "updated", "view_action", "view_seq",
            "view_state", "member_names", "owner_name", "document", "projection"}
    result = copy.deepcopy({key: item for key, item in value.items() if key in keys})
    if "projection" in result:
        result["projection"] = {key: item for key, item in result["projection"].items()
                                if key in {"state", "revision"}}
    document = result.get("document", {})
    document.pop("session_link", None)
    document.pop("last_editor", None)
    contributors = document.pop("contributors", {})
    labels = document.pop("contributor_labels", {})
    document["contributor_labels"] = {
        card["id"]: labels.get(card.get("contributor", contributors.get(card["id"])), "Connected session")
        for card in document.get("cards", [])}
    for card in document.get("cards", []):
        card.pop("contributor", None)
        if card.get("kind") == "text":
            card["html"] = safe_html(card.get("html", ""))
    return result


class PrismWebServer:
    """One private operator server. Starting it does not open or acknowledge boards."""

    def __init__(self, root, host="127.0.0.1", port=0, *, client=None):
        host = "127.0.0.1" if host == "localhost" else host
        address = ipaddress.ip_address(host)
        mapped = getattr(address, "ipv4_mapped", None)
        if (address.is_unspecified or address.is_multicast
                or mapped is not None and (mapped.is_unspecified or mapped.is_multicast)):
            raise ValueError("Choose a specific local interface IP, not a wildcard address")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Port must be between zero and 65535")
        self.root = Path(root).resolve()
        self.client = client or Client(self.root)
        self.token = secrets.token_urlsafe(32)
        self.policy = ActivityPolicy()
        self.thread = None
        self._closed = False
        # Initialize Qt raster plugins on the owner thread before request workers.
        from PySide6.QtGui import QImageReader
        QImageReader.supportedImageFormats()
        server_class = ThreadingHTTPServer
        if address.version == 6:
            class IPv6Server(ThreadingHTTPServer):
                address_family = socket.AF_INET6
            server_class = IPv6Server
        self.httpd = server_class((host, port), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.prism = self
        public_host = f"[{host}]" if address.version == 6 else host
        self.authority = f"{public_host}:{self.httpd.server_port}"
        self.origin = "http://" + self.authority
        self.url = self.origin + "/#token=" + self.token
        self.visor_url = self.origin + "/visor#token=" + self.token

    def start(self):
        if self._closed:
            raise RuntimeError("This Prism browser server has closed")
        if self.thread is None:
            self.thread = threading.Thread(target=self.httpd.serve_forever,
                                           name="prism-browser", daemon=True)
            self.thread.start()
        return self

    def close(self):
        if not self._closed:
            self._closed = True
            if self.thread is not None and self.thread.is_alive():
                self.httpd.shutdown()
                self.thread.join(timeout=3)
            self.httpd.server_close()

    shutdown = close

    def __enter__(self):
        return self.start()

    def __exit__(self, *_args):
        self.close()

    def action(self, action, params):
        if action not in _ACTIONS or not isinstance(params, dict) or set(params) != _ACTIONS[action]:
            raise ValueError("Unsupported Prism operation or fields")
        for field in ("id", "card_id", "action_id", "request_id"):
            if field in params:
                _identifier(params[field])
        for field in ("revision", "view_seq"):
            if field in params and (type(params[field]) is not int or params[field] < 1):
                raise ValueError("A positive revision and view sequence are required")
        if action == "activity_control" and params["action"] not in {"open", "focus", "close", "clear"}:
            raise ValueError("Unsupported board action")
        if action == "activity_view" and params["state"] not in {"visible", "hidden", "closed"}:
            raise ValueError("Unsupported presentation state")
        if action == "activity_reply" and (not isinstance(params["answer"], str)
                or len(params["answer"]) > self.policy.max_answer_chars):
            raise ValueError("Reply exceeds the configured text limit")
        if action == "activity_edit":
            content = params["content"]
            if not isinstance(content, (str, list)):
                raise ValueError("An edit requires text or sketch strokes")
            if isinstance(content, str) and len(content) > self.policy.max_text_chars:
                raise ValueError("Edit exceeds the configured text limit")
        if action == "activity_event":
            value = params["value"]
            if type(value) not in {type(None), bool, int, float, str}:
                raise ValueError("Component events accept only scalar values")
            if isinstance(value, str) and len(value) > self.policy.max_text_chars:
                raise ValueError("Component event exceeds the configured text limit")
            if type(value) in {int, float} and not math.isfinite(value):
                raise ValueError("Component values must be finite")
        try:
            result = self.client.call(action, **params)
        except json.JSONDecodeError as exc:
            raise ConnectionError("The broker receipt was incomplete") from exc
        if not isinstance(result, dict):
            raise ConnectionError("The broker receipt was incomplete")
        return {**{key: value for key, value in result.items()
                   if key in {"id", "revision", "view_seq", "view_state", "accepted", "state"}},
                "request_id": params["request_id"]}

    def asset(self, board_id, relative):
        board = self.client.call("activity_get", id=_identifier(board_id))
        if not isinstance(relative, str) or not any(
                card.get("kind") == "image" and card.get("path") == relative
                for card in board["document"]["cards"]):
            raise ValueError("This image is not declared on the board")
        projection = Path(board["path"]).resolve(strict=True)
        if not projection.is_relative_to(self.root):
            raise ValueError("Board assets must remain inside private Prism state")
        policy = ActivityPolicy(**self.client.call("activity_limits"))
        image = resolve_image(projection, relative, policy)
        descriptor = os.open(image, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_BINARY", 0))
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            current = image.resolve(strict=True)
            # Recheck the opened file against its contained name after opening.
            if (not current.is_relative_to(projection.parent)
                    or not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (current.stat().st_dev, current.stat().st_ino)):
                raise ValueError("Image changed while opening")
            payload = stream.read(policy.max_image_bytes + 1)
        if len(payload) > policy.max_image_bytes:
            raise ValueError("Image exceeds the configured byte limit")
        return payload, mimetypes.guess_type(image.name)[0] or "application/octet-stream"

    def visor_snapshot(self):
        """Fixed operator DTO. Never serialize process identities, settings or paths."""
        snapshot = self.client.call("snapshot")
        ping = self.client.call("ping")
        names = _session_names(snapshot["sessions"])
        sessions = []
        for session in snapshot["sessions"]:
            sessions.append({"id": session["id"], "name": names[session["id"]],
                             "provider": session["provider"], "status": session["status"],
                             "project": Path(session.get("cwd", "").replace("\\", "/")).name,
                             "description": session.get("description", "")})
        pairs = [{"a": a, "b": b, "a_name": names.get(a, "Session unavailable"),
                  "b_name": names.get(b, "Session unavailable")} for a, b in snapshot["pairs"]]
        return {"sessions": sessions, "messages": [_safe_message(message, names)
                                                    for message in snapshot["messages"]],
                "pairs": pairs, "version": ping.get("version", ""),
                "build": ping.get("build", ""), "agent_enabled": bool(snapshot["agent_enabled"])}

    def visor_message(self, identity):
        message = self.client.call("message_get", message=_canonical_uuid(identity))
        names = _session_names(self.client.call("snapshot")["sessions"])
        return _safe_message(message, names, detail=True)

    def visor_send(self, body):
        if not isinstance(body, dict) or set(body) != {"recipient", "body", "request_id"}:
            raise ValueError("Sending requires exactly recipient, body and request_id")
        _canonical_uuid(body["request_id"], version=4)
        if not isinstance(body["recipient"], str) or not _SESSION.fullmatch(body["recipient"]):
            raise ValueError("Choose an existing session recipient")
        text = body["body"]
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 16000 or "\x00" in text:
            raise ValueError("Message requires 1–16000 usable characters")
        text.encode("utf-8", errors="strict")
        snapshot = self.client.call("snapshot")
        names = _session_names(snapshot["sessions"])
        if body["recipient"] not in names or body["recipient"] == "operator":
            raise ValueError("Choose an existing session recipient")
        try:
            message = self.client.call("send", recipient=body["recipient"], body=text.strip(),
                                       request_id=body["request_id"])
        except json.JSONDecodeError as exc:
            raise ConnectionError("The message receipt was incomplete") from exc
        if not isinstance(message, dict) or not message.get("id"):
            raise ConnectionError("The message receipt was incomplete")
        return {**_safe_message(message, names), "request_id": body["request_id"]}


class _Handler(BaseHTTPRequestHandler):
    server_version = "Prism"
    sys_version = ""
    timeout = 8

    def log_message(self, _format, *_args):
        # URLs may contain private board names. The capability is never logged.
        pass

    def _respond(self, status, payload, content_type="application/json; charset=utf-8"):
        if not isinstance(payload, bytes):
            payload = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; "
                         "base-uri 'none'; frame-ancestors 'none'; form-action 'none'; object-src 'none'")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.end_headers()
        self.wfile.write(payload)

    def _guard(self, *, authenticated=True, mutation=False):
        app = self.server.prism
        if (len(self.headers.get_all("Host", [])) != 1
                or self.headers.get("Host") != app.authority):
            self._respond(403, {"error": "Invalid request host"})
            return False
        origins = self.headers.get_all("Origin", [])
        if (len(origins) > 1 or (origins and origins[0] != app.origin)
                or (mutation and not origins)
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            self._respond(403, {"error": "Same-origin access is required"})
            return False
        if authenticated and (len(self.headers.get_all("Authorization", [])) != 1
                or not hmac.compare_digest(self.headers.get("Authorization", "").encode("utf-8"),
                                           ("Bearer " + app.token).encode("ascii"))):
            self._respond(401, {"error": "Open the private browser link from Visor"})
            return False
        return True

    def do_GET(self):
        target = urlsplit(self.path)
        if target.scheme or target.netloc or target.fragment:
            self._respond(400, {"error": "Invalid request target"})
            return
        if target.path in _STATIC:
            if not self._guard(authenticated=False):
                return
            name, content_type = _STATIC[target.path]
            self._respond(200, (Path(__file__).parent / "web" / name).read_bytes(), content_type)
            return
        if not self._guard():
            return
        app = self.server.prism
        try:
            if target.path == "/api/boards" and not target.query:
                result = [_public_board(board) for board in app.client.call("activity_list")]
            elif target.path.startswith("/api/boards/") and not target.query:
                identity = _identifier(unquote(target.path.removeprefix("/api/boards/")))
                result = _public_board(app.client.call("activity_get", id=identity))
            elif target.path == "/api/catalog" and not target.query:
                result = app.client.call("activity_catalog")
            elif target.path == "/api/visor" and not target.query:
                result = app.visor_snapshot()
            elif target.path.startswith("/api/visor/message/") and not target.query:
                result = app.visor_message(unquote(target.path.removeprefix("/api/visor/message/")))
            elif target.path == "/api/asset":
                query = parse_qs(target.query, strict_parsing=True, max_num_fields=2)
                if set(query) != {"board", "path"} or any(len(v) != 1 for v in query.values()):
                    raise ValueError("An image requires exactly one board and path")
                payload, content_type = app.asset(query["board"][0], query["path"][0])
                self._respond(200, payload, content_type)
                return
            else:
                self._respond(404, {"error": "Unknown Prism route"})
                return
            self._respond(200, result)
        except PolicyError:
            self._respond(409, {"error": "Prism state changed or the action was refused. Refresh and try again."})
        except (ValueError, TypeError, KeyError):
            self._respond(400, {"error": "Invalid Prism request or unavailable board data"})
        except (OSError, RuntimeError):
            self._respond(409, {"error": "Prism state changed or the local broker is unavailable. Refresh and try again."})

    def do_POST(self):
        if not self._guard(mutation=True):
            return
        if self.path not in {"/api/action", "/api/visor/send"}:
            self._respond(404, {"error": "Unknown Prism route"})
            return
        try:
            if (self.headers.get_content_type() != "application/json"
                    or self.headers.get("Transfer-Encoding") is not None
                    or len(self.headers.get_all("Content-Length", [])) != 1):
                raise ValueError("A bounded JSON body is required")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= self.server.prism.policy.max_document_bytes:
                self._respond(413, {"error": "Request exceeds the Prism size limit"})
                return
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("Incomplete JSON request")
            body = json.loads(data, parse_constant=_json_constant, object_pairs_hook=_unique_object)
            if self.path == "/api/visor/send":
                self._respond(200, self.server.prism.visor_send(body))
                return
            if not isinstance(body, dict) or set(body) != {"action", "params"}:
                raise ValueError("An action and exact parameters are required")
            self._respond(200, self.server.prism.action(body["action"], body["params"]))
        except PolicyError:
            self._respond(409, {"error": "Prism changed or the action was refused. Refresh before trying again."})
        except (ValueError, TypeError, KeyError, OverflowError):
            self._respond(400, {"error": "Invalid browser operation, fields or values"})
        except (OSError, RuntimeError):
            self._respond(503, {"error": "The receipt is uncertain. Retry the pending request when the broker is available."})


def start_prism_server(root, host="127.0.0.1", port=0, *, client=None):
    """Start a browser façade; caller owns opening its URL and closing the server."""
    return PrismWebServer(root, host, port, client=client).start()


def serve(root, host="127.0.0.1", port=0):
    """Foreground CLI surface for an existing local broker; Ctrl+C closes it."""
    with PrismWebServer(root, host, port).start() as server:
        print(server.url, flush=True)
        try:
            while server.thread.is_alive():
                server.thread.join(timeout=1)
        except KeyboardInterrupt:
            pass
