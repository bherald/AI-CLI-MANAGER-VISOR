"""Explicit operator management of saved Prism boards, never provider sessions."""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
)

from .paths import write_private
from .selection import RowTable, capture_selection, restore_selection, selected_ids

RETENTION = ("Deletes this saved board, its revision history, replies and membership, and closes its Prism view. "
             "Local layout and drafts for this board are removed. Shared image assets and existing backups are retained. "
             "Provider sessions and their conversations are unaffected.")


class PrismManagementDialog(QDialog):
    def __init__(self, supervisor):
        super().__init__(supervisor)
        self.supervisor = supervisor
        self.setWindowTitle("Manage Prism boards")
        self.resize(960, 470)
        self.pending_path = Path(supervisor.root) / "prism-management.json"
        self.pending, self.boards = {}, {}
        self.busy = False
        self.loaded = False
        self.pending_error = ""
        layout = QVBoxLayout(self)
        note = QLabel("Select saved Prism boards. Ctrl/Cmd-click toggles; Shift-click selects a range; Ctrl/Cmd+A selects all.\n" + RETENTION)
        note.setTextFormat(Qt.TextFormat.PlainText)
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = RowTable(0, 6)
        self.table.setHorizontalHeaderLabels(["Prism board", "Owner", "Updated", "Revision", "Requested view", "Deletion"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAccessibleName("Saved Prism boards")
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self.update_controls)
        self.table.deleteRequested.connect(lambda: self.delete_selected())
        self.table.setSortingEnabled(True)
        self.table.sortItems(0, Qt.SortOrder.AscendingOrder)
        layout.addWidget(self.table, 1)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.reload_button = QPushButton("Refresh boards")
        self.reload_button.clicked.connect(self.reload)
        row.addWidget(self.reload_button)
        self.delete_button = QPushButton("Delete selected…")
        self.delete_button.clicked.connect(self.delete_selected)
        row.addWidget(self.delete_button)
        row.addStretch()
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)
        self.read_pending()
        self.reload()

    def read_pending(self):
        try:
            if not self.pending_path.exists():
                return
            value = json.loads(self.pending_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("pending"), dict):
                raise TypeError("Invalid saved deletion requests")
            for identity, request in value["pending"].items():
                if (not isinstance(identity, str) or not isinstance(request, dict)
                        or request.get("id") != identity or not isinstance(request.get("title"), str)
                        or type(request.get("expected_revision")) is not int or request["expected_revision"] < 1
                        or not isinstance(request.get("request_id"), str) or not request["request_id"]
                        or request.get("confirmed") is not True):
                    raise ValueError("Invalid saved deletion request")
            self.pending = value["pending"]
        except (OSError, ValueError, TypeError) as exc:
            self.pending_error = "Saved deletion requests could not be read; deletion is disabled: " + str(exc)

    def selected_id(self):
        identities = selected_ids(self.table)
        return identities[0] if identities else None

    def update_controls(self):
        identities = selected_ids(self.table)
        retry = bool(identities) and all(identity in self.pending and (identity not in self.boards
            or self.boards[identity]["revision"] == self.pending[identity]["expected_revision"]) for identity in identities)
        self.delete_button.setText(("Retry same deletion" if len(identities) == 1 else f"Retry selected deletions ({len(identities)})")
                                   if retry else (f"Delete selected boards ({len(identities)})…" if identities else "Delete selected…"))
        self.delete_button.setEnabled(bool(identities) and self.loaded and not self.busy and not self.pending_error)
        self.reload_button.setEnabled(not self.busy)

    def render(self):
        selection = capture_selection(self.table)
        identities = list(self.boards) + [key for key in self.pending if key not in self.boards]
        names = {session["id"]: session["name"] for session in getattr(self.supervisor, "snapshot", {}).get("sessions", [])}
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for identity in identities:
            board, pending = self.boards.get(identity), self.pending.get(identity)
            source = board or pending
            row = self.table.rowCount()
            self.table.insertRow(row)
            state = "Unconfirmed; retry exact request" if pending else ""
            if pending and board and board["revision"] != pending["expected_revision"]:
                state = "Revision changed; review again"
            owner = source.get("owner")
            values = [source["title"], source.get("owner_name") or names.get(owner, "Session unavailable" if owner else "—"), source.get("updated", "—"),
                      str(source.get("revision", source.get("expected_revision"))),
                      str(source.get("view_action", "Not listed")), state]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, identity)
                    item.setToolTip("Board ID: " + identity)
                if column == 1 and owner:
                    item.setToolTip("Session ID: " + owner)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        restore_selection(self.table, selection)
        self.table.resizeRowsToContents()
        self.update_controls()

    def reload(self):
        if self.busy:
            return
        client = self.supervisor.client
        if client is None:
            self.loaded = False
            self.status.setText("VISOR is disconnected. Reconnect, then refresh boards.")
            self.render()
            return
        self.busy = True
        self.update_controls()
        self.status.setText("Loading saved Prism boards…")
        def finished(result, error):
            self.busy = False
            if error:
                self.loaded = False
                self.status.setText("Boards unavailable: " + str(error))
            else:
                try:
                    if not isinstance(result, list) or any(not isinstance(board, dict)
                            or not isinstance(board.get("id"), str) or not isinstance(board.get("title"), str)
                            or type(board.get("revision")) is not int or board["revision"] < 1 for board in result):
                        raise ValueError("Invalid board list")
                    if len({board["id"] for board in result}) != len(result):
                        raise ValueError("Ambiguous board identities; refresh before selecting boards")
                    self.boards = {board["id"]: board for board in result}
                    self.loaded = True
                    self.status.setText(self.pending_error or (f"{len(result)} saved boards. Select boards to manage."
                                        + (" Unconfirmed deletions remain available for exact retry." if self.pending else "")))
                except ValueError as exc:
                    self.loaded = False
                    self.status.setText(str(exc))
            self.render()
        self.supervisor.job(lambda: client.call("activity_list"), finished)

    def confirm_delete(self, board):
        box = QMessageBox(self)
        box.setWindowTitle("Delete saved Prism board?")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(f"Delete {board['title']}?\nBoard ID: {board['id']}\nRevision: {board['revision']}")
        box.setInformativeText(RETENTION)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setEscapeButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def confirm_delete_many(self, boards, selected_count):
        box = QMessageBox(self)
        box.setWindowTitle("Delete saved Prism boards?")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setTextFormat(Qt.TextFormat.PlainText)
        names = "\n".join(board["title"] for board in boards[:8])
        if len(boards) > 8:
            names += f"\n…and {len(boards) - 8} more"
        box.setText(f"Delete {selected_count} selected Prism boards?\n\n" + names)
        box.setDetailedText("\n".join(f"{board['title']} — Board ID: {board['id']} — Revision: {board['revision']}" for board in boards))
        box.setInformativeText(RETENTION)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setEscapeButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def delete_selected(self):
        identities = selected_ids(self.table)
        if not identities or not self.loaded or self.busy or self.pending_error:
            return
        client = self.supervisor.client
        if client is None:
            self.status.setText("VISOR is disconnected. No deletion was sent.")
            return
        fresh = []
        for identity in identities:
            pending, board = self.pending.get(identity), self.boards.get(identity)
            if not pending or board and board["revision"] != pending["expected_revision"]:
                if board is None:
                    return
                fresh.append(board)
        if fresh and not (self.confirm_delete(fresh[0]) if len(identities) == 1
                          else self.confirm_delete_many(fresh, len(identities))):
            return
        updated = dict(self.pending)
        for board in fresh:
            updated[board["id"]] = {"id": board["id"], "title": board["title"],
                                    "expected_revision": board["revision"],
                                    "request_id": uuid.uuid4().hex, "confirmed": True}
        # Save every exact identity before sending any of the guarded requests.
        try:
            write_private(self.pending_path, {"pending": updated})
        except (OSError, ValueError) as exc:
            self.status.setText("Deletion not sent; unable to save its retry identity: " + str(exc))
            return
        self.pending = updated
        requests = [copy.deepcopy(updated[identity]) for identity in identities]
        self.busy = True
        self.update_controls()
        completed, errors = [], []
        details = []
        needs_poll = False

        def dispatch(index):
            nonlocal needs_poll
            if index == len(requests):
                self.busy = False
                if len(requests) == 1:
                    self.status.setText(details[0])
                else:
                    self.status.setText(f"Deleted {len(completed)} of {len(requests)} selected Prism boards. "
                                        "Image assets and backups were retained."
                                        + (f" {len(errors)} remain selected for review or exact retry.\n" + "\n".join(errors) if errors else ""))
                if needs_poll:
                    self.supervisor.poll()
                self.render()
                return
            request = requests[index]
            identity = request["id"]
            params = {key: request[key] for key in ("id", "expected_revision", "request_id", "confirmed")}
            self.status.setText(f"Deleting selected Prism boards ({index + 1}/{len(requests)}): {request['title']}…")

            def finished(result, error):
                nonlocal needs_poll
                detail = ""
                if (error or not isinstance(result, dict) or result.get("deleted") is not True
                        or result.get("id") != identity or result.get("revision") != request["expected_revision"]):
                    detail = ("Deletion unconfirmed: " + str(error or "Unexpected broker response")
                              + ". Retry uses the same saved request. Refresh to review a revision conflict.")
                else:
                    # Keep durable retry identities until local cleanup is complete.
                    try:
                        self.supervisor.prism.deleted(identity)
                    except (OSError, ValueError) as exc:
                        detail = ("Deletion confirmed; local cleanup pending: " + str(exc)
                                  + ". Retry the same deletion to finish cleanup.")
                    if not detail:
                        remaining = {key: value for key, value in self.pending.items() if key != identity}
                        try:
                            write_private(self.pending_path, {"pending": remaining})
                            self.pending = remaining
                        except (OSError, ValueError):
                            detail = "Deletion confirmed; retry record cleanup failed. Refresh before continuing."
                        needs_poll = True
                    if not detail:
                        completed.append(identity)
                        self.boards.pop(identity, None)
                        detail = ("Deleted " + request["title"] + ". Image assets and backups were retained."
                                  + (" View-file cleanup is pending." if result.get("projection_cleanup") == "pending" else ""))
                details.append(detail)
                if identity not in completed:
                    errors.append(request["title"] + ": " + detail)
                self.render()
                dispatch(index + 1)

            self.supervisor.job(lambda: client.call("activity_delete", **params), finished)

        dispatch(0)


def manage_prism_windows(supervisor):
    dialog = getattr(supervisor, "prism_management_dialog", None)
    if dialog is None:
        dialog = PrismManagementDialog(supervisor)
        supervisor.prism_management_dialog = dialog
    else:
        dialog.reload()
    dialog.exec()
