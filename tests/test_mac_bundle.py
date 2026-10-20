import plistlib
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from visor.mac_bundle import AUTOMATION_REASON, prepare_info_plist, verify_info_plist
from visor.paths import own_command


class MacBundleCase(unittest.TestCase):
    def test_console_helper_is_a_bundle_with_console_stdio_enabled(self):
        root = Path(__file__).resolve().parents[1]
        analysis = Mock(return_value=SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[]))
        exe, bundle = Mock(), Mock()
        runpy.run_path(str(root / "scripts/macos_console.spec"), init_globals={
            "SPECPATH": str(root / "scripts"), "Analysis": analysis,
            "PYZ": Mock(), "EXE": exe, "COLLECT": Mock(), "BUNDLE": bundle,
        })
        self.assertTrue(exe.call_args.kwargs["console"])
        self.assertEqual(bundle.call_args.kwargs["bundle_identifier"], "org.visor.supervisor.helper")
        self.assertEqual(bundle.call_args.kwargs["name"], "visor-console.app")
        self.assertEqual(bundle.call_args.kwargs["info_plist"]["NSAppleEventsUsageDescription"],
                         AUTOMATION_REASON)

    def test_gui_and_helper_resolve_the_same_nested_console_executable(self):
        gui = Path("/Applications/Renamed App.app/Contents/MacOS/visor")
        helper = gui.parent.parent / "Helpers/visor-console.app/Contents/MacOS/visor-console"
        with patch("visor.paths.sys.platform", "darwin"), \
                patch.object(sys, "frozen", True, create=True):
            for executable in (gui, helper):
                with self.subTest(executable=executable), patch("visor.paths.sys.executable", str(executable)):
                    self.assertEqual(own_command(console=True), [str(helper)])

    def test_helper_bundle_identifier_is_verified_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "visor-console.app"
            (app / "Contents").mkdir(parents=True)
            with (app / "Contents/Info.plist").open("wb") as stream:
                plistlib.dump({"CFBundleIdentifier": "org.visor.supervisor.helper"}, stream)
            prepare_info_plist(app)
            with self.assertRaises(ValueError):
                verify_info_plist(app)
            verify_info_plist(app, bundle_identifier="org.visor.supervisor.helper")

    def test_automation_metadata_survives_existing_bundle_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "visor.app"
            (app / "Contents").mkdir(parents=True)
            path = app / "Contents/Info.plist"
            with path.open("wb") as stream:
                plistlib.dump({"CFBundleIdentifier": "org.visor.supervisor",
                               "CFBundleExecutable": "visor"}, stream)
            with self.assertRaises(ValueError):
                verify_info_plist(app)
            prepare_info_plist(app)
            verify_info_plist(app)
            with path.open("rb") as stream:
                info = plistlib.load(stream)
            self.assertEqual(info["CFBundleExecutable"], "visor")
            self.assertEqual(info["NSAppleEventsUsageDescription"], AUTOMATION_REASON)
            info.pop("NSAppleEventsUsageDescription")
            with path.open("wb") as stream:
                plistlib.dump(info, stream)
            with self.assertRaises(ValueError):
                verify_info_plist(app)
