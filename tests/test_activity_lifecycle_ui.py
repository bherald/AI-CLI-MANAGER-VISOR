"""Operator view intent and observed presentation with isolated Qt and fake broker jobs."""
import copy
import json
import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from visor.activity import ActivityManager
from visor.activity_document import ActivityPolicy


class ActivityLifecycleCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "board.json"
        self.document = {"schema_version": 1, "id": "board", "title": "Saved discussion", "revision": 1,
                         "cards": [{"id": "note", "title": "Notes", "kind": "text", "html": "Original"}]}
        self.source.write_text(json.dumps(self.document))
        self.metadata = {"id": "board", "title": "Saved discussion", "path": str(self.source), "revision": 1,
                         "view_seq": 1, "view_action": "open", "view_state": self.state()}
        self.jobs, self.calls, self.managers = [], [], []
        self.client = Mock()
        self.client.call.side_effect = self.broker
        self.supervisor = SimpleNamespace(root=self.root, client=self.client, status=QLabel(),
                                          prism_button=QPushButton("Prism"), job=lambda fn, cb: self.jobs.append((fn, cb)))

    def tearDown(self):
        for manager in self.managers:
            manager.close()
        self.supervisor.status.deleteLater()
        self.supervisor.prism_button.deleteLater()
        self.jobs.clear()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.temp.cleanup()

    @staticmethod
    def state(**updates):
        return {"operator_dismissed": False, "presentation": "unknown", "presented_revision": 0,
                "unshown_revision": 1, "reopen_requested": False, "requested_by": "codex:fixture",
                "requested_revision": 1, "observed_at": None, "last_operator_open_seq": 0, **updates}

    def manager(self):
        manager = ActivityManager(self.supervisor)
        self.managers.append(manager)
        return manager

    def broker(self, operation, **params):
        self.calls.append((operation, copy.deepcopy(params)))
        if operation == "activity_control":
            self.metadata["view_seq"] += 1
            self.metadata["view_action"] = params["action"]
            self.metadata["view_state"].update(operator_dismissed=False, presentation="requested", reopen_requested=False,
                                                 last_operator_open_seq=self.metadata["view_seq"])
            return copy.deepcopy(self.metadata)
        if operation != "activity_view":
            raise AssertionError("Unexpected action " + operation)
        accepted = params["view_seq"] == self.metadata["view_seq"]
        if params["state"] == "closed":
            accepted = self.metadata["view_state"]["last_operator_open_seq"] <= params["view_seq"] <= self.metadata["view_seq"]
        if accepted:
            state = self.metadata["view_state"]
            state["presentation"] = params["state"]
            if params["state"] == "closed":
                self.metadata["view_seq"] += 1
                state["operator_dismissed"] = True
            elif params["state"] == "visible" and not state["operator_dismissed"]:
                state.update(presented_revision=params["revision"], unshown_revision=None)
        return {"id": "board", "view_seq": self.metadata["view_seq"], "accepted": accepted,
                "view_state": copy.deepcopy(self.metadata["view_state"])}

    def finish(self, result=None, error=None):
        function, callback = self.jobs.pop(0)
        callback(function() if result is None and error is None else result, error)

    def settle(self):
        self.app.processEvents()
        for _ in range(20):
            if not self.jobs:
                return
            self.finish()
            self.app.processEvents()
        self.fail("Unexpected repeated presentation jobs")

    def open(self):
        manager = self.manager()
        manager.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
        self.settle()
        return manager, manager.windows["board"]

    def test_restart_preserves_dismissal_and_quiet_coalesced_requests(self):
        self.metadata.update(view_seq=5, revision=3)
        self.metadata["view_state"] = self.state(operator_dismissed=True, presentation="closed",
                                                  unshown_revision=3, reopen_requested=True)
        manager = self.manager()
        manager.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
        self.assertEqual(manager.windows, {})
        self.assertEqual(self.supervisor.prism_button.text(), "Prism (1)")
        for _ in range(3):
            manager.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
        self.assertEqual(self.jobs, [])
        manager.close()
        resumed = self.manager()
        resumed.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
        self.assertEqual(resumed.windows, {})
        self.assertEqual(resumed.dismissed, {"board": 5})

    def test_offline_close_survives_restart_and_retries_exact_durable_request(self):
        manager, window = self.open()
        window.cards["note"].editor.setPlainText("Keep this draft")
        self.supervisor.client = None
        window.close()
        self.assertTrue(window.isHidden())
        request = copy.deepcopy(manager.pending["board"])
        self.assertEqual(request["state"], "closed")
        saved = json.loads(manager.state_path.read_text())
        self.assertEqual(saved["pending"]["board"], request)
        manager.close()
        resumed = self.manager()
        resumed.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
        self.assertEqual(resumed.windows, {})
        self.supervisor.client = self.client
        resumed.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
        self.settle()
        self.assertEqual(self.calls[-1], ("activity_view", request))
        self.assertTrue(self.metadata["view_state"]["operator_dismissed"])
        self.assertTrue((self.root / "prism-layouts/board.layout.json").exists())

    def test_operator_choose_waits_for_control_then_reopens_and_reports_actual_revision(self):
        self.metadata["view_state"] = self.state(operator_dismissed=True, presentation="closed", reopen_requested=True)
        manager = self.manager()
        manager.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
        with patch("visor.activity.QInputDialog.getItem", return_value=("Saved discussion · board · Updated, Reopen requested", True)):
            manager.choose()
        self.assertEqual(manager.windows, {})
        self.assertEqual(len(self.jobs), 1)
        self.finish()
        self.assertIn("board", manager.windows)
        self.settle()
        self.assertEqual(self.calls[0][0], "activity_control")
        self.assertEqual(self.calls[0][1]["action"], "open")
        self.assertEqual(self.calls[-1][1]["state"], "visible")
        self.assertEqual(self.calls[-1][1]["revision"], 1)
        self.assertNotIn("board", manager.dismissed)
        self.assertFalse(manager.windows["board"].isHidden())

    def test_minimized_explicit_reopen_restores_but_ordinary_update_does_not_focus(self):
        manager, window = self.open()
        window.showMinimized()
        self.settle()
        self.assertTrue(window.isMinimized())
        self.document["revision"] = 2
        self.document["cards"][0]["html"] = "New content"
        self.source.write_text(json.dumps(self.document))
        self.metadata["revision"] = 2
        with patch.object(window, "activateWindow") as focus, patch.object(window, "raise_") as raised:
            manager.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
            self.settle()
            focus.assert_not_called()
            raised.assert_not_called()
        self.assertTrue(window.isMinimized())
        manager.operator_open(copy.deepcopy(self.metadata))
        self.settle()
        self.assertFalse(window.isMinimized())
        self.assertEqual(self.calls[-1][1]["revision"], 2)
        self.assertEqual(self.calls[-1][1]["state"], "visible")

    def test_agent_close_and_supervisor_shutdown_do_not_latch_operator_dismissal(self):
        manager, window = self.open()
        self.metadata.update(view_seq=2, view_action="close")
        manager.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
        self.settle()
        self.assertTrue(window.isHidden())
        self.assertNotIn("board", manager.dismissed)
        self.assertEqual(self.calls[-1][1]["state"], "hidden")
        before = len(self.calls)
        manager.close()
        self.settle()
        self.assertEqual(len(self.calls), before)
        self.assertFalse(window.watch.isActive())
        self.assertFalse(window.receipt_timer.isActive())

    def test_late_visible_callback_cannot_replace_newer_close_intent(self):
        manager = self.manager()
        manager.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
        self.app.processEvents()
        self.assertEqual(len(self.jobs), 1)
        window = manager.windows["board"]
        window.close()
        closed = copy.deepcopy(manager.pending["board"])
        self.assertEqual(closed["state"], "closed")
        self.finish()
        self.assertEqual(manager.pending["board"], closed)
        self.assertEqual(len(self.jobs), 1)
        self.settle()
        self.assertTrue(self.metadata["view_state"]["operator_dismissed"])
        self.assertTrue(window.isHidden())

    def test_newer_operator_reopen_supersedes_stale_pending_close_and_callback(self):
        manager, window = self.open()
        window.close()
        self.assertEqual(len(self.jobs), 1)
        self.metadata.update(view_seq=9, view_action="open")
        self.metadata["view_state"] = self.state(operator_dismissed=False, presentation="requested", last_operator_open_seq=9)
        manager.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
        self.assertFalse(window.isHidden())
        self.finish(result={"id": "board", "view_seq": 2, "accepted": True,
                            "view_state": self.state(operator_dismissed=True, presentation="closed")})
        self.settle()
        self.assertNotIn("board", manager.dismissed)
        self.assertFalse(window.isHidden())
        self.assertEqual(window.view_sequence, 9)

    def test_reopen_waits_for_pending_close_receipt_before_control(self):
        manager, window = self.open()
        self.supervisor.client = None
        window.close()
        manager.operator_open(copy.deepcopy(self.metadata))
        self.assertIn("board", manager.reopen_waiting)
        self.assertEqual(self.jobs, [])
        self.supervisor.client = self.client
        manager.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
        self.settle()
        self.assertEqual([operation for operation, _ in self.calls[-3:]],
                         ["activity_view", "activity_control", "activity_view"])
        self.assertFalse(window.isHidden())
        self.assertNotIn("board", manager.dismissed)

    def test_list_absence_preserves_drafts_until_exact_delete_confirmation(self):
        manager, window = self.open()
        window.cards["note"].editor.setPlainText("Retain until confirmed deletion")
        manager.update([], asdict(ActivityPolicy()))
        layout = self.root / "prism-layouts/board.layout.json"
        self.assertTrue(layout.exists())
        self.assertFalse(window.watch.isActive())
        self.assertEqual(manager.windows, {})
        manager.deleted("board")
        self.assertFalse(layout.exists())
        self.assertTrue(self.source.exists())

    def test_failed_durable_close_write_keeps_window_open_and_sends_no_close(self):
        manager, window = self.open()
        with patch("visor.activity.write_private", side_effect=OSError("Full disk")):
            window.close()
        self.assertFalse(window.isHidden())
        self.assertNotIn("board", manager.dismissed)
        self.assertEqual(self.jobs, [])
        self.assertNotEqual(self.calls[-1][1]["state"], "closed")

    def test_ordinary_visible_revision_update_reports_without_focus_or_read_claim(self):
        _manager, window = self.open()
        self.document["revision"] = 2
        self.source.write_text(json.dumps(self.document))
        with patch.object(window, "activateWindow") as focus:
            window.poll_document()
            self.settle()
            focus.assert_not_called()
        self.assertEqual(self.calls[-1][1]["revision"], 2)
        self.assertEqual(self.calls[-1][1]["state"], "visible")
        self.assertFalse({"read", "acknowledged", "completed"} & self.calls[-1][1].keys())

    def test_deleted_board_invalidates_pending_reopen_callback(self):
        manager, window = self.open()
        manager.operator_open(copy.deepcopy(self.metadata))
        self.assertEqual(len(self.jobs), 1)
        manager.deleted("board")
        self.finish(result={**self.metadata, "view_seq": 2})
        self.app.processEvents()
        self.assertEqual(manager.windows, {})
        self.assertTrue(window.isHidden())
        self.assertFalse((self.root / "prism-layouts/board.layout.json").exists())

    def test_delete_cleanup_failure_still_closes_view_and_propagates_for_retry(self):
        manager, window = self.open()
        with patch("visor.activity.Path.unlink", side_effect=OSError("Cannot remove layout")), self.assertRaises(OSError):
            manager.deleted("board")
        self.assertEqual(manager.windows, {})
        self.assertTrue(window.isHidden())
        self.assertFalse(window.watch.isActive())

    def test_clear_and_broker_restart_reobserve_actual_view_without_focus(self):
        manager, window = self.open()
        self.metadata["view_state"]["presentation"] = "unknown"
        with patch.object(window, "activateWindow") as focus:
            manager.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
            self.settle()
            focus.assert_not_called()
        self.assertEqual(self.calls[-1][1]["state"], "visible")
        self.metadata.update(view_seq=2, view_action="clear")
        self.metadata["view_state"]["presentation"] = "requested"
        manager.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
        self.settle()
        self.assertTrue(all(card.isHidden() for card in window.cards.values()))
        self.assertEqual(self.calls[-1][1]["view_seq"], 2)
        self.assertEqual(self.calls[-1][1]["state"], "visible")

    def test_late_card_reply_callback_cannot_recreate_deleted_layout(self):
        self.document["cards"].append({"id": "ask", "kind": "question", "title": "Question", "prompt": "Continue?"})
        self.source.write_text(json.dumps(self.document))
        manager, window = self.open()
        card = window.cards["ask"]
        card.reply.setPlainText("Keep working")
        card.save_answer()
        self.assertEqual(len(self.jobs), 1)
        manager.deleted("board")
        self.finish(result={"id": "reply-event"})
        self.app.processEvents()
        window.schedule_layout_save()
        window.save_layout()
        self.assertTrue(window.retired)
        self.assertFalse(window.layout_timer.isActive())
        self.assertFalse((self.root / "prism-layouts/board.layout.json").exists())
        self.assertIsNone(window.submit)

    def test_offline_operator_close_survives_newer_agent_focus_and_exact_report_retries(self):
        manager, window = self.open()
        self.supervisor.client = None
        window.close()
        closed = copy.deepcopy(manager.pending["board"])
        manager.close()
        manager = self.manager()
        self.metadata.update(view_seq=2, view_action="focus")
        self.metadata["view_state"].update(operator_dismissed=False, presentation="requested", last_operator_open_seq=0)
        manager.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
        self.assertEqual(manager.windows, {})
        self.assertEqual(manager.pending["board"], closed)
        self.assertEqual(manager.dismissed["board"], 1)
        self.supervisor.client = self.client
        manager.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
        self.settle()
        self.assertEqual(self.calls[-1], ("activity_view", closed))
        self.assertTrue(self.metadata["view_state"]["operator_dismissed"])
        self.assertEqual(manager.windows, {})
        self.assertNotIn("board", manager.pending)

    def test_dismissed_board_without_window_reports_actual_closed_once(self):
        self.metadata["view_state"] = self.state(operator_dismissed=True, presentation="requested")
        manager = self.manager()
        manager.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
        self.assertEqual(manager.windows, {})
        self.settle()
        self.assertEqual(self.calls[-1][1]["state"], "closed")
        self.assertEqual(self.metadata["view_state"]["presentation"], "closed")
        sequence, count = self.metadata["view_seq"], len(self.calls)
        for _ in range(3):
            manager.update([copy.deepcopy(self.metadata)], asdict(ActivityPolicy()))
            self.settle()
        self.assertEqual(self.metadata["view_seq"], sequence)
        self.assertEqual(len(self.calls), count)

    def test_real_supervisor_constructs_with_malformed_prism_state_without_broker_work(self):
        from visor.ui import Window
        (self.root / "prism-view-state.json").write_text("malformed")
        with patch("visor.ui.ensure_broker") as broker, patch.object(Window, "job") as job:
            window = Window(self.root)
            try:
                window.timer.stop()
                window.draft_timer.stop()
                self.assertTrue(window.prism.local_error)
                self.assertIn("Prism saved view state unavailable", window.status.text())
                broker.assert_not_called()
                job.assert_not_called()
            finally:
                window.allow_close = True
                window.close()
                window.deleteLater()
