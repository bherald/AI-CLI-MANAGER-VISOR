"""Real Chrome checks for optional Sessions, exact retry and independent receipts."""
import base64
import copy
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from visor.prism_web import PrismWebServer


class BrowserClient:
    def __init__(self):
        self.lock = threading.RLock()
        self.calls = []
        self.fail_next = False
        self.receipts = {}
        self.sessions = [
            {"id": "codex:hidden-build-session", "native_id": "hidden-build-session", "name": "Build lead",
             "provider": "codex", "status": "running", "cwd": "/private/work/project-review", "description": "Reviewing project evidence"},
            {"id": "claude:hidden-quality-session", "name": "Quality review", "provider": "claude",
             "status": "idle", "cwd": "/private/work/project-review", "description": "<img onerror='window.unsafeRan=1'>"},
        ]
        self.message = {"id": str(uuid.uuid4()), "sender": "operator", "recipient": self.sessions[0]["id"],
                        "origin": "operator", "kind": "message", "state": "delivered", "created": time.time(),
                        "updated": time.time(), "body": "Review this exact project message.", "reply": None}

    def call(self, operation, **params):
        with self.lock:
            self.calls.append((operation, copy.deepcopy(params)))
            if operation == "ping":
                return {"version": "1.5.0", "build": "synthetic-review"}
            if operation == "snapshot":
                return {"sessions": copy.deepcopy(self.sessions), "messages": [copy.deepcopy(self.message)],
                        "pairs": [(self.sessions[0]["id"], self.sessions[1]["id"])], "agent_enabled": True}
            if operation == "message_get":
                if params["message"] == self.message["id"]:
                    return copy.deepcopy(self.message)
                return next(copy.deepcopy(item) for item in self.receipts.values() if item["id"] == params["message"])
            if operation == "send":
                if params["request_id"] not in self.receipts:
                    self.receipts[params["request_id"]] = {**copy.deepcopy(self.message), "id": str(uuid.uuid4()),
                                                          "recipient": params["recipient"], "body": params["body"], "reply": None}
                if self.fail_next:
                    self.fail_next = False
                    raise ConnectionError("Synthetic accepted message, receipt unavailable")
                return copy.deepcopy(self.receipts[params["request_id"]])
            raise AssertionError(operation)


