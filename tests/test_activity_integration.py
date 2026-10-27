"""Offline Prism entry-point, MCP framing and SQLite recovery regressions."""
import contextlib
import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from visor import __main__, mcp
from visor.activity_cli import ACTIVITY_TOOLS
from visor.activity_document import ActivityPolicy
from visor.activity_store import ACTIVITY_TABLES, ActivityService
from visor.state_backup import connection, restore, snapshot
from visor.store import Store


class ActivityEntrypointCase(unittest.TestCase):
    def test_activity_remainder_dispatches_without_shell_startup_or_ui(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            argv = ["visor-console", "--data-dir", folder, "--credential", "scoped.json",
                    "--activity", "get", "board", "--session", "claude:fixture"]
            output = io.StringIO()
            with patch("sys.argv", argv), patch("visor.__main__.prepare_runtime_path") as prepare, \
                    patch("visor.__main__.data_dir", return_value=root), \
                    patch("visor.activity_cli.run_activity", return_value={"title": "Prism café"}) as run, \
                    contextlib.redirect_stdout(output):
                __main__.main()
            prepare.assert_not_called()
            run.assert_called_once_with(["--credential", "scoped.json", "get", "board", "--session", "claude:fixture"], root)
            self.assertEqual(json.loads(output.getvalue()), {"title": "Prism café"})

    def test_activity_errors_exit_without_launching_broker(self):
        with patch("sys.argv", ["visor-console", "--activity", "list"]), \
                patch("visor.__main__.data_dir", return_value=Path("/unused")), \
                patch("visor.__main__.prepare_runtime_path") as prepare, \
                patch("visor.activity_cli.run_activity", side_effect=ValueError("Missing session grant")), \
                self.assertRaisesRegex(SystemExit, "Prism: Missing session grant"):
            __main__.main()
        prepare.assert_not_called()


class RecordingStream(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.read_limits = []

    def readline(self, size=-1):
        if size < 0:
            raise AssertionError("Unbounded request read")
        self.read_limits.append(size)
        return super().readline(size)


class ActivityMCPCase(unittest.TestCase):
    def serve(self, frames, limit=65_536):
        stream = RecordingStream(frames)
        client, emitted = Mock(), []
        client.call.return_value = {"accepted": True}
        with patch("sys.stdin", SimpleNamespace(buffer=stream)), \
                patch("visor.mcp._activity_request_limit", return_value=limit):
            mcp.serve_requests(client, False, emitted.append, threading.Event())
        return client, emitted, stream

    def frame(self, name, body, identity=1):
        return (json.dumps({"jsonrpc": "2.0", "id": identity, "method": "tools/call",
                           "params": {"name": name, "arguments": body}}) + "\n").encode()

    def test_reviewed_prism_tools_extend_legacy_inventory_without_operator_tools(self):
        names = {tool["name"] for tool in mcp.handle({"method": "tools/list"}, None)["tools"]}
        self.assertEqual(names, {"peers", "send", "receive", "acknowledge", "operator_reply"} | {t["name"] for t in ACTIVITY_TOOLS})
        self.assertFalse({"activity_bind", "activity_policy", "stop", "delete_session"} & names)

    def test_configured_large_activity_asset_passes_exact_frame(self):
        payload = {"name": "picture.png", "data_base64": "A" * 90_000}
        client, emitted, stream = self.serve(self.frame("activity_asset", payload), 131_072)
        client.call.assert_called_once_with("activity_asset", **payload)
        self.assertIn("result", emitted[0])
        self.assertTrue(all(0 < size <= 131_073 for size in stream.read_limits))

    def test_large_legacy_request_rejected_even_with_prism_capacity(self):
        payload = {"recipient": "fixture", "body": "x" * 90_000, "request_id": "one"}
        client, emitted, _ = self.serve(self.frame("send", payload), 131_072)
        client.call.assert_not_called()
        self.assertIn("Request too large", emitted[0]["error"]["message"])

    def test_oversized_frame_is_drained_once_and_next_request_survives(self):
        oversized = self.frame("activity_asset", {"name": "x.png", "data_base64": "A" * 200_000})
        ping = b'{"jsonrpc":"2.0","id":2,"method":"ping"}\n'
        client, emitted, stream = self.serve(oversized + ping)
        client.call.assert_not_called()
        self.assertEqual(len(emitted), 2)
        self.assertIn("Request too large", emitted[0]["error"]["message"])
        self.assertEqual(emitted[1], {"jsonrpc": "2.0", "id": 2, "result": {}})
        self.assertTrue(all(0 < size <= 65_537 for size in stream.read_limits))

    def test_utf8_byte_limit_and_invalid_argument_shapes_are_rejected(self):
        oversized = self.frame("activity_get", {"id": "字" * 20_000})
        invalid = b'{"id":2,"method":"tools/call","params":[]}\n'
        ping = b'{"id":3,"method":"ping"}\n'
        client, emitted, _ = self.serve(oversized + invalid + ping)
        client.call.assert_not_called()
        self.assertIn("error", emitted[0])
        self.assertIn("error", emitted[1])
        self.assertEqual(emitted[2]["result"], {})

    def test_limits_use_validated_broker_policy_and_fall_back_for_old_broker(self):
        client = Mock()
        policy = ActivityPolicy(max_image_bytes=131_072)
        client.call.return_value = asdict(policy)
        with patch("visor.broker.activity_request_limit", return_value=250_000, create=True) as limit:
            self.assertEqual(mcp._activity_request_limit(client), 250_000)
            limit.assert_called_once_with(policy)
        client.call.assert_called_once_with("activity_limits")
        for value in (None, {"max_image_bytes": True}, {"unsupported": 1}):
            client.call.return_value = value
            with patch("visor.broker.activity_request_limit", return_value=250_000, create=True):
                self.assertEqual(mcp._activity_request_limit(client), 65_536)
        client.call.side_effect = OSError("Offline")
        with patch("visor.broker.activity_request_limit", return_value=250_000, create=True):
            self.assertEqual(mcp._activity_request_limit(client), 65_536)


class ActivityBackupCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.store = Store(self.root)
        self.actor = self.store.upsert_session("codex", "fixture", "Fixture", str(self.root))

    def test_restore_accepts_optional_prism_tables_preserves_content_and_clears_exact_grants(self):
        ActivityService(self.store)
        with self.store.connect() as db:
            db.execute("INSERT INTO activity_assets VALUES(?,?,?,?,?,?)",
                       (self.actor, "assets/fixture.png", "hash", 7, "fixture", b"content"))
        access = self.root / "activity-access"
        access.mkdir()
        grant = access / (hashlib.sha256(self.actor.encode()).hexdigest() + ".json")
        grant.write_text('{"token":"stale-fixture"}')
        unrelated = access / "operator-notes.txt"
        unrelated.write_text("Keep this")
        backup = snapshot(self.root)
        restore(self.root, backup)
        self.assertFalse(grant.exists())
        self.assertEqual(unrelated.read_text(), "Keep this")
        with self.store.connect() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue(ACTIVITY_TABLES <= tables)
            self.assertEqual(db.execute("SELECT content FROM activity_assets").fetchone()[0], b"content")

    def test_legacy_backup_without_activity_tables_remains_supported(self):
        backup = snapshot(self.root)
        restore(self.root, backup)
        self.assertEqual(Store(self.root).session(self.actor)["name"], "Fixture")

    def test_foreign_table_rejected_before_existing_state_or_grants_change(self):
        backup = snapshot(self.root)
        with connection(backup) as db:
            db.execute("CREATE TABLE foreign_state(value TEXT)")
        with self.assertRaisesRegex(ValueError, "supported VISOR"):
            restore(self.root, backup)
        self.assertEqual(self.store.session(self.actor)["name"], "Fixture")

    @unittest.skipIf(os.name == "nt", "Symlink creation requires separate Windows privilege")
    def test_symlink_access_directory_cannot_redirect_cleanup(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside)
            credential = target / ("a" * 64 + ".json")
            credential.write_text("unrelated")
            (self.root / "activity-access").symlink_to(target, target_is_directory=True)
            backup = snapshot(self.root)
            with self.assertRaisesRegex(ValueError, "ordinary directory"):
                restore(self.root, backup)
            self.assertEqual(credential.read_text(), "unrelated")
