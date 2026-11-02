"""Selected terminal recovery uses current evidence and existing guarded RPCs."""
import copy
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from visor.ui import Window


def session(key, **fields):
    return {"id": key, "native_id": key, "provider": "codex", "name": key,
            "cwd": "/fixture", "status": "disconnected", "latest": "Saved conversation",
            "participates": False, "heartbeat": None, **fields}


class RecoveryCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = Window(Path(self.temp.name))
        self.window.timer.stop()
        self.calls = []
        self.failures = set()
        self.fresh = {"sessions": [], "external_focus": {}}
        owner = self

        class Client:
            def call(self, action, **params):
                owner.calls.append((action, params))
                if action == "snapshot":
                    return copy.deepcopy(owner.fresh)
                if action == "resume" and params["session"] in owner.failures:
                    raise ValueError("Session already has a live managed worker; use Focus")
                return True

        self.window.client = Client()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.draft_timer.stop()
        self.window.allow_close = True
        self.window.close()
        self.window.pool.waitForDone(1000)
        self.window.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def select(self, sessions):
        self.window.snapshot["sessions"] = sessions
        self.fresh["sessions"] = copy.deepcopy(sessions)
        self.window.render_sessions()
        self.window.sessions.selectAll()

    def run_recovery(self, response=QMessageBox.StandardButton.Yes):
        with patch.object(QMessageBox, "exec", return_value=response) as confirmation, \
                patch.object(self.window, "job", side_effect=lambda work, done: done(work(), None)), \
                patch.object(self.window, "poll"), \
                patch.object(self.window, "show_recovery_report") as report:
            self.window.resume_selected_sessions()
        return confirmation, report

    def test_cancel_sends_nothing_and_confirmation_defaults_to_no(self):
        self.select([session("codex:saved")])
        questions = []

        def denied(box):
            questions.append(box)
            self.assertEqual(box.defaultButton(), box.button(QMessageBox.StandardButton.No))
            return QMessageBox.StandardButton.No

        with patch.object(QMessageBox, "exec", new=denied):
            self.window.resume_selected_sessions()
        self.assertEqual(len(questions), 1)
        self.assertEqual(self.calls, [])

    def test_batch_reports_each_selected_result_and_skips_live_loaded_unknown_or_linked(self):
        sessions = [session("codex:first"), session("codex:race"), session("codex:last"),
                    session("codex:open", heartbeat=time.time()),
                    session("codex:working", status="working", managed=True),
                    session("codex:idle", status="idle", managed=True),
                    session("codex:unknown", status="unknown"),
                    session("codex:external"), session("codex:stale", window_token="old-window")]
        self.select(sessions)
        self.fresh["sessions"].append(session("codex:unselected"))
        self.fresh["external_focus"] = {"codex:external": {"handle": "fixture"}}
        self.failures.add("codex:race")
        confirmation, report = self.run_recovery()
        confirmation.assert_called_once()
        self.assertEqual([params["session"] for action, params in self.calls if action == "resume"],
                         ["codex:first", "codex:race", "codex:last"])
        self.assertEqual([action for action, _params in self.calls[:2]], ["refresh", "snapshot"])
        results = report.call_args.args[2]
        self.assertEqual(results["codex:first"][0], "Launch requested")
        self.assertEqual(results["codex:last"][0], "Launch requested")
        self.assertEqual(results["codex:race"][0], "Error")
        self.assertIn("Inspect before trying again", results["codex:race"][1])
        for key in ("open", "working", "idle", "unknown", "external", "stale"):
            self.assertEqual(results["codex:" + key][0], "Skipped")
        self.assertFalse(self.window.recovering_sessions)
        self.assertTrue(self.window.resume_sessions_button.isEnabled())
        self.assertTrue(all(action in ("refresh", "snapshot", "resume") for action, _ in self.calls))

    def test_single_selection_has_the_same_result_report_and_no_message_replay(self):
        self.select([session("codex:only")])
        self.window.snapshot["messages"] = [{"id": "held", "state": "delivery_unknown"}]
        _confirmation, report = self.run_recovery()
        self.assertEqual(self.calls, [("refresh", {}), ("snapshot", {}),
                                     ("resume", {"session": "codex:only"})])
        self.assertEqual(report.call_args.args[2]["codex:only"][0], "Launch requested")
        self.assertEqual(self.window.snapshot["messages"], [{"id": "held", "state": "delivery_unknown"}])

    def test_partial_provider_refresh_skips_stale_claude_and_continues_healthy_codex(self):
        self.select([session("codex:healthy"), session("claude:stale", provider="claude")])
        self.fresh["provider_warnings"] = {"claude": "Claude live discovery unavailable: fixture failure"}
        self.fresh["refresh_error"] = "Claude: fixture failure"
        _confirmation, report = self.run_recovery()
        self.assertEqual([params["session"] for action, params in self.calls if action == "resume"],
                         ["codex:healthy"])
        results = report.call_args.args[2]
        self.assertEqual(results["codex:healthy"][0], "Launch requested")
        self.assertEqual(results["claude:stale"][0], "Skipped")
        self.assertIn("discovery", results["claude:stale"][1])

    def test_fresh_evidence_prevents_retargeting_deleted_changed_or_duplicate_sessions(self):
        self.select([session("codex:deleted"), session("codex:changed"), session("codex:duplicate"),
                     session("codex:became-live")])
        self.fresh["sessions"] = [session("codex:changed", cwd="/changed"),
                                  session("codex:duplicate"), session("codex:duplicate"),
                                  session("codex:became-live", status="waiting"), session("codex:new")]
        _confirmation, report = self.run_recovery()
        self.assertEqual([action for action, _params in self.calls], ["refresh", "snapshot"])
        results = report.call_args.args[2]
        self.assertEqual(len(results), 4)
        self.assertTrue(all(state == "Skipped" for state, _detail in results.values()))
        self.assertNotIn("codex:new", results)

    def test_refresh_failure_does_not_launch_from_a_stale_saved_snapshot(self):
        self.select([session("codex:saved")])
        self.window.client = Mock()
        self.window.client.call.side_effect = ValueError("Provider discovery unavailable")

        def work(function, callback):
            try:
                value = function()
            except ValueError as error:
                callback(None, str(error))
            else:
                callback(value, None)

        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Yes), \
                patch.object(self.window, "job", side_effect=work), \
                patch.object(self.window, "show_recovery_report") as report:
            self.window.resume_selected_sessions()
        self.window.client.call.assert_called_once_with("refresh")
        self.assertEqual(report.call_args.args[2]["codex:saved"][0], "Error")
        self.assertFalse(self.window.recovering_sessions)

    def test_confirmation_captures_identity_before_selection_changes(self):
        self.select([session("codex:first"), session("codex:later")])
        self.window.sessions.clearSelection()
        self.window.sessions.selectRow(0)
        selected_key = self.window.sessions.item(0, 0).data(256)

        def accepted(_box):
            self.window.sessions.clearSelection()
            self.window.sessions.selectRow(1)
            return QMessageBox.StandardButton.Yes

        with patch.object(QMessageBox, "exec", new=accepted), \
                patch.object(self.window, "job", side_effect=lambda work, done: done(work(), None)), \
                patch.object(self.window, "poll"), patch.object(self.window, "show_recovery_report"):
            self.window.resume_selected_sessions()
        self.assertEqual([params["session"] for action, params in self.calls if action == "resume"], [selected_key])

    def test_broker_connection_change_is_not_used_to_send_captured_actions(self):
        self.select([session("codex:saved")])
        client = self.window.client
        original_call = client.call
        replacement = Mock()

        def switched(action, **params):
            result = original_call(action, **params)
            if action == "snapshot":
                self.window.client = replacement
            return result

        with patch.object(client, "call", side_effect=switched):
            _confirmation, report = self.run_recovery()
        self.assertEqual([action for action, _params in self.calls], ["refresh", "snapshot"])
        replacement.call.assert_not_called()
        self.assertIn("Broker connection changed", report.call_args.args[2]["codex:saved"][1])

    def test_repeated_click_does_not_duplicate_an_inflight_batch(self):
        self.select([session("codex:saved")])
        jobs = []
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Yes) as question, \
                patch.object(self.window, "job", side_effect=lambda work, done: jobs.append((work, done))), \
                patch.object(self.window, "poll"), patch.object(self.window, "show_recovery_report"):
            self.window.resume_selected_sessions()
            self.window.resume_selected_sessions()
            question.assert_called_once()
            self.assertEqual(len(jobs), 1)
            self.assertFalse(self.window.resume_sessions_button.isEnabled())
            while jobs:
                work, done = jobs.pop(0)
                done(work(), None)
        self.assertEqual(sum(action == "resume" for action, _params in self.calls), 1)

    def test_status_distinguishes_saved_from_open_unattached_and_live_provider(self):
        self.select([session("codex:saved"), session("codex:starting", heartbeat=time.time()),
                     session("codex:working", status="working"), session("codex:unknown", status="unknown")])
        states = {self.window.sessions.item(row, 0).text(): self.window.sessions.item(row, 2).text()
                  for row in range(self.window.sessions.rowCount())}
        self.assertEqual(states, {"codex:saved": "Saved", "codex:starting": "Not attached",
                                  "codex:working": "working", "codex:unknown": "unknown"})


if __name__ == "__main__":
    unittest.main()