@unittest.skipUnless(shutil.which("google-chrome") or shutil.which("chromium"), "Chrome unavailable")
class VisorBrowserUI(unittest.TestCase):
    def test_browser_exact_retry_receipts_drafts_responsive_and_lock(self):
        from websockets.sync.client import connect

        cache = Path(__file__).resolve().parents[1] / ".cache"
        cache.mkdir(exist_ok=True)
        client = BrowserClient()
        with tempfile.TemporaryDirectory(prefix="visor-browser-", dir=cache) as profile:
            server = PrismWebServer(Path(profile) / "state", client=client).start()
            process = subprocess.Popen([shutil.which("google-chrome") or shutil.which("chromium"),
                                        "--headless", "--no-sandbox", "--disable-gpu", "--no-first-run",
                                        "--disable-background-networking", "--remote-debugging-port=0",
                                        "--user-data-dir=" + str(Path(profile) / "chrome"),
                                        server.origin + "/visor#token=" + server.token],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                active = Path(profile) / "chrome/DevToolsActivePort"
                deadline = time.monotonic() + 15
                while not active.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(active.exists(), "Chrome did not start")
                port = active.read_text().splitlines()[0]
                with build_opener(ProxyHandler({})).open(Request(f"http://127.0.0.1:{port}/json/list"), timeout=5) as response:
                    page = next(item for item in json.load(response) if item.get("type") == "page")
                with connect(page["webSocketDebuggerUrl"], open_timeout=5) as wire:
                    serial = 0

                    def command(method, params=None):
                        nonlocal serial
                        serial += 1
                        wire.send(json.dumps({"id": serial, "method": method, "params": params or {}}))
                        while True:
                            result = json.loads(wire.recv(timeout=10))
                            if result.get("id") == serial:
                                self.assertNotIn("error", result)
                                return result.get("result", {})

                    def evaluate(expression):
                        result = command("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
                        self.assertNotIn("exceptionDetails", result)
                        return result["result"].get("value")

                    def until(expression):
                        stop = time.monotonic() + 8
                        while time.monotonic() < stop:
                            if evaluate(expression):
                                return
                            time.sleep(.05)
                        self.fail("Browser did not reach: " + expression)

                    command("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 1050, "deviceScaleFactor": 1, "mobile": False})
                    until("document.querySelectorAll('.session').length===2")
                    self.assertEqual(evaluate("location.hash"), "")
                    self.assertIn("Build lead", evaluate("document.body.innerText"))
                    self.assertNotIn("hidden-build-session", evaluate("document.body.innerText"))
                    self.assertNotIn("/private/work", evaluate("document.body.innerText"))
                    self.assertFalse(evaluate("Boolean(window.unsafeRan)"))
                    evaluate("document.querySelector('.message-actions button').click()")
                    until("document.getElementById('receipt').innerText.includes('Your exact message')")
                    client.message["reply"] = {"id": str(uuid.uuid4()), "parent_message_id": client.message["id"],
                                               "sender": client.sessions[0]["id"], "recipient": "operator", "body": "Verified exact reply ✓", "created": time.time()}
                    evaluate("document.getElementById('refresh').click()")
                    until("document.getElementById('reply').innerText.includes('Verified exact reply')")
                    self.assertEqual(evaluate("[...document.querySelectorAll('.receipt-stages dd')].map(x=>x.textContent)"),
                                     ["Delivered", "Not recorded", "Not recorded"])
                    client.message["state"] = "acknowledged"
                    evaluate("document.getElementById('refresh').click()")
                    until("document.querySelectorAll('.receipt-stages dd')[1].textContent==='Acknowledged'")
                    client.fail_next = True
                    evaluate("document.getElementById('message-body').value='\\u0085Please verify the release ✓\\u001c';document.getElementById('message-body').dispatchEvent(new Event('input'));document.getElementById('composer').requestSubmit()")
                    until("!document.getElementById('pending-note').hidden && document.getElementById('send').textContent==='Retry same message'")
                    original = evaluate("JSON.parse(localStorage.getItem('visor.browser.pending'))")
                    self.assertEqual(set(original), {"recipient", "body", "request_id"})
                    self.assertEqual(original["body"], "Please verify the release ✓")
                    self.assertEqual(uuid.UUID(original["request_id"]).version, 4)
                    self.assertTrue(evaluate("document.getElementById('message-body').disabled"))
                    evaluate("document.getElementById('clear-retry').click()")
                    self.assertEqual(evaluate("document.activeElement.value"), "cancel")
                    evaluate("document.querySelector('#clear-dialog [value=cancel]').click()")
                    self.assertEqual(evaluate("JSON.parse(localStorage.getItem('visor.browser.pending'))"), original)
                    command("Page.reload")
                    until("Boolean(document.getElementById('pending-note')) && !document.getElementById('pending-note').hidden && document.getElementById('connection').textContent.includes('Connected')")
                    self.assertEqual(evaluate("JSON.parse(localStorage.getItem('visor.browser.pending'))"), original)
                    evaluate("document.getElementById('composer').requestSubmit()")
                    until("localStorage.getItem('visor.browser.pending')===null")
                    sends = [params for name, params in client.calls if name == "send"]
                    self.assertEqual(sends, [original, original])
                    self.assertEqual(len(client.receipts), 1)
                    evaluate("document.getElementById('message-body').value='Separate unsent follow-up';document.getElementById('message-body').dispatchEvent(new Event('input'))")
                    command("Page.reload")
                    until("document.getElementById('message-body')?.value==='Separate unsent follow-up'")
                    (cache / "visor-browser-desktop-review.png").write_bytes(base64.b64decode(command("Page.captureScreenshot", {"captureBeyondViewport": True})["data"]))
                    for width in [390, 768, 1440]:
                        command("Emulation.setDeviceMetricsOverride", {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": width < 700})
                        self.assertTrue(evaluate("document.documentElement.scrollWidth<=innerWidth"), width)
                        self.assertTrue(evaluate("[...document.querySelectorAll('.panel')].every(x=>x.getBoundingClientRect().width<=innerWidth)"))
                    command("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
                    (cache / "visor-browser-mobile-review.png").write_bytes(base64.b64decode(command("Page.captureScreenshot")["data"]))
                    evaluate("document.getElementById('lock').click()")
                    self.assertEqual(evaluate("sessionStorage.getItem('prism.token')"), None)
                    self.assertEqual(evaluate("document.querySelectorAll('.message,.session').length"), 0)
                    self.assertEqual(evaluate("document.getElementById('message-body').value"), "")
                    self.assertIn("Separate unsent follow-up", evaluate("localStorage.getItem('visor.browser.draft')"))
                    command("Browser.close")
                    process.wait(timeout=5)
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                server.close()


if __name__ == "__main__":
    unittest.main()
