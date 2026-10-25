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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .paths import write_private

RETENTION = ("Deletes this saved board, its revision history, replies and membership, and closes its Prism view. "
             "Local layout and drafts for this board are removed. Shared image assets and existing backups are retained. "
             "Provider sessions and their conversations are unaffected.")


class PrismManagementDialog(QDialog):
    def __init__(self, supervisor):
        super().__init__(supervisor)
        self.supervisor = supervisor
        self.setWindowTitle("Manage Prism windows")
        self.resize(960, 470)
        self.pending_path = Path(supervisor.root) / "prism-management.json"
        self.pending, self.boards = {}, {}
        self.busy = False
        self.loaded = False
        self.pending_error = ""
        layout = QVBoxLayout(self)
        note = QLabel("Select a saved Prism board to delete. Nothing is selected automatically.\n" + RETENTION)
        note.setTextFormat(Qt.TextFormat.PlainText)
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Title / board ID", "Owner", "Updated", "Revision", "Requested view", "Deletion"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAccessibleName("Saved Prism boards")
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self.update_controls)
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
        selected = self.table.selectionModel().selectedRows()
        return self.table.item(selected[0].row(), 0).data(Qt.ItemDataRole.UserRole) if selected else None

    def update_controls(self):
        identity = self.selected_id()
        pending, board = self.pending.get(identity), self.boards.get(identity)
        retry = pending and (board is None or board["revision"] == pending["expected_revision"])
        self.delete_button.setText("Retry same deletion" if retry else "Delete selected…")
        self.delete_button.setEnabled(bool(identity) and self.loaded and not self.busy and not self.pending_error)
        self.reload_button.setEnabled(not self.busy)

    def render(self):
        selected = self.selected_id()
        identities = list(self.boards) + [key for key in self.pending if key not in self.boards]
        self.table.setRowCount(0)
        for identity in identities:
            board, pending = self.boards.get(identity), self.pending.get(identity)
            source = board or pending
            row = self.table.rowCount()
            self.table.insertRow(row)
            state = "Unconfirmed; retry exact request" if pending else ""
            if pending and board and board["revision"] != pending["expected_revision"]:
                state = "Revision changed; review again"
            values = [source["title"] + "\n" + identity, source.get("owner", "—"), source.get("updated", "—"),
                      str(source.get("revision", source.get("expected_revision"))),
                      str(source.get("view_action", "Not listed")), state]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, identity)
                self.table.setItem(row, column, item)
            if identity == selected:
                self.table.selectRow(row)
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
                    self.boards = {board["id"]: board for board in result}
                    self.loaded = True
                    self.status.setText(self.pending_error or (f"{len(result)} saved boards. Select one to manage."
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
        return box.exec() == QMessageBox.StandardButton.Yes

    def delete_selected(self):
        identity = self.selected_id()
        if not identity or not self.loaded or self.busy or self.pending_error:
            return
        client = self.supervisor.client
        if client is None:
            self.status.setText("VISOR is disconnected. No deletion was sent.")
            return
        pending, board = self.pending.get(identity), self.boards.get(identity)
        if not pending or board and board["revision"] != pending["expected_revision"]:
            if board is None or not self.confirm_delete(board):
                return
            pending = {"id": identity, "title": board["title"], "expected_revision": board["revision"],
                       "request_id": uuid.uuid4().hex, "confirmed": True}
        # Commit the exact identity before dispatch. A lost result can be retried
        # safely even after closing the dialog or restarting the supervisor.
        updated = {**self.pending, identity: pending}
        try:
            write_private(self.pending_path, {"pending": updated})
        except (OSError, ValueError) as exc:
            self.status.setText("Deletion not sent; unable to save its retry identity: " + str(exc))
            return
        self.pending = updated
        request = copy.deepcopy(pending)
        params = {key: request[key] for key in ("id", "expected_revision", "request_id", "confirmed")}
        self.busy = True
        self.update_controls()
        self.status.setText("Waiting for deletion confirmation for " + identity + "…")
        def finished(result, error):
            self.busy = False
            if (error or not isinstance(result, dict) or result.get("deleted") is not True
                    or result.get("id") != identity or result.get("revision") != request["expected_revision"]):
                self.status.setText("Deletion unconfirmed: " + str(error or "Unexpected broker response")
                                    + ". Retry uses the same saved request. Refresh to review a revision conflict.")
                self.render()
                return
            # Canonical deletion is confirmed. Retain the durable retry identity
            # until the view and its local drafts are also safely removed.
            try:
                self.supervisor.prism.deleted(identity)
            except (OSError, ValueError) as exc:
                self.status.setText("Deletion confirmed; local cleanup pending: " + str(exc)
                                    + ". Retry the same deletion to finish cleanup.")
                self.render()
                return
            updated = {key: value for key, value in self.pending.items() if key != identity}
            try:
                write_private(self.pending_path, {"pending": updated})
                self.pending = updated
            except (OSError, ValueError):
                # The durable request remains safe to retry against the tombstone.
                self.status.setText("Deletion confirmed; retry record cleanup failed. Refresh before continuing.")
                self.supervisor.poll()
                self.render()
                return
            self.boards.pop(identity, None)
            self.status.setText("Deleted " + request["title"] + ". Image assets and backups were retained."
                                + (" Window-file cleanup is pending." if result.get("projection_cleanup") == "pending" else ""))
            self.supervisor.poll()
            self.render()
        self.supervisor.job(lambda: client.call("activity_delete", **params), finished)


def manage_prism_windows(supervisor):
    dialog = getattr(supervisor, "prism_management_dialog", None)
    if dialog is None:
        dialog = PrismManagementDialog(supervisor)
        supervisor.prism_management_dialog = dialog
    else:
        dialog.reload()
    dialog.exec()
