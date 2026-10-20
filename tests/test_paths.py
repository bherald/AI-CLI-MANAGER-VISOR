import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from visor.paths import prepare_runtime_path
from visor.providers import executable, session_command


class RuntimePathCase(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX desktop PATH regression")
    def test_desktop_resume_uses_shell_provider_and_node_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("nvm", "system"):
                (root / name).mkdir()
                (root / name / "codex").write_text("#!/bin/sh\nexit 0\n")
                (root / name / "codex").chmod(0o700)
            old = str(root / "system")
            preferred = str(root / "nvm")
            shell_path = preferred + os.pathsep + old
            result = subprocess.CompletedProcess([], 0,
                b"startup message\n\x00VISOR_PATH\x00" + os.fsencode(shell_path) + b"\x00goodbye")
            with patch.dict(os.environ, {"PATH": old, "SHELL": "/bin/bash"}, clear=True), \
                    patch("visor.paths.sys.platform", "linux"), \
                    patch("visor.paths.subprocess.run", return_value=result) as run:
                self.assertEqual(executable("codex"), str(root / "system/codex"))
                prepare_runtime_path()
                self.assertEqual(os.environ["PATH"], shell_path)
                self.assertEqual(run.call_args.args[0][:2], ["/bin/bash", "-ilc"])
                self.assertEqual(executable("codex"), str(root / "nvm/codex"))
                command = session_command(
                    {"provider": "codex", "native_id": "exact", "cwd": directory}, ["mcp-helper"])
                self.assertEqual(command[0], str(root / "nvm/codex"))
                self.assertEqual(command[-2:], ["resume", "exact"])
                prepare_runtime_path()
                self.assertEqual(run.call_count, 1)

    def test_unavailable_shell_preserves_inherited_path(self):
        outcomes = [OSError("missing shell"), subprocess.TimeoutExpired("shell", 5),
                    subprocess.CompletedProcess([], 1, b""),
                    subprocess.CompletedProcess([], 0, b"unrelated output"),
                    subprocess.CompletedProcess([], 0, b"\x00VISOR_PATH\x00\x00")]
        for outcome in outcomes:
            with self.subTest(outcome=outcome), \
                    patch.dict(os.environ, {"PATH": "/original"}, clear=True), \
                    patch("visor.paths.sys.platform", "linux"), \
                    patch("visor.paths.subprocess.run", side_effect=outcome if isinstance(outcome, Exception)
                          else None, return_value=outcome):
                prepare_runtime_path()
                self.assertEqual(os.environ["PATH"], "/original")

    def test_windows_and_explicit_opt_out_do_not_start_shell(self):
        for platform, opt_out in [("win32", "1"), ("linux", "0")]:
            with patch.dict(os.environ, {"VISOR_SHELL_PATH": opt_out}, clear=True), \
                    patch("visor.paths.sys.platform", platform), \
                    patch("visor.paths.subprocess.run") as run:
                prepare_runtime_path()
                run.assert_not_called()
