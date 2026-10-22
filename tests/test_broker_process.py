"""Exercise a real loopback broker, isolated from all provider sessions."""
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import unittest
import uuid
from pathlib import Path

from broker_support import wait_for_broker

from visor.broker import Client
from visor.paths import write_private
from visor.process import hidden_options
from visor.store import PolicyError, Store


class BrokerProcessCase(unittest.TestCase):
    def program(self):
        packaged = os.environ.get("VISOR_TEST_EXECUTABLE")
        return [packaged] if packaged else [sys.executable, "-m", "visor"]

    def setUp(self):
        self.parent = Path(__file__).resolve().parents[1] / ".cache" / "tests"
        self.parent.mkdir(parents=True, exist_ok=True)
        self.root = self.parent / uuid.uuid4().hex
        self.process = None
        self.start()

    def start(self):
        self.process = subprocess.Popen([*self.program(), "--broker", "--data-dir", str(self.root)],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        env={**os.environ, "VISOR_SHELL_PATH": "0", "CLAUDE_CONFIG_DIR": str(self.root / "claude-config")},
                                        **hidden_options())
        self.client = wait_for_broker(self.process, self.root)

    def tearDown(self):
        if self.process and self.process.poll() is None:
            try:
                self.client.call("stop")
                self.process.wait(timeout=5)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.process.terminate()
                self.process.wait(timeout=5)
        if self.process:
            self.process.communicate()
        self.assertTrue(self.root.resolve().is_relative_to(self.parent.resolve()))
        shutil.rmtree(self.root)

    def test_restart_preserves_policy_and_operator_message(self):
        key = self.client.call("register", provider="claude", native_id=str(uuid.uuid4()), name="Test", cwd=str(self.root))
        self.client.call("toggle", enabled=False)
        message = self.client.call("send", recipient=key, body="Isolated test", request_id="test-once")
        self.client.call("stop")
        self.process.wait(timeout=5)
        self.process.communicate()
        self.start()
        snapshot = self.client.call("snapshot")
        self.assertFalse(snapshot["agent_enabled"])
        self.assertEqual(snapshot["messages"][0]["id"], message["id"])
        self.assertEqual(snapshot["messages"][0]["state"], "queued")
        token = Store(self.root).issue_credential(key)
        agent = Client(self.root, token)
        with self.assertRaises(PolicyError):
            agent.call("stop")
        received = agent.call("receive")
        self.assertEqual(received["id"], message["id"])
        agent.call("acknowledge", message=message["id"], completed=True)
        self.assertEqual(self.client.call("snapshot")["messages"][0]["state"], "completed")
        self.assertIsNone(agent.call("receive"))

    def test_preferences_draft_and_backup_survive_restart_and_are_operator_only(self):
        key = self.client.call("register", provider="claude", native_id=str(uuid.uuid4()), name="Fixture", cwd=str(self.root))
        draft = {"text": "Private unfinished draft", "recipient": key}
        appearance = {"linux_font": "monospace", "linux_size": 16, "windows_profile": "Visor", "mac_profile": "Visor"}
        self.assertEqual(self.client.call("message_draft", value=draft), draft)
        self.assertEqual(self.client.call("terminal_appearance", value=appearance), appearance)
        self.client.call("backup_options", value={"before_upgrade": True, "keep": 2})
        path = Path(self.client.call("backup_state"))
        self.assertTrue(path.is_file())
        agent = Client(self.root, Store(self.root).issue_credential(key))
        for action in ("backup_state", "message_draft", "terminal_appearance", "backup_options"):
            with self.subTest(action=action), self.assertRaises(PolicyError):
                agent.call(action)
        self.client.call("stop")
        self.process.wait(timeout=5)
        self.process.communicate()
        self.start()
        self.assertEqual(self.client.call("message_draft"), draft)
        self.assertEqual(self.client.call("terminal_appearance"), appearance)
        self.assertTrue(self.client.call("backup_options")["before_upgrade"])

    def test_delete_claude_conversation_and_visor_data(self):
        sid = str(uuid.uuid4())
        project = self.root / "claude-config" / "projects" / "project"
        project.mkdir(parents=True)
        transcript = project / (sid + ".jsonl")
        transcript.write_text('{}\n')
        adjacent = project / (str(uuid.uuid4()) + ".jsonl")
        adjacent.write_text('{}\n')
        key = self.client.call("register", provider="claude", native_id=sid, name="Delete test", cwd=str(self.root))
        self.client.call("send", recipient=key, body="Pending message", request_id="delete-test")
        token = Store(self.root).issue_credential(key)
        self.client.call("delete_session", session=key, confirmed=True)
        self.assertFalse(transcript.exists())
        self.assertTrue(adjacent.exists())
        self.assertEqual(self.client.call("snapshot")["messages"], [])
        self.assertNotIn(key, [s["id"] for s in self.client.call("snapshot")["sessions"]])
        self.assertIsNone(Store(self.root).authenticate(token))
        self.client.call("stop")
        self.process.wait(timeout=5)
        self.process.communicate()
        self.start()
        with self.assertRaises(PolicyError):
            self.client.call("register", provider="claude", native_id=sid, name="Stale", cwd=str(self.root))

    def test_operator_can_delete_uncertain_message_but_agent_cannot(self):
        key = self.client.call("register", provider="claude", native_id=str(uuid.uuid4()),
                               name="Message deletion", cwd=str(self.root))
        message = self.client.call("send", recipient=key, body="Delete me", request_id="delete-message")
        store = Store(self.root)
        with store.connect(True) as db:
            db.execute("UPDATE messages SET state='delivery_unknown' WHERE id=?", (message["id"],))
        for role in ("agent", "helper"):
            agent = Client(self.root, store.issue_credential(key, role))
            with self.assertRaises(PolicyError):
                agent.call("delete_messages", messages=[message["id"]])
        self.assertEqual(self.client.call("delete_messages", messages=[message["id"]]), 1)
        self.assertEqual(self.client.call("snapshot")["messages"], [])
        self.client.call("stop")
        self.process.wait(timeout=5)
        self.process.communicate()
        self.start()
        self.assertEqual(self.client.call("snapshot")["messages"], [])
        with self.assertRaises(PolicyError):
            self.client.call("send", recipient=key, body="Delete me", request_id="delete-message")

    def test_invalid_token_rejected(self):
        with self.assertRaises(PolicyError):
            Client(self.root, "invalid").call("snapshot")

    def test_external_focus_link_cannot_grant_agent_controls_or_delete_history(self):
        store = Store(self.root)
        sid = str(uuid.uuid4())
        key = self.client.call("register", provider="claude", native_id=sid,
                               name="External link fixture", cwd=str(self.root))
        for role in ("agent", "helper"):
            token = store.issue_credential(key, role)
            with self.assertRaises(PolicyError):
                Client(self.root, token).call("external_window_candidates")
        with self.assertRaises(PolicyError):
            self.client.call("external_focus_link", session=key, identity={})
        project = self.root / "claude-config/projects/project"
        project.mkdir(parents=True)
        transcript = project / (sid + ".jsonl")
        transcript.write_text('{}\n')
        # Synthetic identity tests the control boundary without touching any real window.
        store.set_external_focus(key, {"title": "Fixture", "identity": {"platform": sys.platform,
            "pid": 42, "created": 1, "handle": "fixture", "image": "fixture"}})
        self.assertIn(key, self.client.call("snapshot")["external_focus"])
        with self.assertRaisesRegex(PolicyError, "does not establish worker ownership"):
            self.client.call("delete_session", session=key, confirmed=True)
        self.assertTrue(transcript.exists())
        self.client.call("external_focus_unlink", session=key, confirmed=True)
        self.assertEqual(self.client.call("snapshot")["external_focus"], {})

    def test_claude_channel_cross_provider_acknowledgment_and_broker_restart(self):
        store = Store(self.root)
        sid = str(uuid.uuid4())
        key = self.client.call("register", provider="claude", native_id=sid, name="Claude fixture", cwd=str(self.root))
        peer = self.client.call("register", provider="codex", native_id=str(uuid.uuid4()), name="Codex fixture", cwd=str(self.root))
        # This simulated Codex remains unknown, so the delivery worker never calls its CLI.
        peer_client = Client(self.root, store.issue_credential(peer))
        credential = self.root / "mcp.json"
        write_private(credential, {"root": str(self.root), "token": store.issue_credential(key), "session": key, "channel": True})
        hook_credential = self.root / "hook.json"
        write_private(hook_credential, {"root": str(self.root), "token": store.issue_credential(key, "helper"), "session": key})
        store.update_session(key, managed=True)
        hook = subprocess.run([*self.program(), "--claude-hook", "--credential", str(hook_credential)],
            check=False, input=json.dumps({"session_id": sid, "hook_event_name": "SessionStart"}), text=True,
            capture_output=True, timeout=5, **hidden_options())
        self.assertEqual(hook.returncode, 0)
        self.assertEqual(hook.stdout, "")
        self.assertEqual(store.session(key)["status"], "idle")
        mcp = subprocess.Popen([*self.program(), "--mcp", "--credential", str(credential)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", **hidden_options())
        output = queue.Queue()
        def read():
            for line in mcp.stdout:
                output.put(json.loads(line))
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        def send(value):
            mcp.stdin.write(json.dumps(value) + "\n")
            mcp.stdin.flush()
        try:
            send({"id": 1, "method": "initialize"})
            self.assertIn("claude/channel", output.get(timeout=5)["result"]["capabilities"]["experimental"])
            send({"method": "notifications/initialized"})
            self.client.call("pair", a=key, b=peer)
            for session in (key, peer):
                self.client.call("participate", session=session, enabled=True)
            self.client.call("toggle", enabled=True)
            message = peer_client.call("send", recipient=key, body="Peer message", request_id="cross-provider")
            event = output.get(timeout=5)
            self.assertEqual(event["params"]["meta"]["message_id"], message["id"])
            send({"id": 2, "method": "tools/call", "params": {"name": "acknowledge", "arguments": {"message": message["id"], "completed": True}}})
            self.assertFalse(output.get(timeout=5)["result"].get("isError", False))
            send({"id": 3, "method": "tools/call", "params": {"name": "send", "arguments": {"recipient": peer, "body": "Reply", "request_id": "reply"}}})
            self.assertFalse(output.get(timeout=5)["result"].get("isError", False))
            self.assertEqual(peer_client.call("receive")["body"], "Reply")
            self.client.call("toggle", enabled=False)
            held = peer_client.call("send", recipient=key, body="Held", request_id="held")
            self.assertEqual(held["state"], "held")
            self.client.call("stop")
            self.process.wait(5)
            self.process.communicate()
            self.start()
            # MCP remains open and reconnects using the replaced broker endpoint.
            store.update_session(key, managed=True, status="idle")
            wrap = self.client.call("wrap_up", sessions=[key], request_id="wrap")[0]
            event = output.get(timeout=8)
            self.assertEqual(event["params"]["meta"]["message_id"], wrap["id"])
            send({"id": 4, "method": "tools/call", "params": {"name": "acknowledge", "arguments": {"message": wrap["id"], "completed": True}}})
            self.assertFalse(output.get(timeout=5)["result"].get("isError", False))
            self.assertTrue(store.session(key)["shutdown"].startswith("confirmed"))
            self.assertEqual(store.message(held["id"])["state"], "held")
        finally:
            mcp.stdin.close()
            mcp.wait(5)
            reader.join(2)
            mcp.stdout.close()
            mcp.stderr.close()

