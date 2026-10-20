import json
import tempfile
import unittest
from pathlib import Path

from visor.notices import copy_qt_notices, native_inventory, verify_qt_notices


class NoticesCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.cache = self.root / ".cache/notice-tests"
        self.cache.mkdir(parents=True, exist_ok=True)

    def test_pinned_upstream_snapshot_and_version_guard(self):
        source = self.root / "third_party/qt-6.12.0"
        verified = verify_qt_notices(source, "6.12.0")
        self.assertGreater(verified["files"], 100)
        with self.assertRaisesRegex(ValueError, "does not match"):
            verify_qt_notices(source, "6.13.0")
        with tempfile.TemporaryDirectory(dir=self.cache) as directory:
            destination = Path(directory) / "notices"
            copy_qt_notices(source, destination, "6.12.0")
            manifest = json.loads((destination / "manifest.json").read_text())
            (destination / manifest["files"][0]["path"]).write_text("altered")
            with self.assertRaisesRegex(ValueError, "integrity"):
                verify_qt_notices(destination, "6.12.0")

    def test_inventory_identifies_native_formats_and_uses_relative_paths(self):
        with tempfile.TemporaryDirectory(dir=self.cache) as directory:
            root = Path(directory)
            (root / "native.dll").write_bytes(b"MZ\x00\x00payload")
            (root / "native.so").write_bytes(b"\x7fELFpayload")
            (root / "QtCore").write_bytes(b"\xcf\xfa\xed\xfepayload")
            (root / "note.txt").write_text("license")
            entries = native_inventory(root)
            self.assertEqual({e["path"] for e in entries}, {"native.dll", "native.so", "QtCore"})
            self.assertTrue(all(len(e["sha256"]) == 64 for e in entries))
