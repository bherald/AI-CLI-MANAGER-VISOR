import os
import subprocess
import unittest
from unittest.mock import patch

from visor.appearance import validate
from visor.platforms import Linux, WindowUnavailable, linux_terminal, terminal_launch


class KonsoleCase(unittest.TestCase):
    def test_selection_and_legacy_preferences(self):
        self.assertEqual(validate({"linux_font": "monospace"})["linux_terminal"], "auto")
        for invalid in ("shell", None, ["konsole"]):
            with self.assertRaises(ValueError):
                validate({"linux_terminal": invalid})
        with patch("visor.platforms.shutil.which", side_effect=lambda name: {"konsole": "/usr/bin/konsole", "xterm": "/usr/bin/xterm"}.get(name)):
            self.assertEqual(linux_terminal(), ("konsole", "/usr/bin/konsole"))
            self.assertEqual(linux_terminal("xterm"), ("xterm", "/usr/bin/xterm"))
        with patch("visor.platforms.shutil.which", side_effect=lambda name: "/usr/bin/xterm" if name == "xterm" else None):
            self.assertEqual(linux_terminal(), ("xterm", "/usr/bin/xterm"))
            with self.assertRaises(WindowUnavailable):
                linux_terminal("konsole")
        with patch("visor.platforms.shutil.which", return_value=None), self.assertRaises(WindowUnavailable):
            linux_terminal()

    def test_separate_window_inherits_profile_and_preserves_literal_command(self):
        command = ["/tmp/worker space", "literal's; $(not-a-shell)"]
        with patch("visor.platforms.sys.platform", "linux"), patch("visor.platforms.shutil.which", return_value="/usr/bin/konsole"), patch("visor.platforms.subprocess.Popen") as launch:
            terminal_launch(command, "/tmp/project's space", "VISOR-unique", {"linux_font": "Old xterm font", "linux_size": 16})
        args = launch.call_args.args[0]
        self.assertIn("--separate", args)
        self.assertNotIn("--new-tab", args)
        self.assertEqual(args[args.index("--workdir") + 1], "/tmp/project's space")
        self.assertEqual(args[args.index("-e") + 1:], command)
        self.assertIn("LocalTabTitleFormat=VISOR-unique", args)
        self.assertIn("RemoteTabTitleFormat=VISOR-unique", args)
        self.assertFalse(any("Font=" in arg or "ColorScheme=" in arg for arg in args))

    def test_packaged_konsole_uses_system_qt_without_losing_provider_environment(self):
        env = {"LD_LIBRARY_PATH": "/bundle/lib", "LD_LIBRARY_PATH_ORIG": "/system/lib", "QT_PLUGIN_PATH": "/bundle/qt", "QT_QPA_PLATFORM_PLUGIN_PATH": "/bundle/plugins", "PATH": "/providers:/usr/bin", "DISPLAY": ":10"}
        with patch.dict(os.environ, env, clear=True), patch("visor.platforms.sys.frozen", True, create=True), patch("visor.platforms.sys.platform", "linux"), patch("visor.platforms.shutil.which", return_value="konsole"), patch("visor.platforms.subprocess.Popen") as launch:
            terminal_launch(["helper"], "/project", "token")
        actual = launch.call_args.kwargs["env"]
        self.assertEqual(actual["LD_LIBRARY_PATH"], "/system/lib")
        self.assertEqual(actual["PATH"], env["PATH"])
        self.assertEqual(actual["DISPLAY"], env["DISPLAY"])
        self.assertNotIn("QT_PLUGIN_PATH", actual)
        self.assertNotIn("QT_QPA_PLATFORM_PLUGIN_PATH", actual)

    def test_konsole_suffix_match_is_exact_and_ambiguity_stays_refused(self):
        adapter = Linux()
        def result(text):
            return subprocess.CompletedProcess([], 0, text, "")
        with patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": ""}), patch("visor.platforms.shutil.which", return_value="wmctrl"), patch("visor.platforms.run") as run:
            run.return_value = result("0x01 0 0 0 800 600 host token — Konsole\n0x02 0 0 0 800 600 host token extra — Konsole\n")
            self.assertEqual(adapter.find("token")[0], "0x01")
            run.return_value = result("0x01 0 0 0 800 600 host token — Konsole\n0x02 0 0 0 800 600 host token\n")
            with self.assertRaises(WindowUnavailable):
                adapter.find("token")
