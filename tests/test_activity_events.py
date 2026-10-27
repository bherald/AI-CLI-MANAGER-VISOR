"""Typed Prism intent, ownership, idempotence and authoring discovery; no providers."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from visor.activity_cli import ACTIVITY_TOOLS, run_activity
from visor.activity_store import ACTIVITY_TABLES, ActivityService
from visor.broker import Broker
from visor.mcp import handle
from visor.store import PolicyError


class ActivityEventCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.broker = Broker(self.root)
        self.store = self.broker.store
        self.owner = self.store.upsert_session("codex", "a", "Build lead", str(self.root))
        self.peer = self.store.upsert_session("claude", "b", "Test lead", str(self.root))
        self.other = self.store.upsert_session("codex", "c", "Unrelated", str(self.root))
        self.document = {"schema_version": 1, "id": "board", "title": "Release", "revision": 1,
                         "cards": [
                             {"id": "ready", "title": "Readiness", "kind": "gauge", "value": 95,
                              "actions": [{"id": "show-checks", "label": "View checks"}]},
                             {"id": "notify", "title": "Notify", "kind": "toggle", "action_id": "notify", "value": False},
                             {"id": "capacity", "title": "Capacity", "kind": "slider", "action_id": "capacity", "min": 0, "max": 100, "step": 5, "value": 50},
                             {"id": "stage", "title": "Stage", "kind": "select", "action_id": "stage", "choices": ["Review", "Ready"], "value": "Review"},
                             {"id": "note", "title": "Notes", "kind": "text", "html": "<p>Notes</p>",
                              "actions": [{"id": "inspect", "label": "Inspect"}]}]}
        self.broker.activities.publish(self.owner, self.document, "publish")

    def event(self, card="ready", action="show-checks", value=None, request="event", revision=1, **extra):
        params = {"id": "board", "card_id": card, "revision": revision,
                  "action_id": action, "value": value, "request_id": request, **extra}
        return self.broker.dispatch("activity_event", params, "operator", "operator")

    def read(self, actor=None):
        return self.broker.dispatch("activity_events", {}, actor or self.owner, "activity")

    def shared(self):
        self.store.toggle(True)
        for actor in (self.owner, self.peer):
            self.store.participate(actor, True)
        self.store.pair(self.owner, self.peer)
        self.broker.activities.share(self.owner, "board", [self.peer], "share")

    def test_typed_values_are_durable_and_acknowledgment_only_marks_read(self):
        intents = [("ready", "show-checks", None), ("notify", "notify", True),
                   ("capacity", "capacity", 65), ("stage", "stage", "Ready"), ("note", "inspect", None)]
        events = [self.event(card, action, value, request=f"event-{index}")
                  for index, (card, action, value) in enumerate(intents)]
        self.assertEqual({event["recipient"] for event in events}, {self.owner})
        self.assertEqual({event["state"] for event in self.read()}, {"pending"})
        self.assertEqual({event["card_id"]: event["value"] for event in self.read()},
                         {card: value for card, action, value in intents})
        restarted = ActivityService(self.store)
        self.assertEqual(len(restarted.events(self.owner)), 5)
        result = restarted.acknowledge(self.owner, events[1]["id"])
        self.assertEqual(result["state"], "read")
        self.assertIs(result["value"], True)
        self.assertNotIn("completed", result)
        self.assertNotIn("approved", result)
        self.assertEqual(restarted.get(self.owner, "board")["revision"], 1)
        self.assertEqual(restarted.acknowledge(self.owner, events[1]["id"]), result)

    def test_exact_request_replays_once_even_after_later_revision(self):
        event = self.event()
        newer = copy.deepcopy(self.document)
        newer["revision"] = 2
        self.broker.activities.publish(self.owner, newer, "publish-next", 1)
        self.assertEqual(self.event(), event)
        self.assertEqual(len(self.read()), 1)
        with self.assertRaisesRegex(PolicyError, "different content"):
            self.event(card="notify", action="notify", value=True)
        with self.assertRaisesRegex(PolicyError, "changed"):
            self.event(request="stale")

    def test_unknown_actions_wrong_types_ranges_and_extra_fields_cannot_enqueue(self):
        invalid = [{"action": "exec"}, {"value": "unexpected"}, {"card": "missing"},
                   {"card": "notify", "action": "notify", "value": 1},
                   {"card": "capacity", "action": "capacity", "value": 51},
                   {"card": "capacity", "action": "capacity", "value": float("nan")},
                   {"card": "stage", "action": "stage", "value": "Unlisted"}, {"completed": True}]
        for index, params in enumerate(invalid):
            with self.subTest(params=params), self.assertRaises((PolicyError, ValueError)):
                self.event(request=f"invalid-{index}", **params)
        self.assertEqual(self.read(), [])

    def test_event_submission_is_operator_only_and_does_not_accept_recipient_spoofing(self):
        params = {"id": "board", "card_id": "ready", "revision": 1,
                  "action_id": "show-checks", "value": None, "request_id": "agent-event"}
        for role in ("activity", "agent", "helper"):
            with self.subTest(role=role), self.assertRaises(PolicyError):
                self.broker.dispatch("activity_event", params, self.owner, role)
        with self.assertRaisesRegex(PolicyError, "require only"):
            self.event(recipient=self.other)
        self.assertEqual(self.read(), [])

    def test_shared_contributor_receives_own_events_and_pair_revocation_blocks_access(self):
        self.shared()
        current = self.broker.activities.get(self.peer, "board")["document"]
        current["revision"] = 2
        current["cards"].append({"id": "peer-action", "kind": "action", "title": "Inspect tests", "action_id": "inspect-tests"})
        self.broker.activities.publish(self.peer, current, "peer-publish", 1)
        event = self.event("peer-action", "inspect-tests", request="peer-event", revision=2)
        self.assertEqual(event["recipient"], self.peer)
        self.assertEqual(self.read(), [])
        self.assertEqual(self.read(self.peer), [event])
        self.assertEqual(self.read(self.other), [])
        with self.assertRaisesRegex(PolicyError, "unavailable"):
            self.broker.activities.acknowledge(self.owner, event["id"])
        self.store.pair(self.owner, self.peer, enabled=False)
        self.assertEqual(self.read(self.peer), [])
        with self.assertRaisesRegex(PolicyError, "unavailable"):
            self.broker.activities.acknowledge(self.peer, event["id"])

    def test_operator_receipts_require_board_and_omit_value(self):
        event = self.event("stage", "stage", "Ready")
        with self.assertRaisesRegex(PolicyError, "explicit board_id"):
            self.broker.dispatch("activity_events", {}, "operator", "operator")
        receipts = self.broker.dispatch("activity_events", {"board_id": "board"}, "operator", "operator")
        self.assertEqual(receipts[0]["id"], event["id"])
        self.assertNotIn("value", receipts[0])

    def test_delete_removes_events_and_cached_values_but_keeps_request_identity(self):
        event = self.event("stage", "stage", "Ready")
        self.broker.activities.delete("board", 1, "delete", confirmed=True)
        self.assertEqual(self.read(), [])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM activity_events").fetchone()[0], 0)
            result = json.loads(db.execute("SELECT result FROM activity_requests WHERE request_id='event'").fetchone()[0])
        self.assertEqual(result, {"id": event["id"], "board_id": "board", "deleted": True})
        self.assertIn("activity_events", ACTIVITY_TABLES)

    def test_catalog_templates_and_readable_names_have_no_publish_side_effect(self):
        for actor, role in ((self.owner, "activity"), ("operator", "operator")):
            catalog = self.broker.dispatch("activity_catalog", {}, actor, role)
            self.assertTrue(catalog["freeform"])
            self.assertIn("gauge", {item["kind"] for item in catalog["components"]})
            for name in catalog["templates"]:
                document = self.broker.dispatch("activity_template", {"name": name, "id": "starter", "title": "Starter"}, actor, role)
                self.assertEqual(document["id"], "starter")
                self.assertEqual(document["title"], "Starter")
        self.assertEqual(len(self.broker.activities.list(self.owner)), 1)
        self.shared()
        for board in (self.broker.activities.list(self.owner)[0], self.broker.activities.get(self.peer, "board")):
            self.assertEqual(board["owner_name"], "Build lead")
            self.assertEqual(board["member_names"], {self.owner: "Build lead", self.peer: "Test lead"})
        with self.store.connect(True) as db:
            db.execute("UPDATE sessions SET name='New lead' WHERE id=?", (self.owner,))
        self.assertEqual(self.broker.activities.get(self.owner, "board")["owner_name"], "New lead")

    def test_card_property_update_preserves_board_and_exact_retries(self):
        params = {"id": "board", "card_id": "ready", "changes": {"value": 99, "detail": "One check remains"},
                  "expected_revision": 1, "request_id": "update-readiness"}
        result = self.broker.dispatch("activity_update_card", params, self.owner, "activity")
        self.assertTrue(result["accepted"])
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["request_id"], "update-readiness")
        current = self.broker.activities.get(self.owner, "board")["document"]
        self.assertEqual(current["cards"][0]["value"], 99)
        self.assertEqual(current["cards"][1]["id"], "notify")
        self.broker.activities.update_card(self.owner, "board", "notify", {"value": True}, 2, "next-update")
        retry = self.broker.dispatch("activity_update_card", params, self.owner, "activity")
        self.assertEqual(retry["revision"], 2)
        self.assertEqual(self.broker.activities.get(self.owner, "board")["revision"], 3)
        for changes in ({"value": 95}, {"id": "replacement"}, {"kind": "action"}, {"contributor": self.peer}):
            with self.subTest(changes=changes), self.assertRaises(PolicyError):
                self.broker.dispatch("activity_update_card", {**params, "changes": changes}, self.owner, "activity")

    def test_card_update_recovers_publish_receipt_if_outer_record_was_lost(self):
        result = self.broker.activities.update_card(self.owner, "board", "ready", {"value": 99}, 1, "update-crash")
        with self.store.connect(True) as db:
            db.execute("DELETE FROM activity_requests WHERE request_id='update-crash'")
        recovered = self.broker.activities.update_card(self.owner, "board", "ready", {"value": 99}, 1, "update-crash")
        self.assertEqual(recovered["revision"], result["revision"])
        self.assertEqual(self.broker.activities.get(self.owner, "board")["revision"], 2)

    def test_card_update_preserves_contributor_and_revision_guards(self):
        self.shared()
        with self.assertRaisesRegex(PolicyError, "contributor"):
            self.broker.activities.update_card(self.peer, "board", "ready", {"value": 99}, 1, "not-owner")
        self.broker.activities.update_card(self.owner, "board", "ready", {"value": 99}, 1, "owner-update")
        with self.assertRaisesRegex(PolicyError, "revision conflict"):
            self.broker.activities.update_card(self.owner, "board", "ready", {"value": 85}, 1, "stale-update")

    def test_cli_and_mcp_expose_discovery_and_events_without_operator_credential(self):
        credential = self.root / "agent.json"
        credential.write_text(json.dumps({"root": str(self.root), "token": "scoped-token", "session": self.owner}))
        client = Mock()
        client.call.return_value = {"ok": True}
        with patch("visor.activity_cli.Client", return_value=client):
            for arguments, expected in ((["catalog"], ("activity_catalog", {})),
                                        (["template", "status-board", "--id", "release", "--title", "Release"],
                                         ("activity_template", {"name": "status-board", "id": "release", "title": "Release"})),
                                        (["events", "--board-id", "board"], ("activity_events", {"board_id": "board"}))):
                run_activity(["--credential", str(credential), *arguments], self.root)
                client.call.assert_called_with(expected[0], **expected[1])
            run_activity(["--credential", str(credential), "update-card", "board", "ready", "--changes", '{"value":95}',
                          "--expected-revision", "1", "--request-id", "update-cli"], self.root)
            client.call.assert_called_with("activity_update_card", id="board", card_id="ready", changes={"value": 95},
                                            expected_revision=1, request_id="update-cli")
        names = {tool["name"] for tool in ACTIVITY_TOOLS}
        self.assertTrue({"activity_catalog", "activity_template", "activity_events"} <= names)
        for name in ("activity_catalog", "activity_template", "activity_events"):
            response = handle({"method": "tools/call", "params": {"name": name, "arguments": {}}}, client)
            self.assertNotIn("isError", response)
            client.call.assert_called_with(name)
