import errno
import os
import subprocess
import unittest
from unittest.mock import patch

import psutil

from visor.layout import Rect
from visor.platforms import (
    Linux,
    Mac,
    WindowNotFound,
    WindowUnavailable,
    focus_verified,
    terminal_launch,
)
from visor.process_identity import process_identity, retired


class PlatformsCase(unittest.TestCase):
    def result(self, stdout="", returncode=0, stderr=""):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    @patch("visor.platforms.run")
    @patch("visor.platforms.shutil.which", return_value="wmctrl")
    def test_x11_exact_identity_missing_ambiguous_and_command_failure(self, _, run):
        adapter = Linux()
        with patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": ""}):
            run.return_value = self.result("0x01 0 10 20 800 600 host unique\n")
            with self.assertRaises(WindowUnavailable):
                adapter.close("unique", {"handle": "0x02"})
            self.assertEqual(run.call_count, 1)  # Never sent close to a replacement.
            with self.assertRaises(WindowUnavailable):
                adapter.place("unique", Rect(0, 0, 100, 100), expected_identity={"handle": "0x02"})
            self.assertEqual(run.call_count, 2)  # Never moved a replacement either.
            run.return_value = self.result()
            with self.assertRaises(WindowNotFound):
                adapter.find("unique")
            run.return_value = self.result("0x01 0 0 0 1 1 host unique\n0x02 0 0 0 1 1 host unique\n")
            with self.assertRaises(WindowUnavailable):
                adapter.find("unique")
            run.return_value = self.result(returncode=1)
            with self.assertRaises(WindowUnavailable) as error:
                adapter.find("unique")
            self.assertNotIsInstance(error.exception, WindowNotFound)

    def test_wayland_remains_explicitly_unsupported(self):
        with patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}), self.assertRaisesRegex(WindowUnavailable, "Wayland"):
            Linux().find("unique")

    @patch("visor.platforms.run")
    @patch("visor.platforms.shutil.which", return_value="wmctrl")
    def test_x11_close_retries_disappearing_window_race_without_assuming_exit(self, _, run):
        run.side_effect = [self.result("0x01 0 10 20 800 600 host unique\n"),
                           self.result(),
                           self.result(returncode=1, stderr="BadWindow (invalid Window parameter)"),
                           self.result("0x01 0 host unique\n"), self.result("0x02 0 host adjacent\n")]
        with patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": ""}):
            Linux().close("unique", {"handle": "0x01"})
        self.assertEqual(run.call_count, 5)

    @patch("visor.platforms.run")
    @patch("visor.platforms.shutil.which", return_value="wmctrl")
    def test_x11_close_does_not_treat_enumeration_failure_as_closed(self, _, run):
        run.side_effect = [self.result("0x01 0 10 20 800 600 host unique\n"),
                           self.result(), self.result(returncode=1, stderr="Cannot open display")]
        with patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": ""}), \
                self.assertRaisesRegex(WindowUnavailable, "Cannot verify"):
            Linux().close("unique", {"handle": "0x01"})

    @patch("visor.platforms.subprocess.Popen")
    @patch("visor.platforms.shutil.which", return_value="/usr/bin/xterm")
    def test_linux_launch_preserves_identity_and_argument_boundaries(self, _, popen):
        command = ["/tmp/visor helper", "--terminal", "/tmp/a b'c/ticket.json"]
        with patch("visor.platforms.sys.platform", "linux"):
            terminal_launch(command, "/tmp/a b'c", "unique", {"linux_terminal": "xterm"})
        args = popen.call_args.args[0]
        self.assertEqual(args[args.index("-e") + 1:], command)
        self.assertEqual(args[args.index("-xrm") + 1], "XTerm.vt100.allowTitleOps: false")
        self.assertEqual(popen.call_args.kwargs["cwd"], "/tmp/a b'c")

    @patch("visor.platforms.run")
    def test_mac_missing_is_distinct_from_automation_denial(self, run):
        run.return_value = self.result(returncode=1, stderr="VISOR_WINDOW_NOT_FOUND")
        with self.assertRaises(WindowNotFound):
            Mac().find("unique")
        run.return_value = self.result(returncode=1, stderr="Not authorized to send Apple events")
        with self.assertRaises(WindowUnavailable) as error:
            Mac().find("unique")
        self.assertNotIsInstance(error.exception, WindowNotFound)

    @patch("visor.platforms.run")
    def test_mac_close_revalidates_window_and_protects_other_tabs(self, run):
        run.side_effect = [self.result("42"), self.result("")]
        Mac().close("unique", {"handle": "42"})
        args = run.call_args_list[0].args[0]
        self.assertEqual(args[-2:], ["unique", "42"])
        self.assertIn("VISOR_IDENTITY_CHANGED", args[2])
        self.assertIn("count of tabs of w is not 1", args[2])

    @patch("visor.platforms.run")
    def test_mac_launch_quotes_cwd_and_command(self, run):
        run.return_value = self.result()
        with patch("visor.platforms.sys.platform", "darwin"), \
                patch.dict(os.environ, {"PATH": "/tmp/node bin:/usr/bin",
                                        "PYTHONPATH": "/tmp/source's $(literal)"}, clear=True):
            terminal_launch(["/tmp/visor helper", "--terminal", "a'b"], "/tmp/a b'c", "unique")
        command = run.call_args.args[0][-2]
        import shlex
        self.assertEqual(shlex.split(command), ["cd", "/tmp/a b'c", "&&", "exec",
                                                "/usr/bin/env", "PATH=/tmp/node bin:/usr/bin",
                                                "PYTHONPATH=/tmp/source's $(literal)",
                                                "/tmp/visor helper", "--terminal", "a'b"])

    @patch("visor.platforms.run")
    def test_mac_focus_restores_minimized_window_and_checks_identity(self, run):
        run.return_value = self.result("42")
        Mac().focus("token", {"handle": "42"})
        args = run.call_args.args[0]
        self.assertEqual(args[-2:], ["token", "42"])
        self.assertIn("set miniaturized of w to false", args[2])
        self.assertIn('if application "Terminal" is not running', args[2])

    @patch("visor.platforms.run")
    def test_mac_rejected_controls_report_reason(self, run):
        for marker, reason in [("VISOR_EXTRA_TABS", "other tabs"),
                               ("VISOR_IDENTITY_CHANGED", "identity changed"),
                               ("VISOR_WINDOW_AMBIGUOUS", "ambiguous")]:
            with self.subTest(marker=marker):
                run.return_value = self.result(returncode=1, stderr=marker)
                with self.assertRaisesRegex(WindowUnavailable, reason):
                    Mac().close("token", {"handle": "42"})

    @patch("visor.platforms.run")
    def test_mac_close_poll_does_not_reopen_terminal(self, run):
        run.side_effect = [self.result("42"), self.result("")]
        Mac().close("token", {"handle": "42"})
        self.assertIn('if application "Terminal" is not running then return ""',
                      run.call_args.args[0][2])

    @patch("visor.platforms.windows")
    def test_focus_rechecks_identity_on_every_os(self, windows):
        focus_verified({"token": "unique", "identity": {"handle": "42"}})
        windows.return_value.focus.assert_called_once_with("unique", expected_identity={"handle": "42"})

    def test_current_native_process_identity(self):
        identity = process_identity(os.getpid())
        self.assertEqual(identity["pid"], os.getpid())
        self.assertFalse(retired(identity))
        self.assertTrue(retired({**identity, "created": identity["created"] - 1}))

    def test_unix_missing_denied_and_pid_reuse(self):
        expected = {"pid": 42, "created": 10, "image": "/usr/bin/python"}
        with patch("visor.process_identity.sys.platform", "linux"), \
                patch("visor.process_identity.process_identity") as inspect:
            inspect.side_effect = ProcessLookupError(errno.ESRCH, "gone")
            self.assertTrue(retired(expected))
            inspect.side_effect = PermissionError(errno.EACCES, "denied")
            self.assertFalse(retired(expected))
            inspect.side_effect = None
            inspect.return_value = {**expected, "created": 11}
            self.assertTrue(retired(expected))

    def test_unix_process_access_denied_is_not_missing(self):
        with patch("visor.process_identity.sys.platform", "linux"), \
                patch("visor.process_identity.psutil.Process", side_effect=psutil.AccessDenied(42)), \
                self.assertRaises(PermissionError):
            process_identity(42)


if __name__ == "__main__":
    unittest.main()
