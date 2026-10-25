"""Activity CLI tests use a scoped fake client and isolated local assets only."""
import base64
import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtGui import QImage

from visor.activity_cli import ACTIVITY_TOOLS, run_activity
from visor.activity_document import ActivityPolicy


class ActivityCLICase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.credential = self.root / "session.json"
        self.config = {"root": str(self.root), "token": "fixture-agent-token", "session": "codex:fixture"}
        self.credential.write_text(json.dumps(self.config))
        self.environment = patch.dict(os.environ, {"VISOR_AGENT_CREDENTIAL": ""})
        self.environment.start()
        self.client_patch = patch("visor.activity_cli.Client")
        self.client_type = self.client_patch.start()
        self.client = Mock()
        self.limits = Mock(return_value=asdict(ActivityPolicy()))
        self.client_type.return_value.call.side_effect = self.broker_call
        self.client.call.return_value = {"status": "ok"}

    def broker_call(self, operation, **params):
        return self.limits(**params) if operation == "activity_limits" else self.client.call(operation, **params)

    def tearDown(self):
        self.client_patch.stop()
        self.environment.stop()
        self.folder.cleanup()

    def invoke(self, *args):
        return run_activity(["--credential", str(self.credential), *args], self.root)

    def document(self, images=False):
        document = {"schema_version": 1, "id": "board", "title": "A board", "revision": 3,
                    "cards": [{"id": "note", "kind": "text", "title": "Explanation",
                               "html": "<p>Literal $(touch /tmp/not-run); `no shell`\ntext</p>"}]}
        if images:
            picture = QImage(2, 2, QImage.Format.Format_RGB32)
            picture.fill(0xff336699)
            self.assertTrue(picture.save(str(self.root / "sample.png"), "PNG"))
            for index in range(2):
                document["cards"].append({"id": f"picture-{index}", "kind": "image",
                                          "title": "Picture", "path": "sample.png"})
        source = self.root / "board.json"
        source.write_text(json.dumps(document))
        return source, document

    def test_explicit_credential_constructs_only_scoped_client(self):
        result = self.invoke("list")
        self.client_type.assert_called_once_with(self.root.resolve(), token="fixture-agent-token")
        self.client.call.assert_called_once_with("activity_list")
        self.assertEqual(result, {"status": "ok"})
        self.assertNotIn("fixture-agent-token", json.dumps(result))

    def test_no_credential_never_falls_back_to_operator_broker_token(self):
        (self.root / "broker.json").write_text(json.dumps({"port": 1, "token": "operator-secret"}))
        with self.assertRaisesRegex(ValueError, "require --credential"):
            run_activity(["list"], self.root)
        self.client_type.assert_not_called()

    def test_codex_and_claude_session_access_uses_exact_hash_and_binding(self):
        access = self.root / "activity-access"
        access.mkdir()
        for session in ("codex:exact-id", "claude:exact-id"):
            digest = hashlib.sha256(session.encode()).hexdigest()
            (access / (digest + ".json")).write_text(json.dumps({**self.config, "session": session}))
            run_activity(["list", "--session", session], self.root)
        self.assertEqual(self.client_type.call_count, 2)
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.invoke("list", "--session", "claude:foreign")
        self.assertEqual(self.client_type.call_count, 2)

    def test_environment_credential_is_scoped_and_explicit_path_takes_precedence(self):
        with patch.dict(os.environ, {"VISOR_AGENT_CREDENTIAL": str(self.credential)}):
            run_activity(["get", "board"], self.root)
        self.client.call.assert_called_once_with("activity_get", id="board")
        with patch.dict(os.environ, {"VISOR_AGENT_CREDENTIAL": str(self.root / "missing.json")}):
            self.invoke("list")
        self.assertEqual(self.client_type.call_count, 2)

    def test_missing_or_invalid_token_and_session_never_construct_a_client(self):
        for update in ({"token": ""}, {"token": None}, {"root": ""}, {"session": "operator"}):
            with self.subTest(update=update):
                self.credential.write_text(json.dumps({**self.config, **update}))
                with self.assertRaisesRegex(ValueError, "missing or malformed") as caught:
                    self.invoke("list")
                self.assertNotIn("fixture-agent-token", str(caught.exception))
        self.client_type.assert_not_called()

    def test_publish_preserves_literal_document_and_request_identity(self):
        source, document = self.document()
        self.invoke("publish", "--document", str(source), "--request-id", "literal;$(request)",
                 "--expected-revision", "2")
        self.client.call.assert_called_once_with("activity_publish", document=document,
                                                request_id="literal;$(request)", expected_revision=2)
        self.assertEqual(json.loads(source.read_text()), document)

    def test_publish_defaults_expected_revision_and_uploads_shared_asset_once(self):
        source, document = self.document(images=True)
        def call(action, **params):
            return {"path": "assets/owned.png"} if action == "activity_asset" else {"accepted": True}
        self.client.call.side_effect = call
        result = self.invoke("publish", "--document", str(source), "--request-id", "publish-v3")
        self.assertEqual(result, {"accepted": True})
        calls = self.client.call.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args, ("activity_asset",))
        self.assertEqual(calls[0].kwargs["name"], "sample.png")
        self.assertEqual(base64.b64decode(calls[0].kwargs["data_base64"]), (self.root / "sample.png").read_bytes())
        self.assertEqual(calls[1].kwargs["expected_revision"], 2)
        self.assertEqual([c["path"] for c in calls[1].kwargs["document"]["cards"] if c["kind"] == "image"],
                         ["assets/owned.png", "assets/owned.png"])
        self.assertEqual(json.loads(source.read_text()), document)

    def test_all_assets_validate_before_any_upload(self):
        source, document = self.document(images=True)
        document["cards"][-1]["path"] = "missing.png"
        source.write_text(json.dumps(document))
        with self.assertRaises((OSError, ValueError)):
            self.invoke("publish", "--document", str(source), "--request-id", "publish-v3")
        self.client.call.assert_not_called()

    def test_invalid_owned_asset_response_never_publishes(self):
        source, _ = self.document(images=True)
        self.client.call.return_value = {"path": "../foreign.png"}
        with self.assertRaisesRegex(ValueError, "invalid owned image path"):
            self.invoke("publish", "--document", str(source), "--request-id", "publish-v3")
        self.assertEqual([c.args[0] for c in self.client.call.call_args_list], ["activity_asset"])

    def test_controls_replies_ack_and_standalone_asset_have_exact_arguments(self):
        for action in ("open", "focus", "clear", "close"):
            self.invoke(action, "board", "--request-id", "request-" + action)
            self.client.call.assert_called_with("activity_control", id="board", action=action,
                                                request_id="request-" + action)
        self.invoke("replies", "--board-id", "board")
        self.client.call.assert_called_with("activity_replies", board_id="board")
        self.invoke("replies")
        self.client.call.assert_called_with("activity_replies")
        self.invoke("ack", "reply-event")
        self.client.call.assert_called_with("activity_acknowledge", event_id="reply-event")
        self.document(images=True)
        self.invoke("asset", str(self.root / "sample.png"))
        self.assertEqual(self.client.call.call_args.args, ("activity_asset",))

    def test_publish_and_control_require_request_id_before_client_creation(self):
        for arguments in (("publish", "--document", "unused.json"), ("open", "board")):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.invoke(*arguments)
        self.client_type.assert_not_called()

    def test_mcp_schema_has_no_actor_or_operator_access(self):
        names = {tool["name"] for tool in ACTIVITY_TOOLS}
        self.assertEqual(names, {"activity_list", "activity_get", "activity_publish", "activity_control",
                                 "activity_asset", "activity_replies", "activity_acknowledge", "activity_share"})
        for tool in ACTIVITY_TOOLS:
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
            self.assertFalse({"actor", "owner", "token", "credential"} & tool["inputSchema"]["properties"].keys())
        publish = next(t for t in ACTIVITY_TOOLS if t["name"] == "activity_publish")
        self.assertEqual(set(publish["inputSchema"]["required"]), {"document", "request_id", "expected_revision"})

    def test_share_passes_exact_members_and_request_identity(self):
        self.invoke("share", "--id", "board", "--members", "codex:one", "claude:two",
                    "--members", "codex:three", "--request-id", "share-v1")
        self.client.call.assert_called_once_with("activity_share", id="board",
                                                members=["codex:one", "claude:two", "codex:three"],
                                                request_id="share-v1")

    def test_share_rejects_duplicate_or_unqualified_members_before_rpc(self):
        for members in (("codex:one", "codex:one"), ("operator",)):
            with self.subTest(members=members), self.assertRaises(ValueError):
                self.invoke("share", "--id", "board", "--members", *members, "--request-id", "share-v1")
        self.client.call.assert_not_called()

    def shared_document(self):
        source, document = self.document()
        card = {"id": "foreign-image", "kind": "image", "title": "Other session image",
                "path": "assets/" + "a" * 64 + ".png", "contributor": "claude:other"}
        document["cards"].append(card)
        source.write_text(json.dumps(document))
        current = json.loads(json.dumps(document))
        current["revision"] = 2
        return source, document, current

    def test_shared_image_reference_survives_without_local_file_or_reupload(self):
        source, document, current = self.shared_document()
        self.client.call.side_effect = [{"document": current}, {"accepted": True}]
        self.assertEqual(self.invoke("publish", "--document", str(source), "--request-id", "shared-v3"),
                         {"accepted": True})
        calls = self.client.call.call_args_list
        self.assertEqual([call.args[0] for call in calls], ["activity_get", "activity_publish"])
        self.assertEqual(calls[0].kwargs, {"id": "board"})
        self.assertEqual(calls[1].kwargs, {"document": document, "request_id": "shared-v3", "expected_revision": 2})
        self.assertEqual(json.loads(source.read_text()), document)

    def test_existing_canonical_cache_and_omitted_contributor_still_preserve_asset(self):
        source, document, current = self.shared_document()
        document["cards"][-1].pop("contributor")
        cached = self.root / document["cards"][-1]["path"]
        cached.parent.mkdir()
        cached.write_bytes(b"not an author-local replacement")
        source.write_text(json.dumps(document))
        self.client.call.side_effect = [{"document": current}, {"accepted": True}]
        self.invoke("publish", "--document", str(source), "--request-id", "cache-v3")
        self.assertEqual([call.args[0] for call in self.client.call.call_args_list],
                         ["activity_get", "activity_publish"])

    def test_new_or_mismatched_canonical_reference_requires_local_image(self):
        for field, value in (("id", "new-card"), ("path", "assets/" + "b" * 64 + ".png"),
                             ("contributor", "codex:forged")):
            with self.subTest(field=field):
                source, document, current = self.shared_document()
                document["cards"][-1][field] = value
                source.write_text(json.dumps(document))
                self.client.call.reset_mock()
                self.client.call.return_value = {"document": current}
                with self.assertRaises((OSError, ValueError)):
                    self.invoke("publish", "--document", str(source), "--request-id", "invalid-v3")
                self.client.call.assert_called_once_with("activity_get", id="board")

    def test_missing_noncanonical_paths_are_never_broker_references(self):
        for relative in ("assets/owned.png", "missing.png", "assets/" + "A" * 64 + ".png"):
            with self.subTest(path=relative):
                source, document, _ = self.shared_document()
                document["cards"][-1]["path"] = relative
                source.write_text(json.dumps(document))
                self.client.call.reset_mock()
                with self.assertRaises((OSError, ValueError)):
                    self.invoke("publish", "--document", str(source), "--request-id", "invalid-v3")
                self.client.call.assert_not_called()

    def test_shared_asset_retry_and_revision_conflict_reach_authoritative_broker(self):
        source, document, current = self.shared_document()
        current["revision"] = 3
        self.client.call.side_effect = [{"document": current}, {"accepted": True}]
        self.invoke("publish", "--document", str(source), "--request-id", "same-request")
        self.client.call.assert_called_with("activity_publish", document=document,
                                           request_id="same-request", expected_revision=2)

    def test_shared_reference_and_new_local_picture_upload_only_new_asset(self):
        source, document, current = self.shared_document()
        picture = QImage(2, 2, QImage.Format.Format_RGB32)
        picture.fill(0xff336699)
        self.assertTrue(picture.save(str(self.root / "new.png"), "PNG"))
        document["cards"].append({"id": "my-picture", "kind": "image", "title": "New", "path": "new.png"})
        source.write_text(json.dumps(document))
        self.client.call.side_effect = [{"document": current}, {"path": "assets/" + "b" * 64 + ".png"},
                                       {"accepted": True}]
        self.invoke("publish", "--document", str(source), "--request-id", "mixed-v3")
        calls = self.client.call.call_args_list
        self.assertEqual([call.args[0] for call in calls], ["activity_get", "activity_asset", "activity_publish"])
        self.assertEqual(calls[1].kwargs["name"], "new.png")
        outgoing = calls[-1].kwargs["document"]["cards"]
        self.assertEqual(outgoing[-2], current["cards"][-1])
        self.assertEqual(outgoing[-1]["path"], "assets/" + "b" * 64 + ".png")

    def test_malformed_current_board_never_publishes_or_uploads(self):
        source, _, _ = self.shared_document()
        for current in (None, {}, {"document": []}, {"document": {"id": "other", "cards": []}}):
            with self.subTest(current=current):
                self.client.call.return_value = current
                self.client.call.reset_mock()
                with self.assertRaisesRegex(ValueError, "invalid current Prism document"):
                    self.invoke("publish", "--document", str(source), "--request-id", "invalid-v3")
                self.client.call.assert_called_once_with("activity_get", id="board")

    def test_broker_policy_permits_text_larger_than_local_defaults(self):
        source, document = self.document()
        document["cards"][0]["html"] = "x" * (ActivityPolicy().max_text_chars + 1)
        source.write_text(json.dumps(document))
        self.limits.return_value = asdict(ActivityPolicy(max_text_chars=100_000))
        self.invoke("publish", "--document", str(source), "--request-id", "large-text")
        self.limits.assert_called_once_with()
        self.client.call.assert_called_once_with("activity_publish", document=document,
                                                request_id="large-text", expected_revision=2)

    def test_standalone_asset_honors_broker_image_limit_before_upload(self):
        self.document(images=True)
        self.limits.return_value = asdict(ActivityPolicy(max_image_bytes=1))
        with self.assertRaises(ValueError):
            self.invoke("asset", str(self.root / "sample.png"))
        self.limits.assert_called_once_with()
        self.client.call.assert_not_called()

    def test_explicit_validation_policy_overrides_client_policy_lookup(self):
        source, document = self.document()
        document["cards"][0]["html"] = "x" * (ActivityPolicy().max_text_chars + 1)
        source.write_text(json.dumps(document))
        policy = self.root / "policy.json"
        policy.write_text(json.dumps({"max_text_chars": 100_000}))
        self.invoke("publish", "--document", str(source), "--request-id", "explicit-policy", "--policy", str(policy))
        self.limits.assert_not_called()
        self.assertEqual(self.client.call.call_args.args, ("activity_publish",))

    def test_policy_is_not_queried_for_non_validation_commands(self):
        self.limits.side_effect = AssertionError("Unexpected limits lookup")
        for args in (("list",), ("get", "board"), ("replies",), ("ack", "event"),
                     ("close", "board", "--request-id", "close"),
                     ("share", "--id", "board", "--members", "claude:other", "--request-id", "share")):
            with self.subTest(command=args[0]):
                self.invoke(*args, "--policy", str(self.root / "unused-missing-policy.json"))
        self.limits.assert_not_called()

    def test_missing_invalid_or_unsupported_broker_policy_never_uses_defaults(self):
        source, _ = self.document()
        for value in (None, {}, {"max_cards": 5}, {**asdict(ActivityPolicy()), "unknown": 1},
                      {**asdict(ActivityPolicy()), "max_image_bytes": True}):
            with self.subTest(policy=value):
                self.limits.return_value = value
                with self.assertRaises(ValueError):
                    self.invoke("publish", "--document", str(source), "--request-id", "invalid-policy")
                self.client.call.assert_not_called()
        for error in (ValueError("Unknown action"), OSError("Broker unavailable")):
            self.limits.side_effect = error
            with self.assertRaisesRegex(ValueError, "supports activity_limits"):
                self.invoke("publish", "--document", str(source), "--request-id", "unsupported-policy")
            self.client.call.assert_not_called()
