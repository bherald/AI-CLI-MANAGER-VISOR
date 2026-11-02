import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from native_notices import collect_linux_notices, copy_reviewed_snapshot
from verify_native_package import package_resource


class NativeDependencyNoticesCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.source = self.root / "libfixture.so.1"
        self.source.write_bytes(b"\x7fELFcomplete fixture binary")
        self.analysis = self.root / "Analysis-00.toc"
        self.analysis.write_text(repr(([('libfixture.so.1', str(self.source), 'BINARY')],)), encoding="utf-8")
        self.native = [{"path": "_internal/libfixture.so.1", "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest()}]
        self.doc = self.root / "doc"
        (self.doc / "libfixture1").mkdir(parents=True)
        self.notice = self.doc / "libfixture1/copyright"
        self.notice.write_text("Full copyright José 尾\nSee /usr/share/common-licenses/LGPL-2.1.\n", encoding="utf-8")
        self.common = self.root / "common"
        self.common.mkdir()
        (self.common / "LGPL-2.1").write_text("Complete referenced license\n", encoding="utf-8")

    def run_collection(self, runner=None):
        def lookup(args, **_kwargs):
            text = f"libfixture1:amd64: {self.source}\n" if args[1] == "-S" else "libfixture1:amd64\t1.2-3\tfixture\t1:1.2-3"
            return subprocess.CompletedProcess(args, 0, text, "")
        return collect_linux_notices([self.analysis], self.native, self.root / "output",
                                    doc_root=self.doc, common_root=self.common, runner=runner or lookup,
                                    distribution={"ID": "ubuntu"})

    def test_actual_native_binding_copies_full_original_notice_license_and_exact_source_version(self):
        report = self.run_collection()
        package = report["packages"][0]
        self.assertEqual((self.root / "output" / package["copyright"]["path"]).read_bytes(), self.notice.read_bytes())
        self.assertEqual((self.root / "output" / package["referenced_licenses"][0]["path"]).read_bytes(),
                         (self.common / "LGPL-2.1").read_bytes())
        self.assertEqual(package["source_access"], "https://launchpad.net/ubuntu/+source/fixture/1%3A1.2-3")
        self.assertEqual(report["bindings"][0]["packaged_sha256"], self.native[0]["sha256"])
        self.assertEqual(report["issues"], [])
        self.assertNotIn(str(self.root), json.dumps(report))

    def test_ambiguous_original_sources_are_reported_without_a_guessed_notice(self):
        other = self.root / "other.so"
        other.write_bytes(b"other")
        self.analysis.write_text(repr([('libfixture.so.1', str(p), 'BINARY') for p in [self.source, other]]), encoding="utf-8")
        report = self.run_collection(lambda *_args, **_kwargs: self.fail("Ambiguous input must not query or choose an owner"))
        self.assertEqual(report["issues"][0]["reason"], "ambiguous_native_source")
        self.assertEqual(report["packages"], [])

    def test_wheel_or_bootloader_without_distribution_owner_is_not_claimed_cleared(self):
        report = self.run_collection(lambda args, **_kwargs: subprocess.CompletedProcess(args, 1, "", "not owned"))
        self.assertEqual(report["without_distribution_metadata"], ["_internal/libfixture.so.1"])
        self.assertEqual(report["packages"], [])
        self.assertNotIn("complete", report)

    def test_batched_owner_query_keeps_matches_when_a_wheel_has_no_package(self):
        wheel = self.root / "wheel.so"
        wheel.write_bytes(b"\x7fELFwheel")
        self.analysis.write_text(repr([('libfixture.so.1', str(self.source), 'BINARY'),
                                       ('wheel.so', str(wheel), 'EXTENSION')]), encoding="utf-8")
        self.native.append({"path": "_internal/wheel.so", "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()})
        calls = []
        def lookup(args, **_kwargs):
            calls.append(args)
            if args[1] == "-S":
                self.assertIn(str(wheel), args)
                self.assertIn(str(self.source), args)
                return subprocess.CompletedProcess(args, 1, f"libfixture1:amd64: {self.source}\n", "unowned wheel")
            return subprocess.CompletedProcess(args, 0, "libfixture1:amd64\t1.2-3\tfixture\t1:1.2-3\n", "")
        report = self.run_collection(lookup)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(report["packages"]), 1)
        self.assertEqual(len(report["bindings"]), 1)
        self.assertEqual(report["without_distribution_metadata"], ["_internal/wheel.so"])

    def test_copyright_symlink_outside_documentation_is_not_copied(self):
        self.notice.unlink()
        secret = self.root / "not-a-notice"
        secret.write_text("synthetic private marker", encoding="utf-8")
        self.notice.symlink_to(secret)
        report = self.run_collection()
        self.assertEqual(report["issues"][0]["reason"], "package_metadata_or_copyright_missing")
        self.assertEqual(report["packages"], [])
        self.assertNotIn("synthetic private marker", json.dumps(report))

    def test_missing_referenced_license_is_explicit_while_original_copyright_stays_complete(self):
        (self.common / "LGPL-2.1").unlink()
        report = self.run_collection()
        self.assertEqual(report["issues"][0]["reason"], "referenced_license_missing")
        package = report["packages"][0]
        self.assertEqual((self.root / "output" / package["copyright"]["path"]).read_bytes(), self.notice.read_bytes())

    def test_extracted_metadata_references_cannot_read_outside_the_package(self):
        resources = self.root / "package"
        resources.mkdir()
        (resources / "dependency-sources.json").write_text("{}", encoding="utf-8")
        self.assertEqual(package_resource(resources, "dependency-sources.json"), resources / "dependency-sources.json")
        for name in ["../libfixture.so.1", str(self.source), None]:
            with self.subTest(name=name), self.assertRaises((RuntimeError, TypeError)):
                package_resource(resources, name)

    def test_native_entries_without_analysis_are_reported_not_silently_cleared(self):
        self.native.append({"path": "visor-console", "sha256": "a" * 64})
        report = self.run_collection()
        self.assertEqual(report["without_analysis_source"], ["visor-console"])
        self.assertEqual(len(report["bindings"]), 1)

    def test_reviewed_complete_notice_snapshot_copies_exact_bytes_and_rejects_tamper(self):
        source = self.root / "reviewed"
        source.mkdir()
        raw = b"Complete copyright and all third-party notices\n"
        (source / "LICENSE").write_bytes(raw)
        manifest = {"component": "fixture", "version": "1.0", "files": [
            {"path": "LICENSE", "sha256": hashlib.sha256(raw).hexdigest()}]}
        (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(copy_reviewed_snapshot(source, self.root / "licenses"), manifest)
        self.assertEqual((self.root / "licenses/upstream-fixture-1.0/LICENSE").read_bytes(), raw)
        (source / "LICENSE").write_bytes(raw + b"changed")
        with self.assertRaises(ValueError):
            copy_reviewed_snapshot(source, self.root / "tampered")
        self.assertFalse((self.root / "tampered").exists())
