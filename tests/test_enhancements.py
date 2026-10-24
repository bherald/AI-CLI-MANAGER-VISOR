import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from visor.appearance import validate
from visor.broker import BUILD, ensure_broker
from visor.build_storage import check_space, cleanup_candidates
from visor.platforms import WindowUnavailable, terminal_launch
from visor.state_backup import connection, restore, snapshot
from visor.store import Store


class AppearanceCase(unittest.TestCase):
    def test_rejects_controls_and_out_of_range_sizes(self):
        for value in ({"linux_size": True}, {"linux_size": 49}, {"mac_profile": "bad\nprofile"}, {"unknown": 1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate(value)

    def test_xterm_font_arguments_preserve_command_and_title_guards(self):
        command = ["helper", "literal argument"]
        with patch("visor.platforms.sys.platform", "linux"), patch("visor.platforms.shutil.which", return_value="xterm"), patch("visor.platforms.subprocess.Popen") as launch:
            terminal_launch(command, "/project", "unique", {"linux_terminal": "xterm", "linux_font": "DejaVu Sans Mono", "linux_size": 16})
        args = launch.call_args.args[0]
        self.assertEqual(args[1:5], ["-fa", "DejaVu Sans Mono", "-fs", "16"])
        self.assertEqual(args[args.index("-e") + 1:], command)
        self.assertIn("XTerm.vt100.allowTitleOps: false", args)

    def test_windows_profile_is_one_argument_and_project_is_preserved(self):
        with patch("visor.platforms.sys.platform", "win32"), patch("visor.platforms.shutil.which", return_value="wt"), patch("visor.platforms.subprocess.Popen") as launch:
            terminal_launch(["helper"], "project space", "unique", {"windows_profile": "Profile ; literal"})
        args = launch.call_args.args[0]
        self.assertEqual(args[args.index("--profile") + 1], "Profile ; literal")
        self.assertEqual(args[args.index("-d") + 1], "project space")

    def test_mac_profile_is_passed_as_data_and_missing_profile_prevents_launch(self):
        result = SimpleNamespace(returncode=0)
        with patch("visor.platforms.sys.platform", "darwin"), patch("visor.platforms.run", return_value=result) as run:
            terminal_launch(["helper"], "/project space", "unique", {"mac_profile": 'Name "quoted"'})
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0][-1], 'Name "quoted"')
        self.assertNotIn('Name "quoted"', run.call_args.args[0][2])
        with patch("visor.platforms.sys.platform", "darwin"), patch("visor.platforms.run", return_value=SimpleNamespace(returncode=1)) as run:
            with self.assertRaises(WindowUnavailable):
                terminal_launch(["helper"], "/project", "unique", {"mac_profile": "Missing"})
            self.assertEqual(run.call_count, 1)


