import hashlib
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from visor.bundles import create_archive, extract_archive


class BundlesCase(unittest.TestCase):
    def setUp(self):
        self.cache = Path(__file__).resolve().parents[1] / ".cache" / "bundle-tests"
        self.cache.mkdir(parents=True, exist_ok=True)

    def test_archive_roundtrip_and_checksum_rejection(self):
        with tempfile.TemporaryDirectory(dir=self.cache) as tmp:
            root = Path(tmp)
            app = root / "visor.app"
            (app / "Contents/Helpers").mkdir(parents=True)
            exe = app / "Contents/Helpers/visor-console"
            exe.write_text("console helper")
            exe.chmod(0o755)
            (app / "LICENSE").write_text("MIT")
            if os.name != "nt":
                (app / "helper-link").symlink_to("Contents/Helpers/visor-console")
            archive = create_archive(app, root / "bundle", windows=False)
            with tarfile.open(archive) as bundle:
                self.assertTrue(all(not member.uname and not member.gname for member in bundle))
            extract_archive(archive, root / "extracted")
            extracted = root / "extracted/visor.app"
            self.assertEqual((extracted / "LICENSE").read_text(), "MIT")
            if os.name != "nt":
                self.assertTrue(os.access(extracted / "Contents/Helpers/visor-console", os.X_OK))
                self.assertTrue((extracted / "helper-link").is_symlink())
            archive.write_bytes(archive.read_bytes() + b"tampered")
            with self.assertRaisesRegex(ValueError, "checksum"):
                extract_archive(archive, root / "bad")

    def test_archive_cannot_escape_extraction_directory(self):
        with tempfile.TemporaryDirectory(dir=self.cache) as tmp:
            root = Path(tmp)
            archive = root / "unsafe.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                item = tarfile.TarInfo("../escaped")
                item.size = 1
                bundle.addfile(item, io.BytesIO(b"x"))
            Path(str(archive) + ".sha256").write_text(hashlib.sha256(archive.read_bytes()).hexdigest())
            with self.assertRaises(tarfile.FilterError):
                extract_archive(archive, root / "extracted")
            self.assertFalse((root / "escaped").exists())
