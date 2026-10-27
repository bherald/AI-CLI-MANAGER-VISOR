"""Capture the real native UI using synthetic data in disposable private state.

Run with the repository interpreter. No broker, provider discovery, live profile,
model calls or external application is started. Browser captures are separate.
The manifest deliberately starts with manual review pending after every run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "1"
os.environ["TZ"] = "America/New_York"
if hasattr(time, "tzset"):
    time.tzset()

from PySide6.QtCore import QCoreApplication, QEvent, QItemSelectionModel, qVersion
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QHeaderView

from visor import __version__
from visor.activity import ActivityWindow
from visor.prism_workspace import PrismWorkspace
from visor.ui import STYLE, Window

_STAMP = datetime(2026, 10, 27, 17, 35, tzinfo=UTC).timestamp()
_IMAGES = ("visor-main.png", "visor-beta.png", "prism-workspace.png", "prism-tables.png")


def settle(app):
    """Drain layout work without waiting for network or periodic application jobs."""
    for _ in range(5):
        app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def select_rows(table, rows):
    table.clearSelection()
    for row in rows:
        table.selectionModel().select(table.model().index(row, 0),
                                      QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)


def snapshot():
    sessions = []
    fixtures = [
        ("codex", "release-lead", "Release lead", "working", "Preparing the staged rollout summary"),
        ("claude", "quality-review", "Quality review", "working", "Retesting the final permissions scenario"),
        ("codex", "interface-review", "Interface review", "waiting", "Desktop and phone layouts are ready for review"),
        ("claude", "support-guide", "Support guide", "idle", "Support notes and recovery steps are ready"),
    ]
    for index, (provider, native, name, status, detail) in enumerate(fixtures):
        sessions.append({"id": provider + ":" + native, "native_id": native, "provider": provider,
                         "name": name, "provider_name": name, "cwd": "/demo/customer-portal",
                         "status": status, "latest": detail, "description": detail, "participates": True,
                         "heartbeat": time.time(), "last_used": _STAMP - index * 120})
    release, quality, interface, support = [session["id"] for session in sessions]
    messages = [
        ("operator", release, "Review the final permissions retest and prepare the staged rollout summary.", "acknowledged"),
        (quality, release, "Retest evidence is ready; one permissions case needs your review.", "delivered"),
        (interface, quality, "Desktop and phone layout checks passed. Evidence is ready.", "completed"),
        (support, release, "Support notes and recovery steps are ready for final review.", "acknowledged"),
    ]
    return {"sessions": sessions, "pairs": [[release, quality], [release, interface], [release, support]],
            "messages": [{"id": f"10000000-0000-4000-8000-{index + 1:012}", "sender": sender,
                          "recipient": recipient, "body": body, "state": state,
                          "created": _STAMP - (4 - index) * 60, "detail": "Synthetic release-review message",
                          "origin": "operator" if sender == "operator" else "agent", "kind": "message"}
                         for index, (sender, recipient, body, state) in enumerate(messages)],
            "agent_enabled": True}


def save(widget, output, name):
    image = widget.grab()
    if image.isNull() or not image.save(str(output / name), "PNG"):
        raise RuntimeError("Native image did not render: " + name)
    return {"path": str((output / name).relative_to(ROOT)), "width": image.width(), "height": image.height(),
            "sha256": hashlib.sha256((output / name).read_bytes()).hexdigest()}


def supervisor(app, state, output, *, populated):
    window = Window(state, client=None)
    window.timer.stop()
    window.draft_timer.stop()
    window.resize(1360, 850 if populated else 760)
    try:
        if populated:
            window.snapshot = snapshot()
            window.render_sessions()
            window.populate_combos()
            window.render_messages()
            window.agent_toggle.blockSignals(True)
            window.agent_toggle.setChecked(True)
            window.agent_toggle.blockSignals(False)
            for combo, identity in ((window.source, "codex:interface-review"),
                                    (window.target, "claude:support-guide"),
                                    (window.recipient, "codex:release-lead")):
                combo.blockSignals(True)
                combo.setCurrentIndex(combo.findData(identity))
                combo.blockSignals(False)
            window.composer.blockSignals(True)
            window.composer.setText("Please review the remaining permissions retest.")
            window.composer.blockSignals(False)
            window.prism.boards = [
                {"id": "customer-portal", "title": "Customer portal launch", "view_state": {"unshown_revision": 1}},
                {"id": "styled-project", "title": "Project release review", "view_state": {"unshown_revision": 1}},
            ]
            window.prism.update_indicator()
            window.sessions.setColumnWidth(0, 210)
            window.sessions.setColumnWidth(1, 70)
            window.sessions.setColumnWidth(2, 85)
            window.sessions.setColumnWidth(4, 145)
            window.sessions.setColumnWidth(5, 200)
            window.messages.horizontalHeader().setStretchLastSection(False)
            window.messages.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            window.messages.setColumnWidth(0, 70)
            window.messages.setColumnWidth(1, 270)
            window.messages.setColumnWidth(3, 145)
            select_rows(window.sessions, [0, 1])
            select_rows(window.messages, [0, 1])
            select_rows(window.pairs, [0])
        else:
            window.render_sessions()
            window.populate_combos()
            window.render_messages()
        window.status.setText("Illustrative sessions and messages · No live provider data" if populated
                              else "Illustrative empty workspace · No broker or provider connected")
        window.show()
        settle(app)
        window.reflow_sections()
        window.messages.setFocus()
        window.draft_timer.stop()
        settle(app)
        if window.client is not None or window.jobs or window.polling:
            raise RuntimeError("A release capture must not connect to a broker")
        return save(window, output, "visor-main.png" if populated else "visor-beta.png")
    finally:
        window.timer.stop()
        window.draft_timer.stop()
        window.allow_close = True
        window.close()
        window.pool.waitForDone(1000)
        window.deleteLater()
        settle(app)


def prism(app, state, output):
    workspace = PrismWorkspace(state / "workspace")
    workspace.resize(1360, 920)
    windows = []
    try:
        for filename in ("customer-portal.json", "styled-project.json"):
            document = json.loads((ROOT / "examples/prism" / filename).read_text(encoding="utf-8"))
            source = state / filename
            source.write_text(json.dumps(document), encoding="utf-8")
            board = ActivityWindow(source, state / "layouts")
            board.watch.stop()
            board.receipt_timer.stop()
            board.layout_timer.stop()
            board.session_names = {"codex:release-lead": "Release lead", "claude:quality-review": "Quality review",
                                   "codex:interface-review": "Interface review"}
            board.apply_document(board.document)
            board.status.setText("Illustrative release review · Local layout and direct card actions")
            windows.append(board)
            workspace.add_board(board, focus=False)
        settle(app)
        overview, tables = windows
        # This is a real local card layout, like an operator resizing cards.
        # Slightly wider overview cards keep status columns and captions readable.
        for index, data in enumerate(overview.document["cards"]):
            overview.cards[data["id"]].setGeometry(20 + index % 3 * 430, 20 + index // 3 * 320, 410, 300)
        overview_table = overview.cards["table-2"].content.content
        for column, width in enumerate((170, 116, 88)):
            overview_table.setColumnWidth(column, width)
        workspace.stack.setCurrentWidget(overview)
        workspace.refresh()
        settle(app)
        first = save(workspace, output, "prism-workspace.png")
        workspace.stack.setCurrentWidget(tables)
        workspace.refresh()
        select_rows(tables.cards["release-checks"].content.content, [0])
        tables.cards["release-checks"].content.content.clearFocus()
        settle(app)
        second = save(workspace, output, "prism-tables.png")
        return [first, second]
    finally:
        for board in windows:
            board.watch.stop()
            board.receipt_timer.stop()
            board.layout_timer.stop()
        workspace.close()
        workspace.deleteLater()
        settle(app)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-only", action="store_true", help="Capture native images (browser images use their own script)")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/images")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if not output.is_relative_to(ROOT):
        parser.error("Image output must stay inside this repository")
    output.mkdir(parents=True, exist_ok=True)
    cache = ROOT / ".cache" / ("release-" + __version__ + "-addon")
    cache.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("VISOR release image capture")
    app.setStyle("Fusion")
    app.setFont(QFont("DejaVu Sans", 10))
    app.setStyleSheet(STYLE)
    entries = []
    # Fail closed if any future UI constructor accidentally starts discovery.
    with tempfile.TemporaryDirectory(prefix="native-image-state-", dir=cache) as temporary, \
            patch("visor.ui.ensure_broker", side_effect=AssertionError("Capture attempted a broker connection")):
        state = Path(temporary)
        os.chmod(state, 0o700)
        entries.append(supervisor(app, state / "main", output, populated=True))
        entries.append(supervisor(app, state / "empty", output, populated=False))
        entries.extend(prism(app, state, output))
    if {Path(entry["path"]).name for entry in entries} != set(_IMAGES):
        raise RuntimeError("Unexpected release image set")
    sources = ["scripts/make_release_images.py", "src/visor/__init__.py", "src/visor/ui.py",
               "src/visor/selection.py", "src/visor/activity.py", "src/visor/activity_gadgets.py",
               "src/visor/activity_table.py", "src/visor/prism_theme.py",
               "src/visor/activity_table_widget.py", "src/visor/prism_workspace.py",
               "examples/prism/customer-portal.json", "examples/prism/styled-project.json"]
    manifest = {"version": __version__, "generated_utc": datetime.now(UTC).isoformat(),
                "renderer": "Actual current Qt widgets; no mockups or image synthesis",
                "synthetic_data_only": True, "live_broker_or_provider": False,
                "state": "Disposable private temporary directory; removed after capture",
                "platform": platform.system(), "qt_version": qVersion(), "font": app.font().family(),
                "display_timezone": os.environ["TZ"], "scale_factor": 1,
                "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "sources": [{"path": name, "sha256": hashlib.sha256((ROOT / name).read_bytes()).hexdigest()} for name in sources],
                "images": entries, "manual_review": {"status": "pending", "notes": []}}
    destination = cache / "image-manifest.json"
    destination.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"images": entries, "manifest": str(destination.relative_to(ROOT))}, indent=2))


if __name__ == "__main__":
    main()