class BackupCase(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / ".cache/tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.key = self.store.upsert_session("codex", "fixture", "Fixture", str(self.root))

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_is_consistent_private_and_retained_by_policy(self):
        self.store.set_setting("backup_options", {"before_upgrade": False, "keep": 2})
        paths = [Path(snapshot(self.root)) for _ in range(3)]
        self.assertFalse(paths[0].exists())
        self.assertTrue(all(p.exists() for p in paths[1:]))
        with connection(paths[-1]) as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT name FROM sessions").fetchone()[0], "Fixture")
        if os.name != "nt":
            self.assertEqual(paths[-1].stat().st_mode & 0o777, 0o600)

    def test_restore_holds_queue_clears_credentials_and_preserves_deduplication(self):
        self.store.set_setting("agent_enabled", True)
        self.store.issue_credential(self.key)
        message = self.store.enqueue("operator", self.key, "fixture", "stable", operator=True)
        path = snapshot(self.root)
        self.store.update_session(self.key, name="Changed")
        restore(self.root, path)
        recovered = Store(self.root)
        self.assertEqual(recovered.session(self.key)["name"], "Fixture")
        self.assertEqual(recovered.message(message["id"])["state"], "held")
        self.assertFalse(recovered.get_setting("agent_enabled"))
        with recovered.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM credentials").fetchone()[0], 0)
        replay = recovered.enqueue("operator", self.key, "fixture", "stable", operator=True)
        self.assertEqual(replay["id"], message["id"])

    def test_restore_refuses_live_helpers_and_unsupported_sql_objects(self):
        path = snapshot(self.root)
        self.store.update_session(self.key, process_id=os.getpid())
        with self.assertRaisesRegex(ValueError, "helpers"):
            restore(self.root, path)
        self.store.update_session(self.key, process_id=None)
        with connection(path) as db:
            db.execute("CREATE VIEW unexpected AS SELECT 1")
        with self.assertRaisesRegex(ValueError, "objects"):
            restore(self.root, path)
        self.assertEqual(self.store.session(self.key)["name"], "Fixture")

    def test_upgrade_snapshot_failure_is_not_bypassed_by_next_reconnect(self):
        self.store.set_setting("backup_options", {"before_upgrade": True, "keep": 2})
        class Client:
            first = True
            def call(self, action):
                if action == "stop":
                    return True
                if self.first:
                    self.first = False
                    return {"build": "old"}
                raise OSError("stopped")
        client = Client()
        with patch("visor.broker.Client", return_value=client), patch("visor.broker.subprocess.Popen") as launch, patch("visor.state_backup.snapshot", side_effect=OSError("disk full")):
            for _ in range(2):
                with self.assertRaisesRegex(OSError, "disk full"):
                    ensure_broker(self.root)
                self.assertTrue((self.root / "upgrade-backup.json").exists())
            launch.assert_not_called()

    def test_successful_upgrade_snapshot_precedes_new_broker_launch(self):
        self.store.set_setting("backup_options", {"before_upgrade": True, "keep": 2})
        class Client:
            first, started = True, False
            def call(self, action):
                if action == "stop":
                    return True
                if self.first:
                    self.first = False
                    return {"build": "old"}
                if self.started:
                    return {"build": BUILD}
                raise OSError("stopped")
        client = Client()
        native_popen = subprocess.Popen
        def launch(*args, **kwargs):
            # Windows snapshot permissions invoke PowerShell through subprocess.run.
            # Intercept only our broker fixture, preserving that real ACL check.
            if args[0][0] != "fixture":
                return native_popen(*args, **kwargs)
            self.assertEqual(len(list((self.root / "backups").glob("*.sqlite3"))), 1)
            client.started = True
        with patch("visor.broker.Client", return_value=client), patch("visor.broker.subprocess.Popen", side_effect=launch), patch("visor.broker.own_command", return_value=["fixture"]):
            self.assertIs(ensure_broker(self.root), client)
        self.assertFalse((self.root / "upgrade-backup.json").exists())


class BuildStorageCase(unittest.TestCase):
    def test_space_check_fails_before_build(self):
        with patch("visor.build_storage.shutil.disk_usage", return_value=SimpleNamespace(free=1)), self.assertRaisesRegex(RuntimeError, "Build needs"):
            check_space(Path.cwd())

    def test_cleanup_keeps_recent_selected_and_running_bundles(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for index in range(5):
                suffix = "visor.app/Contents/Resources/visor" if index == 2 else "visor"
                bundle = root / f"dist/native/{index}" / suffix
                bundle.mkdir(parents=True)
                (bundle / "build-manifest.json").write_text("{}")
                os.utime(root / f"dist/native/{index}", (index + 1, index + 1))
            (root / "dist/latest-build.json").write_text('{"bundle":"native/1/visor"}')
            process = SimpleNamespace(info={"exe": str(root / "dist/native/0/visor/visor"), "cmdline": []})
            with patch("visor.build_storage.psutil.process_iter", return_value=[process]):
                self.assertEqual([p.name for p in cleanup_candidates(root)], ["2"])
            self.assertTrue((root / "dist/native/0").exists())


if __name__ == "__main__":
    unittest.main()
