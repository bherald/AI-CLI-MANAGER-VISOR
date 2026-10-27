"""Synthetic HTTP boundary and real-browser checks for the private Prism surface."""
import base64
import copy
import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from PySide6.QtGui import QImage

from visor.activity_components import catalog, template
from visor.activity_document import ActivityPolicy
from visor.prism_web import PrismWebServer, safe_html
from visor.store import PolicyError


class FakeClient:
    def __init__(self, root):
        self.calls = []
        self.lock = threading.Lock()
        self.policy = asdict(ActivityPolicy())
        self.fail_next_event_receipt = False
        self.fail_next_send_receipt = False
        self.sent = {}
        session = "claude:11111111-2222-4333-8444-555555555555"
        self.sessions = [{"id": session, "native_id": session.split(":")[1], "name": "Quality review",
                          "provider": "claude", "status": "waiting", "cwd": "/private/source/Serious project",
                          "description": "Release checks", "helper_identity": {"pid": 1234},
                          "credential": "private-helper-token", "window_identity": {"handle": 1}}]
        self.message = {"id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "sender": "operator",
                        "recipient": session, "kind": "message", "state": "acknowledged",
                        "body": "Please review the release", "created": 100, "updated": 110,
                        "origin": "operator", "detail": "private native path", "dedupe": "internal-request",
                        "reply": {"id": "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
                                  "parent_message_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                                  "sender": session, "recipient": "operator", "body": "Checks are ready",
                                  "created": 105, "parent_sha256": "private-binding-hash", "request_id": "private-request"}}
        document = template("status-board", "synthetic-board", "Project review")
        document["updated_at"] = "2026-10-27T17:05:00+00:00"
        document["cards"][0]["actions"] = [{"id": "review", "label": "Review checks"}]
        document["cards"] += [
            {"id": "notes", "kind": "text", "title": "Review notes",
             "html": '<p>Clear project status ✓</p><script>window.boardScriptRan=1</script><img src="https://invalid.example/private"><b onclick="alert(1)">Safe emphasis</b>'},
            {"id": "picture", "kind": "image", "title": "Project image", "path": "sample.png"},
            {"id": "target", "kind": "slider", "title": "Target", "action_id": "set-target",
             "value": 50, "min": 0, "max": 100, "step": 1},
            {"id": "decision", "kind": "question", "title": "Your decision", "prompt": "Next step?"},
        ]
        self.board = {"id": document["id"], "title": document["title"], "revision": 1,
                      "view_seq": 1, "updated": document["updated_at"], "document": document,
                      "owner": "hidden-provider-session-uuid", "owner_name": "Release coordinator",
                      "members": ["hidden-provider-session-uuid"],
                      "member_names": {"hidden-provider-session-uuid": "Quality review"},
                      "view_state": {"operator_dismissed": False},
                      "path": str(root / "board.json"),
                      "projection": {"state": "ready", "revision": 1, "error": "private path"}}

    def call(self, operation, **params):
        with self.lock:
            self.calls.append((operation, copy.deepcopy(params)))
            if operation == "snapshot":
                return {"sessions": copy.deepcopy(self.sessions), "messages": [copy.deepcopy(self.message)],
                        "pairs": [], "agent_enabled": False, "message_draft": "private draft",
                        "external_focus": {"pid": 1234}, "delivery_error": "private source path"}
            if operation == "ping":
                return {"version": "1.5.0", "build": "synthetic-build", "token": "private-broker-token"}
            if operation == "message_get":
                if params["message"] == self.message["id"]:
                    return copy.deepcopy(self.message)
                raise ValueError("Unknown exact message")
            if operation == "send":
                prior = self.sent.get(params["request_id"])
                if prior and prior[0] != params:
                    raise PolicyError("Conflicting send request")
                if not prior:
                    result = {**self.message, "id": str(uuid.uuid4()), "recipient": params["recipient"],
                              "body": params["body"], "state": "queued", "reply": None}
                    self.sent[params["request_id"]] = (copy.deepcopy(params), result)
                if self.fail_next_send_receipt:
                    self.fail_next_send_receipt = False
                    raise OSError("Synthetic lost send receipt")
                return copy.deepcopy(self.sent[params["request_id"]][1])
            if operation == "activity_list":
                board = copy.deepcopy(self.board)
                board.pop("document")
                return [board]
            if operation == "activity_get":
                if params["id"] != self.board["id"]:
                    raise ValueError("No board")
                return copy.deepcopy(self.board)
            if operation == "activity_limits":
                return self.policy
            if operation == "activity_catalog":
                return catalog()
            if operation == "activity_control":
                self.board["view_seq"] += 1
                self.board["view_state"]["operator_dismissed"] = params["action"] == "close"
            if operation == "activity_event" and self.fail_next_event_receipt:
                self.fail_next_event_receipt = False
                raise OSError("Synthetic lost receipt after acceptance")
            return {"accepted": True, "id": params.get("id", "intent-1"),
                    "revision": self.board["revision"], "request_id": params.get("request_id"),
                    "owner": "must-not-leak", "path": "/private/board.json"}


class PrismWebCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = FakeClient(self.root)
        (self.root / "board.json").write_text(json.dumps(self.client.board["document"]), encoding="utf-8")
        image = QImage(8, 6, QImage.Format.Format_RGB32)
        image.fill(0xFF008080)
        self.assertTrue(image.save(str(self.root / "sample.png")))
        self.app = PrismWebServer(self.root, client=self.client).start()
        self.addCleanup(self.app.close)

    def request(self, path, *, method="GET", body=None, auth=True, origin=True, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.app.httpd.server_port, timeout=5)
        self.addCleanup(connection.close)
        values = {}
        if auth:
            values["Authorization"] = "Bearer " + self.app.token
        if origin:
            values["Origin"] = self.app.origin
        if body is not None:
            body = json.dumps(body).encode() if not isinstance(body, bytes) else body
            values["Content-Type"] = "application/json"
        values.update(headers or {})
        connection.request(method, path, body, values)
        response = connection.getresponse()
        data = response.read()
        return response.status, data, dict(response.getheaders())

    def post(self, action, params, **kwargs):
        return self.request("/api/action", method="POST", body={"action": action, "params": params}, **kwargs)

    def test_loopback_capability_and_no_unauthenticated_data(self):
        self.assertEqual(self.app.httpd.server_address[0], "127.0.0.1")
        self.assertGreaterEqual(len(self.app.token), 40)
        self.assertIn("/#token=", self.app.url)
        other = PrismWebServer(self.root, client=self.client)
        self.addCleanup(other.close)
        self.assertNotEqual(other.token, self.app.token)
        for route in ("/api/boards", "/api/boards/synthetic-board", "/api/catalog",
                      "/api/asset?board=synthetic-board&path=sample.png"):
            self.assertEqual(self.request(route, auth=False)[0], 401)
        status, body, headers = self.request("/", auth=False, origin=False)
        self.assertEqual(status, 200)
        self.assertNotIn(self.app.token.encode(), body)
        self.assertNotIn(b"Project review", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.client.calls, [])

    def test_same_origin_and_host_are_enforced(self):
        params = {"id": "synthetic-board", "action": "open", "request_id": "open-1"}
        for headers in ({"Origin": "https://untrusted.example"}, {"Origin": "null"},
                        {"Host": "untrusted.example"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.post("activity_control", params, headers=headers)[0], 403)
        self.assertEqual(self.post("activity_control", params, origin=False)[0], 403)
        self.assertEqual(self.request("/api/boards", headers={"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.request("/api/boards", headers={"Authorization": "Bearer é"})[0], 401)
        self.assertEqual(self.client.calls, [])

    def test_board_projection_omits_paths_ids_and_sanitizes_html(self):
        status, payload, _ = self.request("/api/boards/synthetic-board")
        self.assertEqual(status, 200)
        board = json.loads(payload)
        self.assertEqual(board["owner_name"], "Release coordinator")
        self.assertNotIn("path", board)
        self.assertNotIn("members", board)
        self.assertNotIn("owner", board)
        self.assertEqual(board["projection"], {"state": "ready", "revision": 1})
        text = next(card for card in board["document"]["cards"] if card["id"] == "notes")["html"]
        self.assertIn("<b>Safe emphasis</b>", text)
        for unsafe in ("script", "onclick", "https:", "<img"):
            self.assertNotIn(unsafe, text)
        self.assertFalse(any(call[0] == "activity_view" for call in self.client.calls))

    def test_routes_and_action_fields_are_an_allowlist(self):
        for route in ("/rpc", "/api/snapshot", "/broker.json", "/../broker.json",
                      "/api/boards/../secret", "/api/boards/synthetic-board?token=x"):
            self.assertIn(self.request(route)[0], (400, 404))
        self.assertEqual(self.post("stop", {})[0], 400)
        params = {"id": "synthetic-board", "action": "open", "request_id": "open-2"}
        self.assertEqual(self.post("activity_control", {**params, "session": "private"})[0], 400)
        self.assertEqual(self.post("activity_control", {**params, "action": "delete"})[0], 400)
        status, payload, _ = self.post("activity_control", params)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["accepted"])
        self.assertNotIn(b"must-not-leak", payload)
        self.assertEqual(self.client.calls[-1], ("activity_control", params))

    def test_bounded_json_and_scalar_typed_events(self):
        params = {"id": "synthetic-board", "card_id": "target", "revision": 1,
                  "action_id": "set-target", "value": 70, "request_id": "event-1"}
        self.assertEqual(self.post("activity_event", params)[0], 200)
        for change in ({"value": {}}, {"value": []}, {"revision": True}, {"card_id": "../secret"}):
            self.assertEqual(self.post("activity_event", {**params, **change})[0], 400)
        self.assertEqual(self.request("/api/action", method="POST", body=b'{"action":"activity_event","params":{"value":NaN}}')[0], 400)
        self.assertEqual(self.request("/api/action", method="POST", body=b'{"action":"stop","action":"activity_list","params":{}}')[0], 400)
        self.assertEqual(self.request("/api/action", method="POST", body=b"{}", headers={"Content-Type": "text/plain"})[0], 400)
        self.assertEqual(self.request("/api/action", method="POST", body=b"{}", headers={"Content-Length": str(self.app.policy.max_document_bytes + 1)})[0], 413)

    def test_declared_assets_containment_type_and_live_policy(self):
        def asset(path):
            return self.request("/api/asset?" + urlencode({"board": "synthetic-board", "path": path}))
        status, data, headers = asset("sample.png")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertTrue(data.startswith(b"\x89PNG"))
        (self.root / "secret.png").write_bytes(data)
        for path in ("secret.png", "../sample.png", "/sample.png", "sample.png?private"):
            self.assertEqual(asset(path)[0], 400)
        self.client.policy["max_image_bytes"] = 1
        self.assertEqual(asset("sample.png")[0], 400)
        self.client.policy = asdict(ActivityPolicy())
        (self.root / "sample.png").write_bytes(b"not an image")
        self.assertEqual(asset("sample.png")[0], 400)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_declared_symlink_cannot_escape_board_directory(self):
        nested = self.root / "nested"
        nested.mkdir()
        projection = nested / "board.json"
        projection.write_text("{}", encoding="utf-8")
        self.client.board["path"] = str(projection)
        try:
            (nested / "sample.png").symlink_to(self.root / "sample.png")
        except OSError as error:
            self.skipTest(str(error))
        path = "/api/asset?" + urlencode({"board": "synthetic-board", "path": "sample.png"})
        self.assertEqual(self.request(path)[0], 400)

    def test_html_resource_and_active_content_rejected(self):
        value = safe_html('<svg><foreignObject><p>private</p></foreignObject></svg>'
                          '<p style="background:url(https://bad)">Good &amp; clear</p>'
                          '<iframe srcdoc="x">hidden</iframe><a href="javascript:x">Text</a>')
        self.assertEqual(value, "<p>Good &amp; clear</p>Text")

    def test_wildcard_binds_rejected_and_shutdown_releases_port(self):
        for host in ("0.0.0.0", "::", "224.0.0.1", "public.example", "::ffff:0.0.0.0",
                     "::ffff:0:0", "0:0:0:0:0:ffff:0:0", "::ffff:224.0.0.1",
                     "::ffff:e000:1", "::ffff:239.255.255.255"):
            with self.assertRaises(ValueError):
                PrismWebServer(self.root, host=host, client=self.client)
        self.app.close()
        self.assertFalse(self.app.thread.is_alive())
        self.app.close()
        with self.assertRaises(RuntimeError):
            self.app.start()

    def test_combined_visor_dto_and_bound_reply_omit_private_internals(self):
        self.assertEqual(self.request("/api/visor", auth=False)[0], 401)
        status, payload, _headers = self.request("/api/visor")
        self.assertEqual(status, 200)
        overview = json.loads(payload)
        self.assertEqual(set(overview), {"sessions", "messages", "pairs", "version", "build", "agent_enabled"})
        session = overview["sessions"][0]
        self.assertEqual(set(session), {"id", "name", "provider", "status", "project", "description"})
        self.assertEqual(session["project"], "Serious project")
        self.assertEqual(session["name"], "Quality review")
        self.assertEqual(overview["messages"][0]["sender_name"], "You")
        for secret in (b"private-helper-token", b"/private/source", b"private draft", b"helper_identity", b"window_identity", b"private-broker-token", b"internal-request"):
            self.assertNotIn(secret, payload)
        route = "/api/visor/message/" + self.client.message["id"]
        self.assertEqual(self.request(route, auth=False)[0], 401)
        status, payload, _headers = self.request(route)
        exact = json.loads(payload)
        self.assertEqual(status, 200)
        self.assertEqual(exact["updated"], 110)
        self.assertEqual(exact["reply"]["body"], "Checks are ready")
        self.assertEqual(exact["reply"]["sender_name"], "Quality review")
        self.assertEqual(set(exact["reply"]), {"id", "parent_message_id", "sender", "recipient", "sender_name", "recipient_name", "body", "created"})
        self.assertNotIn(b"private-binding-hash", payload)
        self.assertNotIn(b"private-request", payload)
        self.assertEqual(self.request("/api/visor/message/not-a-uuid")[0], 400)
        self.assertEqual(self.request(route.upper())[0], 404)
        self.assertEqual(self.request("/api/visor?action=stop")[0], 404)
        self.client.sessions[0]["name"] = self.client.sessions[0]["native_id"]
        self.assertEqual(json.loads(self.request("/api/visor")[1])["sessions"][0]["name"], "Claude session")

    def test_combined_send_exact_fields_text_uuid_and_idempotent_receipts(self):
        body = {"recipient": self.client.sessions[0]["id"], "body": "  Release review ✓  ",
                "request_id": str(uuid.uuid4())}
        route = "/api/visor/send"
        self.assertEqual(self.request(route, method="POST", body=body, auth=False)[0], 401)
        self.assertEqual(self.request(route, method="POST", body=body, origin=False)[0], 403)
        for change in ({"sender": "operator"}, {"body": "   "}, {"body": "x" * 16001},
                       {"body": "bad\x00text"}, {"body": "bad\ud800text"},
                       {"request_id": "not-a-uuid"}, {"request_id": str(uuid.uuid1())},
                       {"request_id": body["request_id"].upper()}, {"recipient": "operator"}):
            self.assertEqual(self.request(route, method="POST", body={**body, **change})[0], 400)
        status, payload, _headers = self.request(route, method="POST", body=body)
        receipt = json.loads(payload)
        self.assertEqual(status, 200)
        self.assertEqual(receipt["request_id"], body["request_id"])
        self.assertEqual(receipt["body"], "Release review ✓")
        self.assertEqual(receipt["state"], "queued")
        self.assertEqual(receipt["recipient_name"], "Quality review")
        self.assertNotIn("dedupe", receipt)
        self.assertNotIn("reply", receipt)
        replay = json.loads(self.request(route, method="POST", body=body)[1])
        self.assertEqual(receipt["id"], replay["id"])
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(self.request(route, method="POST", body={**body, "body": "Changed text"})[0], 409)
        self.client.fail_next_send_receipt = True
        body["request_id"] = str(uuid.uuid4())
        self.assertEqual(self.request(route, method="POST", body=body)[0], 503)
        self.assertEqual(self.request(route, method="POST", body=body)[0], 200)
        self.assertEqual(len(self.client.sent), 2)

    @unittest.skipUnless(shutil.which("google-chrome") or shutil.which("chromium"), "Chrome unavailable")
    def test_real_chrome_desktop_mobile_gadgets_and_intents(self):
        """CDP controls only a disposable synthetic board and a fresh browser profile."""
        from websockets.sync.client import connect

        cache = Path(__file__).resolve().parents[1] / ".cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="prism-web-browser-", dir=cache) as profile:
            process = subprocess.Popen([shutil.which("google-chrome") or shutil.which("chromium"),
                                        "--headless", "--no-sandbox", "--disable-gpu",
                                        "--no-first-run", "--disable-background-networking",
                                        "--remote-debugging-port=0", "--user-data-dir=" + profile,
                                        self.app.url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                active = Path(profile) / "DevToolsActivePort"
                deadline = time.monotonic() + 15
                while not active.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(active.exists(), "Chrome did not start")
                port = active.read_text().splitlines()[0]
                opener = build_opener(ProxyHandler({}))
                with opener.open(Request(f"http://127.0.0.1:{port}/json/list"), timeout=5) as response:
                    pages = json.load(response)
                page = next(page for page in pages if page.get("type") == "page")
                with connect(page["webSocketDebuggerUrl"], open_timeout=5) as wire:
                    serial = 0

                    def command(method, params=None):
                        nonlocal serial
                        serial += 1
                        wire.send(json.dumps({"id": serial, "method": method, "params": params or {}}))
                        while True:
                            result = json.loads(wire.recv(timeout=8))
                            if result.get("id") == serial:
                                self.assertNotIn("error", result)
                                return result.get("result", {})

                    def evaluate(expression):
                        result = command("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                              "awaitPromise": True})
                        self.assertNotIn("exceptionDetails", result)
                        return result["result"].get("value")

                    command("Emulation.setDeviceMetricsOverride", {"width": 1400, "height": 1000,
                                                                    "deviceScaleFactor": 1, "mobile": False})
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        if evaluate("document.querySelectorAll('.prism-card').length") == 10:
                            break
                        time.sleep(0.05)
                    self.assertEqual(evaluate("document.querySelectorAll('.prism-card').length"), 10)
                    self.assertIn("Quality review", evaluate("document.body.innerText"))
                    self.assertNotIn("hidden-provider-session-uuid", evaluate("document.body.innerText"))
                    self.assertFalse(evaluate("Boolean(window.boardScriptRan)"))
                    self.assertEqual(evaluate("location.hash"), "")
                    self.assertFalse(evaluate("Boolean(document.querySelector('script[src^=\"http\"],img[src^=\"http\"],iframe,svg foreignObject'))"))
                    self.assertTrue(evaluate("[...document.querySelectorAll('.card-options,.board-options')].every(item => !item.open)"))
                    self.assertTrue(evaluate("(() => {const card=document.querySelector('[data-card=\"gauge-1\"]');return card.querySelector('.direct-actions').getBoundingClientRect().top >= card.querySelector('svg').getBoundingClientRect().bottom;})()"))
                    evaluate("document.querySelector('[data-card=\"gauge-1\"] .direct-actions button').click()")
                    evaluate("document.querySelector('[data-card=\"target\"] input').value=70;document.querySelector('[data-card=\"target\"] .direct-actions button').click()")
                    deadline = time.monotonic() + 5
                    while sum(name == "activity_event" for name, _ in self.client.calls) < 2 and time.monotonic() < deadline:
                        time.sleep(0.05)
                    events = [params for name, params in self.client.calls if name == "activity_event"]
                    self.assertTrue(any(event["action_id"] == "review" and event["value"] is None for event in events))
                    self.assertTrue(any(event["action_id"] == "set-target" and event["value"] == 70 for event in events))
                    self.client.fail_next_event_receipt = True
                    evaluate("document.querySelector('[data-card=\"gauge-1\"] .direct-actions button').click()")
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline and not evaluate("document.querySelector('[data-card=\"gauge-1\"] .direct-actions').innerText.includes('Retry pending intent')"):
                        time.sleep(0.05)
                    original = [params for name, params in self.client.calls if name == "activity_event"][-1]
                    command("Page.reload")
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if evaluate("Boolean(document.querySelector('[data-card=\"gauge-1\"] .direct-actions')?.innerText.includes('Retry pending intent'))"):
                            break
                        time.sleep(0.05)
                    self.assertTrue(evaluate("document.querySelector('[data-card=\"gauge-1\"] .direct-actions').innerText.includes('Retry pending intent')"))
                    evaluate("[...document.querySelector('[data-card=\"gauge-1\"] .direct-actions').querySelectorAll('button')].find(button=>button.textContent==='Retry pending intent').click()")
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline and len([params for name, params in self.client.calls if name == "activity_event"]) < 4:
                        time.sleep(0.05)
                    retry = [params for name, params in self.client.calls if name == "activity_event"][-1]
                    self.assertEqual(original, retry, "A retry must retain its original request ID and value")
                    evaluate("document.getElementById('view').value='canvas';document.getElementById('view').dispatchEvent(new Event('change'))")
                    before = evaluate("parseInt(document.querySelector('[data-card=\"gauge-1\"]').style.left)")
                    evaluate("document.querySelector('[data-card=\"gauge-1\"] .move-handle').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}))")
                    self.assertEqual(evaluate("parseInt(document.querySelector('[data-card=\"gauge-1\"]').style.left)"), before + 10)
                    evaluate("document.getElementById('view').value='read';document.getElementById('view').dispatchEvent(new Event('change'))")
                    (cache / "prism-browser-desktop-review.png").write_bytes(base64.b64decode(command("Page.captureScreenshot", {"captureBeyondViewport": True})["data"]))
                    command("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844,
                                                                    "deviceScaleFactor": 1, "mobile": True})
                    self.assertTrue(evaluate("document.documentElement.scrollWidth <= innerWidth"))
                    self.assertTrue(evaluate("[...document.querySelectorAll('.prism-card')].every(card=>card.getBoundingClientRect().width<=innerWidth)"))
                    self.assertGreaterEqual(evaluate("document.getElementById('previous').getBoundingClientRect().width"), 64)
                    (cache / "prism-browser-mobile-review.png").write_bytes(base64.b64decode(command("Page.captureScreenshot")["data"]))
                    wire.send(json.dumps({"id": serial + 1, "method": "Browser.close"}))
            finally:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
