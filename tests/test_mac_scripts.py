"""Compile generated AppleScript on macOS without executing window operations."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from visor.external_windows import _mac_call
from visor.platforms import Mac, terminal_launch


@unittest.skipUnless(sys.platform == "darwin", "Requires the native AppleScript compiler")
class MacScriptCase(unittest.TestCase):
    def test_generated_terminal_scripts_compile(self):
        result = subprocess.CompletedProcess([], 0, "42", "")
        scripts = []
        with patch("visor.platforms.run", return_value=result) as run:
            terminal_launch(["/tmp/a b/helper", "--terminal", "a'b"], "/tmp/a'b", "token")
            terminal_launch(["/tmp/helper"], "/tmp/project", "token", appearance={"mac_profile": 'Profile "quoted"'})
            for operation in ("find", "focus", "close", "geometry", "place"):
                Mac()._call("token", operation, (0, 0, 640, 400), {"handle": "42"})
            run.side_effect = [result, subprocess.CompletedProcess([], 0, "", "")]
            Mac().close("token", {"handle": "42"})
            scripts.extend(call.args[0][2] for call in run.call_args_list)
        with patch("visor.external_windows.run", return_value=result) as run:
            for operation in ("title", "focus"):
                _mac_call("42", operation)
            scripts.extend(call.args[0][2] for call in run.call_args_list)
        with tempfile.TemporaryDirectory() as directory:
            for index, script in enumerate(scripts):
                with self.subTest(index=index):
                    source = Path(directory) / f"script-{index}.applescript"
                    source.write_text(script)
                    compiled = subprocess.run(
                        ["/usr/bin/osacompile", "-o", str(source.with_suffix(".scpt")), str(source)],
                        capture_output=True, text=True, timeout=15, check=False)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
