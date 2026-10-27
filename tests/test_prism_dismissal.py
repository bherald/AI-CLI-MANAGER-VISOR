"""A stacked, inactive board still obeys an operator dismissal from another view."""
import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from visor.activity import ActivityManager
from visor.activity_document import ActivityPolicy
from visor.broker import Broker


class PrismDismissalCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.broker = Broker(self.root)
        self.owner = self.broker.store.upsert_session("codex", "owner", "Release lead", str(self.root))
        for identity in ("a", "b"):
            self.broker.activities.publish(self.owner,
                {"schema_version": 1, "id": identity, "title": "Board " + identity.upper(), "revision": 1,
                 "cards": [{"id": "notes", "title": "Notes", "kind": "text", "html": "Project details"}]},
                "publish-" + identity)
        self.jobs = []
        client = Mock()
        client.call.side_effect = lambda operation, **params: self.broker.dispatch(operation, params, "operator", "operator")
        self.supervisor = SimpleNamespace(root=self.root, client=client, status=QLabel(),
            snapshot={"sessions": self.broker.store.sessions()}, prism_button=QPushButton("Prism"),
            job=lambda function, callback: self.jobs.append((function, callback)))
        self.manager = ActivityManager(self.supervisor)

    def tearDown(self):
        self.manager.close()
        if self.manager.workspace:
            self.manager.workspace.deleteLater()
        self.supervisor.status.deleteLater()
        self.supervisor.prism_button.deleteLater()
        self.jobs.clear()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def settle(self):
        self.app.processEvents()
        for _ in range(30):
            if not self.jobs:
                return
            function, callback = self.jobs.pop(0)
            callback(function(), None)
            self.app.processEvents()
        self.fail("Presentation reports did not settle")

    def refresh(self):
        self.manager.update(self.broker.activities.list(None), asdict(ActivityPolicy()))
        self.settle()

    def test_externally_dismissed_inactive_board_needs_explicit_reopen(self):
        self.refresh()
        workspace = self.manager.workspace
        workspace.selector.setCurrentIndex(workspace.selector.findData("a"))
        self.settle()
        inactive = self.manager.windows["b"]
        self.assertFalse(inactive.isVisible())
        self.assertNotIn("b", workspace.hidden_boards)
        self.broker.activities.control(None, "b", "close", "other-operator-close")
        self.refresh()
        self.assertIn("b", workspace.hidden_boards)
        self.assertEqual(workspace.selector.findData("b"), -1)
        self.assertFalse(inactive.isVisible())
        workspace.advance(1)
        self.assertEqual(workspace.stack.currentWidget().document["id"], "a")
        board = next(board for board in self.manager.boards if board["id"] == "b")
        self.manager.operator_open(board)
        self.settle()
        self.assertNotIn("b", workspace.hidden_boards)
        self.assertGreaterEqual(workspace.selector.findData("b"), 0)
        self.assertEqual(workspace.stack.currentWidget(), inactive)
        self.assertTrue(inactive.isVisible())
        self.assertEqual(len(self.broker.activities.list(self.owner)), 2)
