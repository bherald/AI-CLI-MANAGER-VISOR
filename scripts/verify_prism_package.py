"""Verify frozen Prism APIs and browser assets using disposable synthetic state only."""
from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from visor.broker import Client
from visor.paths import data_dir
from visor.process import hidden_options
from visor.store import PolicyError


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def wait_broker(process, data):
    client = Client(data)
    deadline = time.monotonic() + 25
    while process.poll() is None and time.monotonic() < deadline:
        try:
            client.call("ping")
            return client
        except (OSError, ValueError):
            time.sleep(0.05)
    raise RuntimeError("The packaged synthetic broker did not become ready")


def private_browser_link(process):
    """Read the capability through a pipe. Never put it in logs or exceptions."""
    lines = queue.Queue()

    def read_lines():
        for line in process.stdout:
            lines.put(line.strip())

    threading.Thread(target=read_lines, name="prism-acceptance-output", daemon=True).start()
    deadline = time.monotonic() + 25
    while process.poll() is None and time.monotonic() < deadline:
        try:
            line = lines.get(timeout=0.1)
        except queue.Empty:
            continue
        link = urlsplit(line)
        if link.scheme != "http" or link.hostname != "127.0.0.1" or not link.port:
            continue
        values = parse_qs(link.fragment)
        if set(values) == {"token"} and len(values["token"]) == 1 and len(values["token"][0]) >= 40:
            return f"http://127.0.0.1:{link.port}", values["token"][0]
    raise RuntimeError("The packaged browser did not supply a private loopback link")


