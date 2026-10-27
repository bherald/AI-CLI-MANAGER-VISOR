"""Actual scoped authoring workflow, read-only preflight and discoverable contracts."""
import base64
import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import asdict, replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from visor.activity_components import KINDS, component, template
from visor.activity_contract import LEGACY_KINDS
from visor.activity_document import ActivityPolicy, _validate
from visor.broker import Broker, Client, handler_for
from visor.paths import own_command, write_private
from visor.prism_web import PrismWebServer
from visor.store import PolicyError


class ActivityAuthoringCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.broker = Broker(self.root)
        self.store = self.broker.store
        self.owner = self.store.upsert_session("codex", "author", "Release author", str(self.root))
        self.peer = self.store.upsert_session("claude", "reviewer", "Review lead", str(self.root))
        self.other = self.store.upsert_session("codex", "other", "Unrelated", str(self.root))
        self.policy = ActivityPolicy()
        self.repository = Path(__file__).resolve().parents[1]
        self.transport = None

    def call(self, operation, principal=None, **params):
        return self.broker.dispatch(operation, params, principal or self.owner, "agent")

    def document(self, identity="board"):
        return {"schema_version": 1, "id": identity, "title": "Release review", "revision": 1,
                "cards": [component("table", id="checks", title="Checks")]
                }

    def state(self):
        with self.store.connect() as db:
            database = tuple(db.iterdump())
        files = {}
        activity_root = self.root / "activities"
        if activity_root.exists():
            files = {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
                     for path in activity_root.rglob("*") if path.is_file()}
        return database, files, copy.deepcopy(self.broker.activities._projection_cache)

    def assert_read_only(self, document, actor=None, **params):
        before = self.state()
        original = copy.deepcopy(document)
        with patch.object(self.broker.activities, "_projection", side_effect=AssertionError("Preflight must not project")), \
                patch.object(self.broker.activities, "_owner_dir", side_effect=AssertionError("Preflight must not create directories")):
            result = self.call("activity_validate", actor, document=document, **params)
        self.assertTrue(result["read_only"])
        self.assertEqual(document, original)
        self.assertEqual(self.state(), before)
        return result

    def share(self):
        self.store.toggle(True)
        self.store.participate(self.owner, True)
        self.store.participate(self.peer, True)
        self.store.pair(self.owner, self.peer)
        self.broker.activities.share(self.owner, "board", [self.peer], "share-board")

    def image(self, actor=None):
        image = QImage(2, 2, QImage.Format.Format_RGB32)
        image.fill(0xff417b77)
        output = QByteArray()
        buffer = QBuffer(output)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(image.save(buffer, "PNG"))
        return self.broker.activities.asset(actor or self.owner, "check.png", base64.b64encode(bytes(output)).decode())

    def test_catalog_is_structured_complete_and_reflects_operator_policy(self):
        policy = replace(self.policy, max_text_chars=123, max_items_per_card=17, max_cards=7, max_answer_chars=50)
        self.store.set_setting("activity_policy", asdict(policy))
        table = self.call("activity_catalog", kind="table")
        self.assertEqual(table["limits"], asdict(policy))
        self.assertEqual([entry["kind"] for entry in table["components"]], ["table"])
        schema = table["components"][0]["json_schema"]
        self.assertTrue({"id", "kind", "title", "columns", "rows"} <= set(schema["required"]))
        properties = schema["properties"]
        self.assertEqual(properties["columns"]["maxItems"], 17)
        self.assertEqual(properties["help"]["maxLength"], 123)
        self.assertEqual(properties["table_style"]["properties"]["cells"]["maxItems"], 17)
        self.assertEqual(properties["table_style"]["properties"]["header"]["properties"]["tooltip"]["maxLength"], 123)
        scalar = properties["rows"]["items"]["items"]
        alternatives = scalar["anyOf"]
        self.assertEqual(next(item for item in alternatives if item.get("type") == "string")["maxLength"], 123)
        self.assertEqual(next(item for item in alternatives if item.get("type") == "number")["maximum"], 1e15)
        full = self.call("activity_catalog", include_schema=True)
        self.assertEqual({entry["kind"] for entry in full["components"]}, KINDS | set(LEGACY_KINDS))
        self.assertTrue(all(isinstance(entry["json_schema"], dict) for entry in full["components"]))
        document_schema = full["document_schema"]
        self.assertEqual(document_schema["properties"]["cards"]["maxItems"], 7)
        self.assertEqual(len(document_schema["properties"]["cards"]["items"]["oneOf"]), len(KINDS) + len(LEGACY_KINDS))
        self.assertIn("activity_validate", " ".join(full["authoring"]["workflow"]))
        self.assertIn("row_ids", " ".join(full["authoring"]["defaults"]))
        for params in ({"kind": "missing"}, {"kind": []}, {"include_schema": "yes"}, {"actor": "operator"}):
            with self.subTest(params=params), self.assertRaises((PolicyError, ValueError)):
                self.call("activity_catalog", **params)

    def test_local_cli_hint_uses_current_install_and_is_scoped_without_credentials(self):
        tokens = [self.store.issue_credential(self.owner, role) for role in ("agent", "activity")]
        expected = [*own_command(console=True), "--data-dir", str(self.root), "--activity"]
        for role in ("agent", "activity"):
            with self.subTest(role=role):
                catalog = self.broker.dispatch("activity_catalog", {"kind": "table"}, self.owner, role)
                hint = catalog["local_cli"]
                self.assertEqual(hint["command"], expected)
                self.assertTrue(Path(hint["command"][0]).is_absolute())
                self.assertEqual(set(hint), {"command", "authentication", "purpose"})
                self.assertIn("existing --credential PATH", hint["authentication"])
                self.assertIn("Never use operator credentials", hint["authentication"])
                self.assertNotIn("--credential", hint["command"])
                raw = json.dumps(catalog)
                for token in [*tokens, self.broker.token]:
                    self.assertNotIn(token, raw)
                self.assertNotIn(str(self.root / "agent-credential.json"), raw)
        operator = self.broker.dispatch("activity_catalog", {"kind": "table"}, "operator", "operator")
        self.assertNotIn("local_cli", operator)
        self.assertNotIn(str(self.root), json.dumps(operator))
        self.assertNotIn(sys.executable, json.dumps(operator))

    def test_actual_browser_catalog_has_no_local_cli_paths_or_broker_credential(self):
        self.start_transport()
        activity_token = self.store.issue_credential(self.owner, "activity")
        scoped = Client(self.root, token=activity_token).call("activity_catalog", kind="table")
        self.assertIn("local_cli", scoped)
        browser = PrismWebServer(self.root).start()
        self.addCleanup(browser.close)
        request = Request(browser.origin + "/api/catalog", headers={"Authorization": "Bearer " + browser.token})
        with urlopen(request, timeout=5) as response:
            catalog = json.loads(response.read())
        self.assertNotIn("local_cli", catalog)
        raw = json.dumps(catalog)
        for private_value in (str(self.root), sys.executable, self.broker.token, activity_token, str(self.credential)):
            self.assertNotIn(private_value, raw)

    def test_help_and_data_status_are_literal_bounded_current_metadata(self):
        card = component("indicator", id="status", title="Status")
        document = {**self.document(), "cards": [card]}
        for state in ("ready", "loading", "empty", "error", "stale"):
            card.update(help="Literal <b>help</b> 😀", data_status={"state": state, "source": "Local checks",
                "message": "Current observation", "observed_at": "2026-10-27T12:30:00-04:00"})
            self.assertTrue(self.assert_read_only(document)["valid"])
        invalid = [{"help": "x" * 1001}, {"help": "bad\x00text"}, {"help": []},
                   {"data_status": {"state": "approved"}}, {"data_status": {"state": "ready", "extra": True}},
                   {"data_status": {"state": []}}, {"data_status": {"state": {}}},
                   {"data_status": {"state": "ready", "observed_at": "2026-10-27T12:30:00"}},
                   {"data_status": {"state": "ready", "observed_at": "not a timestamp"}},
                   {"data_status": {"state": "ready", "message": "x" * 401}},
                   {"data_status": {"state": "ready", "source": "x" * 161}}]
        for properties in invalid:
            with self.subTest(properties=properties):
                rejected = {**document, "cards": [{**card, **properties}]}
                result = self.assert_read_only(rejected)
                self.assertFalse(result["valid"])
                self.assertEqual(result["errors"][0]["code"], "invalid_schema")
                self.assertEqual(result["errors"][0]["path"], "/cards/0")
                with self.assertRaises(ValueError):
                    _validate(rejected, self.policy)
        small = replace(self.policy, max_text_chars=10)
        for properties in ({"help": "x" * 11}, {"data_status": {"state": "ready", "message": "x" * 11}},
                           {"data_status": {"state": "ready", "source": "x" * 11}}):
            with self.subTest(properties=properties), self.assertRaises(ValueError):
                _validate({**document, "title": "Review", "cards": [{"id": "note", "title": "Note", "kind": "text", "html": "Note", **properties}]}, small)

    def test_preflight_new_valid_and_invalid_styles_never_mutate_or_project(self):
        document = self.document()
        result = self.assert_read_only(document, expected_revision=0)
        self.assertTrue(result["valid"])
        self.assertEqual(set(result["checks"].values()), {"passed"})
        self.assertEqual(result["checked_revision"], 0)
        self.assertIn("does not reserve", result["publication"])
        document["cards"][0]["table_style"]["cells"] = [{"row": 0, "column": 0, "onclick": "run"}]
        result = self.assert_read_only(document)
        self.assertFalse(result["valid"])
        self.assertEqual(result["errors"][0]["path"], "/cards/0")
        self.assertEqual(result["checks"]["revision"], "not_checked")
        self.assertEqual(self.broker.activities.list(self.owner), [])

    def test_preflight_conflicts_ownership_and_current_permissions_are_read_only(self):
        document = self.document()
        self.broker.activities.publish(self.owner, document, "publish-board")
        conflict = self.assert_read_only(document, expected_revision=0)
        self.assertFalse(conflict["valid"])
        self.assertEqual(conflict["errors"][0]["code"], "revision_conflict")
        self.assertTrue(conflict["errors"][0]["retryable"])
        newer = self.broker.activities.get(self.owner, "board")["document"]
        newer["revision"] = 2
        denied = self.assert_read_only(newer, self.other, expected_revision=1)
        self.assertFalse(denied["valid"])
        self.assertEqual(denied["errors"][0]["code"], "access_unavailable")
        self.share()
        newer["cards"][0]["rows"][0][0] = "Another contributor's card changed"
        denied = self.assert_read_only(newer, self.peer, expected_revision=1)
        self.assertFalse(denied["valid"])
        self.assertEqual(denied["errors"][0]["code"], "card_ownership")
        original = self.broker.activities.get(self.peer, "board")["document"]
        original["revision"] = 2
        original["cards"].append(component("stat", id="peer-check", title="Peer check"))
        self.assertTrue(self.assert_read_only(original, self.peer, expected_revision=1)["valid"])
        self.store.pair(self.owner, self.peer, enabled=False)
        self.assertFalse(self.assert_read_only(original, self.peer, expected_revision=1)["valid"])

    def test_preflight_assets_check_contributor_custody_without_writing_files(self):
        asset = self.image()
        document = {**self.document("images"), "cards": [{"id": "image", "kind": "image", "title": "Check image", "path": asset["path"]}]}
        self.assertTrue(self.assert_read_only(document)["valid"])
        foreign = self.assert_read_only(document, self.other)
        self.assertFalse(foreign["valid"])
        self.assertEqual(foreign["errors"][0]["code"], "assets_unavailable")
        missing = copy.deepcopy(document)
        missing["cards"][0]["path"] = "assets/" + "a" * 64 + ".png"
        self.assertFalse(self.assert_read_only(missing)["valid"])
        with self.store.connect(True) as db:
            db.execute("UPDATE activity_assets SET content=?", (b"corrupt",))
        self.assertFalse(self.assert_read_only(document)["valid"])

    def test_preflight_role_fields_and_document_limits_do_not_create_receipts(self):
        document = self.document()
        for identity, role in (("operator", "operator"), (self.owner, "helper")):
            with self.subTest(role=role), self.assertRaises(PolicyError):
                self.broker.dispatch("activity_validate", {"document": document}, identity, role)
        for params in ({"document": document, "actor": self.other}, {}, {"document": document, "request_id": "not-a-write"}):
            with self.subTest(params=params), self.assertRaises(PolicyError):
                self.call("activity_validate", **params)
        too_big = replace(self.policy, max_document_bytes=100)
        self.store.set_setting("activity_policy", asdict(too_big))
        result = self.assert_read_only(document)
        self.assertFalse(result["valid"])
        self.assertEqual(result["errors"][0]["code"], "document_limit")

    def start_transport(self):
        self.transport = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.broker))
        self.transport.daemon_threads = True
        worker = threading.Thread(target=lambda: self.transport.serve_forever(poll_interval=.05), daemon=True)
        worker.start()
        def close():
            self.transport.shutdown()
            self.transport.server_close()
            worker.join(timeout=3)
        self.addCleanup(close)
        write_private(self.root / "broker.json", {"port": self.transport.server_port, "token": self.broker.token})
        token = self.store.issue_credential(self.owner, "agent")
        self.credential = self.root / "agent-credential.json"
        write_private(self.credential, {"root": str(self.root), "session": self.owner, "token": token})
        self.environment = {**os.environ, "PYTHONPATH": str(self.repository / "src"), "QT_QPA_PLATFORM": "offscreen"}

    def stdio_tool(self, name, arguments=None):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": name, "arguments": arguments or {}}}
        result = subprocess.run([sys.executable, "-m", "visor", "--mcp", "--credential", str(self.credential)],
            input=json.dumps(request) + "\n", text=True, capture_output=True, timeout=15, check=False,
            cwd=self.repository, env=self.environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(messages), 1, result.stdout)
        self.assertNotIn("error", messages[0], messages[0])
        response = messages[0]["result"]
        self.assertFalse(response.get("isError"), response)
        return json.loads(response["content"][0]["text"])

    def test_real_mcp_stdio_and_loopback_broker_complete_scoped_authoring_workflow(self):
        self.start_transport()
        catalog = self.stdio_tool("activity_catalog", {"kind": "table"})
        self.assertIn("json_schema", catalog["components"][0])
        document = self.stdio_tool("activity_template", {"name": "project-overview", "id": "pipeline", "title": "Synthetic release"})
        gauge = next(card for card in document["cards"] if card["kind"] == "gauge")
        gauge["actions"] = [{"id": "inspect-checks", "label": "Inspect checks"}]
        gauge["help"] = "Readiness from synthetic local checks."
        gauge["data_status"] = {"state": "ready", "source": "Isolated fixture", "observed_at": "2026-10-27T12:30:00Z"}
        before = self.state()
        feedback = self.stdio_tool("activity_validate", {"document": document, "expected_revision": 0})
        self.assertTrue(feedback["valid"])
        self.assertEqual(self.state(), before)
        publish = {"document": document, "expected_revision": 0, "request_id": "pipeline-publish"}
        receipt = self.stdio_tool("activity_publish", publish)
        self.assertTrue(receipt["accepted"])
        update = {"id": "pipeline", "card_id": gauge["id"], "changes": {"value": 99},
                  "expected_revision": 1, "request_id": "pipeline-update"}
        self.assertEqual(self.stdio_tool("activity_update_card", update)["revision"], 2)
        self.assertEqual(self.stdio_tool("activity_update_card", update)["revision"], 2)
        self.assertEqual(self.stdio_tool("activity_publish", publish)["revision"], 1)
        event = self.broker.dispatch("activity_event", {"id": "pipeline", "card_id": gauge["id"], "revision": 2,
            "action_id": "inspect-checks", "value": None, "request_id": "pipeline-intent"}, "operator", "operator")
        self.assertEqual(self.stdio_tool("activity_events", {"board_id": "pipeline"}), [event])
        self.assertEqual(self.call("activity_events", self.other), [])
        read = self.stdio_tool("activity_acknowledge", {"event_id": event["id"]})
        self.assertEqual(read["state"], "read")
        self.assertNotIn("completed", read)
        self.assertNotIn("approved", read)
        current = self.stdio_tool("activity_get", {"id": "pipeline"})
        self.assertEqual(current["revision"], 2)
        self.assertEqual(next(card for card in current["document"]["cards"] if card["id"] == gauge["id"])["value"], 99)

    def test_real_cli_discovers_table_schema_and_returns_preflight_feedback_without_publish(self):
        self.start_transport()
        def invoke(*arguments):
            result = subprocess.run([sys.executable, "-m", "visor", "--data-dir", str(self.root),
                "--credential", str(self.credential), "--activity", *arguments],
                text=True, capture_output=True, timeout=15, cwd=self.repository, env=self.environment, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        catalog = invoke("catalog", "--kind", "table", "--include-schema")
        self.assertEqual(catalog["components"][0]["kind"], "table")
        self.assertIn("document_schema", catalog)
        document = template("decision-board", "cli-pipeline", "CLI synthetic review")
        source = self.root / "author-document.json"
        write_private(source, document)
        before = self.state()
        self.assertTrue(invoke("validate", "--document", str(source), "--expected-revision", "0")["valid"])
        self.assertEqual(self.state(), before)
        table = next(card for card in document["cards"] if card["kind"] == "table")
        table["table_style"]["columns"] = [{"column": 999, "bold": True}]
        write_private(source, document)
        feedback = invoke("validate", "--document", str(source))
        self.assertFalse(feedback["valid"])
        self.assertEqual(feedback["errors"][0]["code"], "invalid_schema")
        self.assertEqual(json.loads(source.read_text()), document)
        self.assertEqual(self.broker.activities.list(self.owner), [])
