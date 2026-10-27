"""Standard row selection with stable identities shared by VISOR grids."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from PySide6.QtCore import QItemSelectionModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTableWidget,
    QTextEdit,
)


def row_identity(table, row):
    item = table.item(row, 0)
    identity = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
    if isinstance(identity, list):
        identity = tuple(identity)
    if isinstance(identity, str) and identity:
        return identity
    if isinstance(identity, tuple) and identity and all(isinstance(part, str) and part for part in identity):
        return identity
    return None


def selected_ids(table):
    """Only return unambiguous IDs, in the current visible row order."""
    counts = Counter(row_identity(table, row) for row in range(table.rowCount()))
    return [identity for index in sorted(table.selectionModel().selectedRows(), key=lambda index: index.row())
            if (identity := row_identity(table, index.row())) is not None and counts[identity] == 1]


@dataclass
class RowSelection:
    selected: set
    current: object
    scroll: int


def capture_selection(table):
    return RowSelection(set(selected_ids(table)), row_identity(table, table.currentRow()),
                        table.verticalScrollBar().value())


def restore_selection(table, state):
    """Restore selected rows and the Shift anchor after rebuilding or sorting."""
    identities = [row_identity(table, row) for row in range(table.rowCount())]
    counts = Counter(identities)
    selection = table.selectionModel()
    selection.clear()
    selection.setCurrentIndex(QModelIndex(), QItemSelectionModel.SelectionFlag.NoUpdate)
    for row, identity in enumerate(identities):
        if identity is None or counts[identity] != 1:
            continue
        index = table.model().index(row, 0)
        if identity in state.selected:
            selection.select(index, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        if identity == state.current:
            selection.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
    table.verticalScrollBar().setValue(state.scroll)


class RowTable(QTableWidget):
    """Native ExtendedSelection, plus platform selection and scoped Delete keys."""

    deleteRequested = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)

    def keyPressEvent(self, event):
        focus = QApplication.focusWidget()
        editing = (self.state() == QTableWidget.State.EditingState
                   or isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QComboBox))
                   and self.isAncestorOf(focus))
        if not editing:
            select_all = (event.matches(QKeySequence.StandardKey.SelectAll)
                          or event.key() == Qt.Key.Key_A
                          and event.modifiers() in (Qt.KeyboardModifier.ControlModifier,
                                                    Qt.KeyboardModifier.MetaModifier))
            if select_all:
                self.selectAll()
                event.accept()
                return
            if event.key() == Qt.Key.Key_Delete and event.modifiers() == Qt.KeyboardModifier.NoModifier:
                if not event.isAutoRepeat():
                    self.deleteRequested.emit()
                event.accept()
                return
        super().keyPressEvent(event)