def stop_process(process, *, interrupt=False):
    if process is None:
        return
    if process.poll() is None:
        try:
            if interrupt and os.name != "nt":
                process.send_signal(signal.SIGINT)
            else:
                process.terminate()
            process.wait(timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
    if process.stdout is not None:
        process.stdout.close()


def verify(executable):
    executable = Path(executable).resolve(strict=True)
    require(executable.is_file(), "Packaged console executable is missing")
    cache = root / ".cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prism-package-acceptance-", dir=cache) as temporary:
        work = Path(temporary)
        data = data_dir(str(work / "isolated data"))
        provider_path = work / "empty provider path"
        provider_path.mkdir()
        # No provider executable can be discovered, even during periodic refresh.
        # Do not change the user's provider home, credentials, or shell settings.
        env = {key: value for key, value in os.environ.items()
               if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}}
        env.update({"VISOR_SHELL_PATH": "0", "QT_QPA_PLATFORM": "offscreen",
                    "CLAUDE_CONFIG_DIR": str(work / "synthetic Claude config"),
                    "PATH": str(provider_path)})
        command = [str(executable), "--data-dir", str(data)]
        broker = browser = None
        operator = None
        try:
            broker = subprocess.Popen([*command, "--broker"], cwd=work, env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      **hidden_options())
            operator = wait_broker(broker, data)
            operator.call("toggle", enabled=False)
            for host in ("0.0.0.0", "::", "::ffff:0.0.0.0", "::ffff:e000:1"):
                refused = subprocess.run([*command, "--web", "--web-host", host], cwd=work, env=env,
                                         capture_output=True, text=True, timeout=10, check=False)
                require(refused.returncode != 0 and "specific local interface IP" in refused.stderr,
                        "Frozen browser accepted a wildcard or multicast bind alias")
            session = operator.call("register", provider="claude", native_id=str(uuid.uuid4()),
                                    name="Prism package reviewer", cwd=str(work))
            binding = operator.call("activity_bind", session=session, confirmed=True)
            credential_path = Path(binding["credential_file"])
            require(credential_path.resolve().is_relative_to(data), "Prism credential escaped test data")
            credential = json.loads(credential_path.read_text(encoding="utf-8"))
            scoped = Client(data, token=credential["token"])
            catalog = scoped.call("activity_catalog")
            require(catalog["schema_version"] == 1 and catalog["freeform"], "Frozen catalog schema mismatch")
            require("gauge" in {item["kind"] for item in catalog["components"]}, "Frozen gauge recipe missing")
            table_catalog = scoped.call("activity_catalog", kind="table")
            table_entry = table_catalog["components"][0]
            require(table_catalog["contract_revision"] >= 2
                    and table_entry["json_schema"]["properties"]["table_style"]["properties"]["border"]["properties"]["width"]["maximum"] == 8,
                    "Frozen table authoring schema or border limit is missing")
            document = scoped.call("activity_template", name="status-board", id="package-review",
                                   title="Synthetic packaged Prism")
            require(document["schema_version"] == 1 and document["revision"] == 1,
                    "Frozen template did not return a versioned board document")
            gauge = next(card for card in document["cards"] if card["kind"] == "gauge")
            gauge["actions"] = [{"id": "review-checks", "label": "Review checks"}]
            image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
                                     "AAAAC0lEQVR42mP8/x8AAwMCAO+jZakAAAAASUVORK5CYII=")
            asset = scoped.call("activity_asset", name="synthetic.png",
                                data_base64=base64.b64encode(image).decode("ascii"))
            document["cards"].append({"id": "image", "kind": "image", "title": "Synthetic image",
                                      "path": asset["path"]})
            table = table_entry["styled_example"]
            table["help"] = "Read the status labels; colors are supplementary."
            table["data_status"] = {"state": "stale", "message": "Awaiting final review",
                                    "source": "Synthetic package checks", "observed_at": "2026-10-27T12:00:00Z"}
            table["table_style"]["border"] = {"color": "#67C5C1", "width": 3, "style": "dashed"}
            document["cards"].append(table)
            check = scoped.call("activity_validate", document=document, expected_revision=0)
            require(check["valid"] and check["read_only"] and set(check["checks"].values()) == {"passed"},
                    "Frozen read-only authoring preflight failed")
            require(scoped.call("activity_list") == [], "Frozen preflight published a board")
            published = scoped.call("activity_publish", document=document,
                                    expected_revision=0, request_id="package-publish")
            require(published["revision"] == 1, "Frozen board publication failed")
            update = {"id": document["id"], "card_id": gauge["id"], "changes": {"value": 81},
                      "expected_revision": 1, "request_id": "package-update"}
            require(scoped.call("activity_update_card", **update)["revision"] == 2,
                    "Frozen card update did not advance the board revision")
            require(scoped.call("activity_update_card", **update)["revision"] == 2,
                    "Frozen card update retry was not idempotent")
            current = scoped.call("activity_get", id=document["id"])
            require(next(card for card in current["document"]["cards"] if card["id"] == gauge["id"])["value"] == 81,
                    "Frozen card update was not readable")
            require(current["owner_name"] == "Prism package reviewer", "Frozen board lacks readable owner name")
            params = {"id": document["id"], "card_id": gauge["id"], "revision": 2,
                      "action_id": "review-checks", "value": None, "request_id": "package-event"}
            event = operator.call("activity_event", **params)
            require(operator.call("activity_event", **params)["id"] == event["id"],
                    "Frozen typed event retry created a duplicate")
            intents = scoped.call("activity_events", board_id=document["id"])
            require(len(intents) == 1 and intents[0]["recipient"] == session
                    and intents[0]["action_id"] == "review-checks", "Frozen event recipient or shape mismatch")
            require(scoped.call("activity_acknowledge", event_id=event["id"])["state"] == "read",
                    "Frozen event acknowledgment did not mark read")
            try:
                scoped.call("activity_event", **{**params, "request_id": "forbidden-agent-event"})
            except PolicyError:
                pass
            else:
                raise RuntimeError("Scoped session was allowed to impersonate operator intent")
            cli = subprocess.run([*command, "--credential", str(credential_path), "--activity", "catalog"],
                                 cwd=work, env=env, capture_output=True, timeout=20,
                                 check=False, **hidden_options())
            require(cli.returncode == 0, "Frozen scoped authoring CLI failed: "
                    + cli.stderr.decode("utf-8", errors="replace")[-1000:])
            require(json.loads(cli.stdout)["schema_version"] == 1, "Frozen authoring CLI catalog mismatch")
            candidate = current["document"]
            candidate["revision"] = 3
            candidate_path = work / "synthetic-preflight.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            cli = subprocess.run([*command, "--credential", str(credential_path), "--activity", "validate",
                                  "--document", str(candidate_path), "--expected-revision", "2"],
                                 cwd=work, env=env, capture_output=True, timeout=20,
                                 check=False, **hidden_options())
            require(cli.returncode == 0 and json.loads(cli.stdout)["valid"], "Frozen authoring CLI preflight failed")
            browser = subprocess.Popen([*command, "--prism-web"], cwd=work, env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       text=True, encoding="utf-8", **hidden_options())
            origin, view_token = private_browser_link(browser)
            endpoint = json.loads((data / "broker.json").read_text(encoding="utf-8"))
            require(view_token not in {endpoint["token"], credential["token"]},
                    "Browser reused an operator or provider credential")
            opener = build_opener(ProxyHandler({}))

            def http(route, *, body=None, auth=True, headers=None):
                values = {"Origin": origin}
                if auth:
                    values["Authorization"] = "Bearer " + view_token
                if body is not None:
                    values["Content-Type"] = "application/json"
                    body = json.dumps(body).encode("utf-8")
                values.update(headers or {})
                request = Request(origin + route, data=body, headers=values)
                try:
                    response = opener.open(request, timeout=8)
                except HTTPError as error:
                    response = error
                with response:
                    return response.code, response.read(), response.headers

            for route, marker in (("/", b"Prism"), ("/app.js", b"activity_event"),
                                  ("/table.js", b"PrismTables"),
                                  ("/style.css", b".prism-card"), ("/visor", b"VISOR"),
                                  ("/visor.js", b"/api/visor"), ("/visor.css", b"{")):
                status, payload, headers = http(route, auth=False)
                require(status == 200 and marker in payload, "Frozen browser static asset is missing")
                require(view_token.encode() not in payload and endpoint["token"].encode() not in payload,
                        "Frozen browser exposed a private credential in static assets")
                require("frame-ancestors 'none'" in headers["Content-Security-Policy"],
                        "Frozen browser lacks its active-content boundary")
            require(http("/api/boards", auth=False)[0] == 401, "Unauthenticated browser data was allowed")
            require(http("/api/visor", auth=False)[0] == 401, "Unauthenticated VISOR data was allowed")
            require(http("/api/boards", headers={"Authorization": "Bearer invalid"})[0] == 401,
                    "Invalid browser credential was accepted")
            require(http("/api/boards", headers={"Origin": "https://untrusted.example"})[0] == 403,
                    "Cross-origin browser data was allowed")
            require(http("/api/boards", headers={"Host": "untrusted.example"})[0] == 403,
                    "Browser Host boundary was bypassed")
            status, payload, _headers = http("/api/boards/" + document["id"])
            board = json.loads(payload)
            require(status == 200 and board["revision"] == 2 and "path" not in board,
                    "Frozen browser board projection is incomplete or exposes local paths")
            require(board["owner_name"] == "Prism package reviewer", "Frozen browser lost session labels")
            require(http("/api/catalog")[0] == 200, "Frozen browser catalog route failed")
            route = "/api/asset?" + urlencode({"board": document["id"], "path": asset["path"]})
            status, payload, headers = http(route)
            require(status == 200 and payload == image and headers.get_content_type() == "image/png",
                    "Frozen browser could not serve a validated declared image")
            require(http(route, auth=False)[0] == 401, "Unauthenticated image access was allowed")
            require(http("/api/asset?" + urlencode({"board": document["id"], "path": "../../broker.json"}))[0] == 400,
                    "Frozen browser accepted asset traversal")
            require(http("/broker.json")[0] == 404, "Frozen browser exposed arbitrary files")
            require(http("/api/action", body={"action": "snapshot", "params": {}})[0] == 400,
                    "Frozen browser accepted arbitrary broker RPC")
            params["request_id"] = "package-browser-event"
            event_body = {"action": "activity_event", "params": params}
            status, payload, _headers = http("/api/action", body=event_body)
            receipt = json.loads(payload)
            require(status == 200 and receipt["request_id"] == params["request_id"],
                    "Frozen browser typed intent or receipt failed")
            require(json.loads(http("/api/action", body=event_body)[1])["id"] == receipt["id"],
                    "Frozen browser retry created a duplicate intent")
            invalid = {"action": "activity_event", "params": {**params, "value": {}, "request_id": "bad-value"}}
            require(http("/api/action", body=invalid)[0] == 400, "Frozen browser accepted a non-scalar intent")
            intents = scoped.call("activity_events", board_id=document["id"])
            require(len(intents) == 2 and any(item["id"] == receipt["id"] for item in intents),
                    "Frozen browser intent did not reach the scoped contributor exactly once")
            require(scoped.call("activity_acknowledge", event_id=receipt["id"])["state"] == "read",
                    "Frozen browser intent could not be acknowledged")
            status, payload, _headers = http("/api/visor")
            overview = json.loads(payload)
            require(status == 200 and set(overview) == {"sessions", "messages", "pairs", "version", "build", "agent_enabled"},
                    "Frozen combined VISOR overview returned unsupported fields")
            summary = next(item for item in overview["sessions"] if item["id"] == session)
            require(set(summary) == {"id", "name", "provider", "status", "project", "description"}
                    and summary["name"] == "Prism package reviewer" and summary["project"] == work.name,
                    "Frozen combined VISOR overview exposed process identity or omitted friendly names")
            send_body = {"recipient": session, "body": "Synthetic browser operator message", "request_id": str(uuid.uuid4())}
            require(http("/api/visor/send", body=send_body, auth=False)[0] == 401,
                    "Unauthenticated VISOR send was allowed")
            status, payload, _headers = http("/api/visor/send", body=send_body)
            sent = json.loads(payload)
            require(status == 200 and sent["request_id"] == send_body["request_id"] and sent["state"] == "queued",
                    "Frozen combined VISOR send or receipt failed")
            replay = json.loads(http("/api/visor/send", body=send_body)[1])
            require(replay["id"] == sent["id"], "Frozen combined VISOR retry duplicated a message")
            require(http("/api/visor/send", body={**send_body, "sender": "operator"})[0] == 400,
                    "Frozen combined VISOR accepted extra send authority")
            require(http("/api/visor/send", body={**send_body, "request_id": "noncanonical"})[0] == 400,
                    "Frozen combined VISOR accepted a noncanonical send request")
            status, payload, _headers = http("/api/visor/message/" + sent["id"])
            exact = json.loads(payload)
            require(status == 200 and exact["id"] == sent["id"] and exact["reply"] is None
                    and exact["recipient_name"] == "Prism package reviewer" and "dedupe" not in exact,
                    "Frozen combined VISOR exact message projection is invalid")
            stop_process(browser, interrupt=True)
            require(os.name == "nt" or browser.returncode == 0, "Packaged browser did not shut down cleanly")
            browser = None
            operator.call("stop")
            broker.wait(timeout=8)
            require(broker.returncode == 0, "Packaged synthetic broker exited with an error")
        finally:
            stop_process(browser, interrupt=True)
            if broker is not None and broker.poll() is None and operator is not None:
                try:
                    operator.call("stop")
                    broker.wait(timeout=8)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
            stop_process(broker)
    print("PASS: frozen Prism catalog/template/publish/update, scoped typed intents/read receipts, browser assets/auth/origin/routes/images, combined VISOR DTO/send/exact lookup, idempotent messages/events")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    verify(parser.parse_args().executable)


if __name__ == "__main__":
    main()
