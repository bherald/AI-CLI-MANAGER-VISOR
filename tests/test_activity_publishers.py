"""Publication delegation keeps authorship and operator input on existing cards."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from visor.activity_preflight import preflight
from visor.activity_store import ActivityService
from visor.broker import Broker
from visor.store import PolicyError, Store


class PublisherCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.owner = self.store.upsert_session("codex", "contributor", "Contributor", str(Path(self.temp.name)))
        self.runner = self.store.upsert_session("codex", "publisher", "Publisher", str(Path(self.temp.name)))
        self.other = self.store.upsert_session("claude", "other", "Other contributor", str(Path(self.temp.name)))
        self.service = ActivityService(self.store)
        self.store.toggle(True)
        for actor in (self.owner, self.runner, self.other):
            self.store.participate(actor, True)
        for first, second in ((self.owner, self.runner), (self.owner, self.other), (self.runner, self.other)):
            self.store.pair(first, second)
        self.service.publish(self.owner, {"schema_version": 1, "id": "board", "title": "Existing board", "revision": 1,
            "cards": [{"id": "text", "kind": "text", "title": "Progress", "html": "<p>Before</p>",
                       "actions": [{"id": "inspect", "label": "Inspect"}]},
                      {"id": "question", "kind": "question", "title": "Decision", "prompt": "Continue?", "choices": ["Yes", "No"]}]}, "initial")
        self.service.share(self.owner, "board", [self.runner, self.other], "members")
        document = self.service.get(self.other, "board")["document"]
        document["revision"] = 2
        document["cards"].append({"id": "other", "kind": "text", "title": "Other work", "html": "<p>Retain</p>"})
        self.service.publish(self.other, document, "other-content", 1)
        self.service.control(None, "board", "close", "operator-dismiss")

    def grant(self):
        return self.service.publisher_set(self.owner, "board", self.owner, self.runner, 2, 0, "grant", ["text", "question"])

    def changed(self):
        document = copy.deepcopy(self.service.get(self.runner, "board")["document"])
        document["revision"] += 1
        document["cards"][0]["html"] = "<p>Current result</p>"
        return document

    def test_delegated_publish_preserves_content_owners_history_members_dismissal_and_input_recipients(self):
        before = self.service.get(self.owner, "board")
        old_reply = self.service.reply("board", "question", 2, "Yes", "old-answer")
        grant = self.grant()
        self.assertEqual(before, self.service.get(self.owner, "board"))
        self.assertEqual(grant, self.grant())
        document = self.changed()
        check = preflight(self.service, self.runner, document, 2, {self.owner: 1})
        self.assertTrue(check["valid"], check)
        self.service.publish(self.runner, document, "delegated", 2, {self.owner: 1})
        after = self.service.get(self.runner, "board")
        self.assertEqual(before["owner"], after["owner"])
        self.assertEqual(before["members"], after["members"])
        self.assertEqual({key: value for key, value in before["view_state"].items() if key != "unshown_revision"},
                         {key: value for key, value in after["view_state"].items() if key != "unshown_revision"})
        self.assertEqual(3, after["view_state"]["unshown_revision"])
        self.assertEqual(before["document"]["session_link"], after["document"]["session_link"])
        self.assertEqual(before["document"]["contributors"], after["document"]["contributors"])
        self.assertEqual(before["document"]["cards"][2], after["document"]["cards"][2])
        self.assertEqual(after["document"]["last_editor"], self.runner)
        reply = self.service.reply("board", "question", 3, "No", "new-answer")
        event = self.service.event("board", "text", 3, "inspect", None, "new-event")
        self.assertEqual(self.owner, reply["recipient"])
        self.assertEqual(self.owner, event["recipient"])
        self.assertIn(old_reply["id"], [row["id"] for row in self.service.replies(self.owner)])
        self.assertEqual([], self.service.replies(self.runner))
        self.assertEqual([], self.service.events(self.runner))

    def test_unauthorized_scope_revision_and_nonmember_grants_refuse_without_board_changes(self):
        before = self.service.get(self.owner, "board")
        for actor, publisher, cards, revision, grant_revision in [
            (self.runner, self.runner, ["text"], 2, 0), (self.owner, self.runner, ["other"], 2, 0),
            (self.owner, "codex:missing", ["text"], 2, 0), (self.owner, self.runner, ["text"], 1, 0),
            (self.owner, self.runner, ["text"], 2, 1), (self.owner, self.runner, ["text", "text"], 2, 0),
        ]:
            with self.subTest(actor=actor, publisher=publisher, cards=cards, revision=revision, grant_revision=grant_revision), \
                    self.assertRaises(PolicyError):
                self.service.publisher_set(actor, "board", self.owner, publisher, revision, grant_revision, "bad", cards)
        self.assertEqual(before, self.service.get(self.owner, "board"))

    def test_revocation_and_changed_grant_refuse_new_publish_but_keep_prior_receipts(self):
        self.grant()
        document = self.changed()
        for grants in (None, {self.owner: 2}, {self.owner: True}):
            with self.subTest(grants=grants), self.assertRaises(PolicyError):
                self.service.publish(self.runner, document, "wrong-grant", 2, grants)
        result = self.service.publish(self.runner, document, "written-once", 2, {self.owner: 1})
        revoked = self.service.publisher_set(self.owner, "board", self.owner, self.runner, 3, 1, "revoke", revoke=True)
        self.assertFalse(revoked["enabled"])
        self.assertEqual(2, revoked["grant_revision"])
        self.assertFalse(self.service.publisher_get(self.runner, "board", self.owner, self.runner)["enabled"])
        self.assertEqual(result, self.service.publish(self.runner, document, "written-once", 2, {self.owner: 1}))
        with self.assertRaises(PolicyError):
            self.service.publish(self.runner, self.changed(), "new-after-revoke", 3, {self.owner: 1})
        self.assertEqual(3, self.service.get(self.owner, "board")["revision"])

    def test_revocation_during_asset_validation_is_rechecked_in_publish_transaction(self):
        self.grant()
        assets = self.service._assets
        document = self.changed()

        def revoke(db, document, policy):
            self.service.publisher_set(self.owner, "board", self.owner, self.runner, 2, 1, "raced-revoke", revoke=True)
            return assets(db, document, policy)

        with patch.object(self.service, "_assets", side_effect=revoke), self.assertRaisesRegex(PolicyError, "revoked or changed"):
            self.service.publish(self.runner, document, "raced-publish", 2, {self.owner: 1})
        self.assertEqual(2, self.service.get(self.owner, "board")["revision"])

    def test_delegation_cannot_add_remove_reorder_cards_or_change_unrelated_content_or_board_metadata(self):
        self.grant()
        changes = [lambda doc: doc.update(title="Changed title"), lambda doc: doc["cards"].pop(),
                   lambda doc: doc["cards"].reverse(), lambda doc: doc["cards"][2].update(html="<p>Wrong scope</p>"),
                   lambda doc: doc["cards"].append({"id": "new", "kind": "text", "title": "New", "html": "<p>New</p>"})]
        for change in changes:
            document = self.changed()
            change(document)
            with self.subTest(document=document), self.assertRaises(PolicyError):
                self.service.publish(self.runner, document, "bad-scope", 2, {self.owner: 1})
        self.assertEqual(2, self.service.get(self.owner, "board")["revision"])

    def test_normal_broker_identity_controls_and_delegated_card_update_keep_contributor(self):
        broker = object.__new__(Broker)
        broker.store, broker.activities = self.store, self.service
        params = {"id": "board", "contributor": self.owner, "publisher": self.runner,
                  "expected_revision": 2, "expected_grant_revision": 0, "request_id": "broker-grant", "card_ids": ["text"]}
        result = broker.activity_action("activity_publisher_grant", params, self.owner, "activity")
        self.assertEqual(1, result["grant_revision"])
        with self.assertRaises(PolicyError):
            broker.activity_action("activity_publisher_grant", params, self.runner, "activity")
        with self.assertRaises(PolicyError):
            broker.activity_action("activity_publisher_get", {"id": "board", "contributor": self.owner, "publisher": self.runner}, self.runner, "helper")
        self.service.update_card(self.runner, "board", "text", {"html": "<p>Updated</p>"}, 2, "scoped-update", {self.owner: 1})
        self.assertEqual(self.owner, self.service.get(self.owner, "board")["document"]["cards"][0]["contributor"])
        operator = {**params, "expected_revision": 3, "expected_grant_revision": 1,
                    "request_id": "operator-grant", "card_ids": ["text", "question"]}
        self.assertTrue(broker.activity_action("activity_publisher_grant", operator, "operator", "operator")["enabled"])

    def test_existing_confirmed_board_deletion_cleans_grants_and_cannot_restore_publication(self):
        self.grant()
        self.assertTrue(self.service.delete("board", 2, "delete-fixture", confirmed=True)["deleted"])
        with self.store.connect() as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM activity_publishers").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM activity_deleted WHERE id='board'").fetchone()[0])
        with self.assertRaises(PolicyError):
            self.service.publisher_get(self.runner, "board", self.owner, self.runner)


if __name__ == "__main__":
    unittest.main()
