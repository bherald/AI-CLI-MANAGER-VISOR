"""Windows npm Codex launch compatibility, without authenticating a provider."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from visor.diagnostics import doctor
from visor.providers import (
    CodexRPC,
    ProviderUnavailable,
    create_session,
    deliver_codex,
    ensure_codex_daemon,
    provider_command,
    session_command,
)


class WindowsNpmCodexCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="visor npm ")
        self.addCleanup(self.temporary.cleanup)
        self.prefix = Path(self.temporary.name) / "Program Files & Node"
        self.prefix.mkdir()
        self.shim = self.prefix / "codex.cmd"
        self.shim.write_text("@echo WRAPPER_SHOULD_NOT_RUN\n@exit /b 1\n")
        self.node = self.prefix / "node.exe"
        self.node.write_bytes(b"fixture")
        self.package = self.prefix / "node_modules/@openai/codex"
        self.script = self.package / "bin/codex.js"
        self.script.parent.mkdir(parents=True)
        self.script.write_text('process.stdout.write(JSON.stringify(process.argv.slice(2)));\n')
        self.manifest = self.package / "package.json"
        self.manifest.write_text(json.dumps({"name": "@openai/codex", "bin": {"codex": "bin/codex.js"}}))
        self.expected = [str(self.node.resolve()), str(self.script.resolve())]
        self.addCleanup(patch.stopall)
        patch("visor.providers.sys.platform", "win32").start()
        self.which_patch = patch("visor.providers.shutil.which", side_effect=self.lookup)
        self.which = self.which_patch.start()

    def lookup(self, name):
        return str(self.shim) if name == "codex" else None

    def test_npm_cmd_ps1_and_extensionless_entry_use_native_node(self):
        for name in ("codex.cmd", "codex.ps1", "codex", "codex.CMD"):
            with self.subTest(name=name):
                self.shim = self.prefix / name
                self.shim.touch()
                self.assertEqual(provider_command("codex"), self.expected)

    def test_native_codex_exe_does_not_require_node(self):
        self.shim = self.prefix / "codex.exe"
        self.node.unlink()
        self.assertEqual(provider_command("codex"), [str(self.shim)])

    def test_roaming_npm_prefix_uses_node_from_path(self):
        self.node.unlink()
        node = self.prefix.parent / "Other Node/node.exe"
        node.parent.mkdir()
        node.touch()
        self.which.side_effect = lambda name: str(self.shim) if name == "codex" else str(node) if name == "node.exe" else None
        self.assertEqual(provider_command("codex"), [str(node.resolve()), str(self.script.resolve())])

    def test_missing_node_or_incomplete_package_fails_without_running_shim(self):
        self.node.unlink()
        with self.assertRaisesRegex(ProviderUnavailable, "node.exe is unavailable"):
            provider_command("codex")
        self.node.touch()
        for data in ({"name": "other", "bin": {"codex": "bin/codex.js"}},
                     {"name": "@openai/codex", "bin": {"codex": "../other.js"}}):
            self.manifest.write_text(json.dumps(data))
            with self.assertRaisesRegex(ProviderUnavailable, "npm entry is missing"):
                provider_command("codex")
        self.manifest.write_text("invalid json")
        with self.assertRaises(ProviderUnavailable):
            provider_command("codex")

    def test_claude_shell_wrapper_is_not_enabled(self):
        self.which.side_effect = lambda _name: str(self.prefix / "claude.cmd")
        with self.assertRaises(ProviderUnavailable):
            provider_command("claude")

    def test_resume_daemon_queue_and_control_socket_keep_command_prefix(self):
        session = {"provider": "codex", "native_id": "exact thread", "cwd": "project & spaces"}
        command = session_command(session, ["visor-console", "--mcp"])
        self.assertEqual(command[:2], self.expected)
        self.assertEqual(command[-4:], ["-C", session["cwd"], "resume", "exact thread"])
        with patch("visor.providers.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run, \
                patch("visor.providers.CodexRPC"):
            ensure_codex_daemon()
            self.assertEqual(run.call_args.args[0], [*self.expected, "app-server", "daemon", "start"])
            deliver_codex(session, {"id": "fixture", "sender": "operator", "body": 'Text & "quotes"'})
            self.assertEqual(run.call_args.args[0][:3], [*self.expected, "queue"])
            self.assertIn('Text & "quotes"', run.call_args.args[0][-3])
        with patch("visor.providers.subprocess.Popen", side_effect=OSError("fixture")) as popen:
            with self.assertRaises(OSError):
                CodexRPC()
            self.assertEqual(popen.call_args.args[0], [*self.expected, "app-server", "proxy"])

    def test_create_session_accepts_npm_preflight(self):
        with patch("visor.providers.ensure_codex_daemon"), patch("visor.providers.CodexRPC") as rpc:
            rpc.return_value.__enter__.return_value.call.return_value = {"thread": {"id": "exact"}}
            self.assertEqual(create_session("codex", str(self.prefix)), "exact")

    def test_doctor_reports_effective_command_and_checks_version(self):
        with patch("visor.diagnostics.run", return_value=subprocess.CompletedProcess([], 0, "codex fixture", "")) as run:
            report = doctor()
        self.assertEqual(report["providers"]["codex"]["command"], self.expected)
        self.assertEqual(report["providers"]["codex"]["launcher"], str(self.shim))
        self.assertEqual(report["providers"]["codex"]["version"], "codex fixture")
        self.assertEqual(run.call_args.args[0], [*self.expected, "--version"])
        self.assertFalse(any("native executable" in text for text in report["warnings"]))

    @unittest.skipUnless(os.name == "nt", "Run npm JS through a real Windows node.exe")
    def test_native_windows_node_preserves_arguments_with_shell_metacharacters(self):
        # Resolve before the fixture's which mock so the real Node installation is used.
        self.which_patch.stop()
        node = shutil.which("node.exe")
        self.assertIsNotNone(node, "Windows npm acceptance requires Node.js on the CI host")
        shutil.copy2(node, self.node)
        self.which = self.which_patch.start()
        arguments = ["--fixture", 'spaces & echo "literal"', "a|b>c", "%PATH%", "Unicode café"]
        result = subprocess.run([*provider_command("codex"), *arguments], capture_output=True,
                                text=True, encoding="utf-8", check=True, timeout=15)
        self.assertEqual(json.loads(result.stdout), arguments)
