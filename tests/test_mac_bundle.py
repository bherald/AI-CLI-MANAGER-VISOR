import plistlib
import tempfile
import unittest
from pathlib import Path

from visor.mac_bundle import AUTOMATION_REASON, prepare_info_plist, verify_info_plist


class MacBundleCase(unittest.TestCase):
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
