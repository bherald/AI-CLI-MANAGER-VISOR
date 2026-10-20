import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from visor.data_security import ensure_windows_private
from visor.diagnostics import doctor


class SecurityCase(unittest.TestCase):
    @patch("visor.data_security.subprocess.run")
    def test_existing_acl_is_read_only_and_rejects_other_users(self, run):
        root = Path("private data ' & text")
        value = {"user": "S-1-5-21-123", "owner": "S-1-5-21-123",
                 "rules": [{"sid": "S-1-1-0", "allow": True, "rights": 1}]}
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps(value), "")
        with self.assertRaisesRegex(PermissionError, "other Windows accounts"):
            ensure_windows_private(root, created=False)
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["env"]["VISOR_ACL_CREATE"], "0")
        self.assertEqual(kwargs["env"]["VISOR_ACL_TARGET"], str(root))
        self.assertNotIn(str(root), run.call_args.args[0])
        value["rules"][0]["sid"] = value["user"]
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps(value), "")
        ensure_windows_private(root, created=False)

    @unittest.skipUnless(os.name == "nt", "Native Windows ACL")
    def test_new_native_directory_and_existing_private_directory(self):
        cache = Path(__file__).resolve().parents[1] / ".cache" / "acl-tests"
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as directory:
            root = Path(directory)
            ensure_windows_private(root, created=True)
            ensure_windows_private(root, created=False)

    @patch("visor.diagnostics.shutil.which", return_value=None)
    def test_doctor_recognizes_wayland_without_wayland_display(self, _):
        with patch("visor.diagnostics.sys.platform", "linux"), \
                patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": ""}):
            report = doctor()
        self.assertTrue(report["wayland"])
        self.assertEqual(report["window_control"], "unavailable")
