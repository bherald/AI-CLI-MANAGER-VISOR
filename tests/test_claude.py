import io
import json
import shutil
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from visor import claude
from visor.broker import Broker
from visor.mcp import channel_loop, handle, serve_requests
from visor.providers import session_command
from visor.store import PolicyError


class ClaudeCase(unittest.TestCase):
    def setUp(self):
        self.parent = Path(__file__).resolve().parents[1] / ".cache" / "tests"
        self.root = self.parent / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.sid = str(uuid.uuid4())
        self.broker = Broker(self.root)
        self.key = self.broker.store.upsert_session("claude", self.sid, "Claude fixture", str(self.root))

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(self.parent.resolve()))
        shutil.rmtree(self.root)

    def test_roster_uses_full_session_uuid_and_preserves_unknown_state(self):
        rows = [{"id": "short-job", "sessionId": self.sid, "cwd": str(self.root), "state": "blocked",
                 "status": "waiting", "waitingFor": "permission prompt"},
                {"id": "not-a-uuid", "cwd": str(self.root)},
                {"sessionId": str(uuid.uuid4()), "cwd": str(self.root), "status": "new-state"}]
        sessions = claude.parse_roster(rows)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["native_id"], self.sid)
        self.assertEqual(sessions[0]["status"], "waiting")
        self.assertEqual(sessions[1]["status"], "unknown")

    def test_saved_history_ignores_partial_records_and_subagents(self):
        project = self.root / "projects" / "project"
        project.mkdir(parents=True)
        path = project / (self.sid + ".jsonl")
        path.write_text(json.dumps({"cwd": str(self.root), "type": "user"}) + "\n" +
                        json.dumps({"type": "custom-title", "customTitle": "Saved fixture"}) + "\n{", encoding="utf-8")
        (project / "subagents").mkdir()
        (project / "subagents" / (str(uuid.uuid4()) + ".jsonl")).write_text(path.read_text())
        sessions, _ = claude.discover(config_dir=self.root)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["name"], "Saved fixture")
        self.assertEqual(sessions[0]["status"], "unknown")
        self.assertEqual(sessions[0]["cwd"], str(self.root))

    def test_old_cli_never_probes_unknown_agents_command(self):
        with patch("visor.claude.run", return_value=SimpleNamespace(stdout="2.1.138 (Claude Code)", returncode=0)) as run:
            claude.discover(exe="fixture-old-claude", config_dir=self.root)
        self.assertEqual(run.call_args.args[0], ["fixture-old-claude", "--version"])
        self.assertEqual(run.call_count, 1)

    def test_current_cli_uses_documented_roster(self):
        rows = [{"sessionId": self.sid, "cwd": str(self.root), "state": "working", "status": "busy"}]
        outputs = [SimpleNamespace(stdout="2.1.290", returncode=0), SimpleNamespace(stdout=json.dumps(rows), returncode=0)]
        with patch("visor.claude.run", side_effect=outputs) as run:
            sessions, warning = claude.discover(exe="fixture-new-claude", config_dir=self.root)
        self.assertEqual(run.call_args.args[0], ["fixture-new-claude", "agents", "--json", "--all"])
        self.assertFalse(warning)
        self.assertEqual(sessions[0]["status"], "working")

    def test_scoped_hooks_report_waiting_idle_and_background_work(self):
        for event, expected in (("SessionStart", "idle"), ("UserPromptSubmit", "working"),
                                ("PermissionRequest", "waiting"), ("Stop", "idle"), ("SessionEnd", "disconnected")):
            self.broker.dispatch("claude_event", {"event": {"session_id": self.sid, "hook_event_name": event}}, self.key, "helper")
            self.assertEqual(self.broker.store.session(self.key)["status"], expected)
        state = claude.hook_event({"session_id": self.sid, "hook_event_name": "Stop", "background_tasks": [{}]}, self.key)
        self.assertEqual(state["status"], "working")
        self.assertEqual(state["background_count"], 1)

    def test_hooks_reject_another_session_and_agent_cannot_spoof_them(self):
        with self.assertRaises(ValueError):
            claude.hook_event({"session_id": str(uuid.uuid4()), "hook_event_name": "Stop"}, self.key)
        with self.assertRaises(PolicyError):
            self.broker.dispatch("claude_event", {"event": {}}, self.key)

    def test_hook_settings_are_local_and_observe_only(self):
        with patch("visor.claude.own_command", return_value=["C:/Program Files/VISOR/visor-console.exe"]):
            path = claude.launch_settings(self.root, self.key, "fixture-helper-token")
        settings = json.loads(path.read_text())
        self.assertEqual(set(settings["hooks"]), set(claude.HOOK_EVENTS))
        hook = settings["hooks"]["PermissionRequest"][0]["hooks"][0]
        self.assertIn("--claude-hook", hook["command"])
        self.assertNotIn("fixture-helper-token", hook["command"])
        self.assertEqual(hook["type"], "command")
        self.assertNotIn("permissions", settings)

    @patch("visor.providers.executable", return_value="claude")
    def test_channels_require_operator_choice_and_never_skip_tool_permissions(self, _):
        session = self.broker.store.session(self.key)
        base = session_command(session, ["visor-console", "--mcp"])
        self.assertNotIn("--dangerously-load-development-channels", base)
        preview = session_command(session, ["visor-console", "--mcp"], claude_options={"mode": "development"})
        self.assertEqual(preview[-2:], ["--dangerously-load-development-channels", "server:visor"])
        approved = session_command(session, ["visor-console"], claude_options={"mode": "plugin", "plugin": "plugin:visor@company"})
        self.assertEqual(approved[-2:], ["--channels", "plugin:visor@company"])
        for cmd in (base, preview, approved):
            self.assertNotIn("--dangerously-skip-permissions", cmd)
            self.assertNotIn("-p", cmd)

    def test_invalid_plugin_and_agent_options_are_rejected(self):
        with self.assertRaises(ValueError):
            claude.validate_options({"mode": "plugin", "plugin": "server:anything --bad"})
        with self.assertRaises(PolicyError):
            self.broker.dispatch("claude_options", {"value": {"mode": "development"}}, self.key)

    def test_channel_capability_has_no_permission_relay(self):
        result = handle({"method": "initialize"}, None, channel=True)
        self.assertEqual(result["capabilities"]["experimental"], {"claude/channel": {}})
        self.assertNotIn("experimental", handle({"method": "initialize"}, None)["capabilities"])
        self.assertNotEqual(result["protocolVersion"], "2026-07-28")

    def test_push_is_delivery_unknown_until_explicit_completion(self):
        self.broker.store.update_session(self.key, managed=True, status="idle")
        message = self.broker.dispatch("wrap_up", {"sessions": [self.key], "request_id": "wrap"}, "operator")[0]
        broker = self.broker
        class Client:
            def call(self, action, **params):
                return broker.dispatch(action, params, self_key)
        self_key = self.key
        stopped, notifications = threading.Event(), []
        def emit(value):
            notifications.append(value)
            stopped.set()
        channel_loop(Client(), emit, stopped)
        current = self.broker.store.message(message["id"])
        self.assertEqual(current["state"], "delivery_unknown")
        self.assertEqual(notifications[0]["method"], "notifications/claude/channel")
        self.assertEqual(notifications[0]["params"]["meta"]["message_id"], message["id"])
        self.assertIsNone(self.broker.dispatch("receive", {}, self.key))
        self.broker.dispatch("acknowledge", {"message": message["id"], "completed": True}, self.key)
        self.assertTrue(self.broker.store.session(self.key)["shutdown"].startswith("confirmed"))

    def test_channel_cannot_confirm_another_recipient(self):
        other = self.broker.store.upsert_session("claude", str(uuid.uuid4()), "Other", str(self.root))
        message = self.broker.dispatch("send", {"recipient": other, "body": "Other", "request_id": "other"}, "operator")
        with self.assertRaises(PolicyError):
            self.broker.dispatch("channel_written", {"message": message["id"]}, self.key)

    def test_channel_starts_only_after_client_initialized_notification(self):
        requests = [{"id": 1, "method": "initialize"}, {"method": "notifications/initialized"},
                    {"method": "notifications/initialized"}]
        with patch("sys.stdin", io.StringIO("\n".join(json.dumps(r) for r in requests))), patch("visor.mcp.threading.Thread") as thread:
            emitted = []
            serve_requests(None, True, emitted.append, threading.Event())
        self.assertEqual(thread.call_count, 1)
        self.assertEqual(emitted[0]["id"], 1)

    def test_claude_discovery_succeeds_when_codex_is_unavailable(self):
        item = {"provider": "claude", "native_id": self.sid, "name": "Discovered", "cwd": str(self.root), "status": "idle"}
        with patch("visor.broker.discover_codex", side_effect=RuntimeError("not installed")), patch("visor.broker.discover_claude", return_value=([item], "")):
            self.assertEqual(self.broker.dispatch("refresh", {}, "operator"), 1)
        self.assertEqual(self.broker.store.session(self.key)["status"], "idle")

    def test_saved_discovery_does_not_erase_hook_status(self):
        self.broker.store.update_session(self.key, managed=True, heartbeat=time.time(), status="waiting")
        self.broker.store.set_setting("claude_runtime:" + self.key, {"observed": time.time()})
        item = {"provider": "claude", "native_id": self.sid, "name": "Saved", "cwd": str(self.root), "status": "unknown"}
        with patch("visor.broker.discover_codex", return_value=[]), patch("visor.broker.discover_claude", return_value=([item], "")):
            self.broker.dispatch("refresh", {}, "operator")
        self.assertEqual(self.broker.store.session(self.key)["status"], "waiting")
