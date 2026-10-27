"""One native home for Prism boards; board documents remain independent."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .paths import write_private
from .prism_theme import THEMES, apply_theme


class PrismWorkspace(QMainWindow):
    def __init__(self, state_dir, manager=None):
        super().__init__()
        self.manager = manager
        self.state_dir = Path(state_dir)
        self.boards = {}
        self.hidden_boards = set()
        self.web_server = None
        self.web_port = 0
        self.web_host = "127.0.0.1"
        self.web_was_active = False
        self.theme_name = "calm-dark"
        self.setWindowTitle("Prism")
        self.resize(1360, 900)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 6, 8, 4)
        layout.setSpacing(4)
        navigation = QHBoxLayout()
        self.previous = QPushButton("‹")
        self.next = QPushButton("›")
        for button, name, step in ((self.previous, "Previous Prism board", -1), (self.next, "Next Prism board", 1)):
            button.setMinimumWidth(112)
            button.setMinimumHeight(32)
            button.setAccessibleName(name)
            button.setToolTip(name)
            button.clicked.connect(lambda checked=False, offset=step: self.advance(offset))
        self.selector = QComboBox()
        self.selector.setAccessibleName("Prism board")
        self.selector.currentIndexChanged.connect(self.choose_index)
        self.position = QLabel("No boards open")
        navigation.addWidget(self.previous)
        navigation.addWidget(self.selector, 1)
        navigation.addWidget(self.position)
        navigation.addWidget(self.next)
        layout.addLayout(navigation)
        self.sessions = QLabel()
        self.sessions.setTextFormat(Qt.TextFormat.PlainText)
        self.sessions.setObjectName("metadata")
        self.sessions.setMaximumHeight(22)
        self.sessions.setAccessibleName("Connected sessions")
        layout.addWidget(self.sessions)
        self.toggle = QToolButton()
        self.toggle.setText("Board actions")
        self.toggle.setCheckable(True)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.actions = QWidget()
        actions = QHBoxLayout(self.actions)
        actions.setContentsMargins(0, 0, 0, 0)
        for label, callback in (("Hide board", self.hide_current), ("Show all boards", self.show_all),
                                ("Open in browser", self.open_browser)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        self.hidden = QComboBox()
        self.hidden.setAccessibleName("Show a hidden board")
        self.hidden.activated.connect(self.show_hidden)
        actions.addWidget(self.hidden, 1)
        self.theme = QComboBox()
        self.theme.setAccessibleName("Prism theme")
        for name, label in THEMES.items():
            self.theme.addItem(label, name)
        self.theme.currentIndexChanged.connect(self.change_theme)
        actions.addWidget(self.theme)
        self.toggle.toggled.connect(self.toggle_actions)
        layout.addWidget(self.actions)
        self.actions.hide()
        self.stack = QStackedWidget()
        self.empty = QLabel("Open a saved Prism board from VISOR, or show a hidden board above.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.empty)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        try:
            saved = json.loads((self.state_dir / "prism-workspace.json").read_text())
            if not isinstance(saved, dict):
                raise TypeError("Invalid workspace preferences")
            port = saved.get("web_port", 0)
            self.web_port = port if type(port) is int and 0 <= port <= 65535 else 0
            try:
                from .web_access import validated_bind_host
                self.web_host = validated_bind_host(saved.get("web_host", "127.0.0.1"))
                self.web_was_active = (saved.get("web_was_active") is True
                                       and "web_host" in saved and self.web_port > 0)
            except ValueError:
                self.web_host = "127.0.0.1"
            self.theme_name = saved.get("theme") if saved.get("theme") in THEMES else "calm-dark"
            geometry = saved.get("geometry")
            if isinstance(geometry, list) and len(geometry) == 4 and all(type(v) is int for v in geometry):
                screen = self.screen().availableGeometry()
                self.setGeometry(max(screen.left(), min(geometry[0], screen.right() - 200)),
                                 max(screen.top(), min(geometry[1], screen.bottom() - 100)),
                                 max(600, min(geometry[2], screen.width())), max(400, min(geometry[3], screen.height())))
        except (OSError, ValueError, TypeError):
            pass
        self.theme.setCurrentIndex(self.theme.findData(self.theme_name))
        self.change_theme()
        self.refresh()

    def change_theme(self, index=None):
        from .activity import STYLE
        self.theme_name = self.theme.currentData() or "calm-dark"
        apply_theme(self, self.theme_name, base_style=STYLE)
        for board in self.boards.values():
            apply_theme(board, self.theme_name, base_style=STYLE)

    def toggle_actions(self, expanded):
        self.actions.setVisible(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        current = self.stack.currentWidget()
        if current in self.boards.values():
            current.actions_toggle.setChecked(expanded)

    def add_board(self, board, focus=True):
        key = board.document["id"]
        if key not in self.boards:
            self.boards[key] = board
            board.workspace = self
            board.setWindowFlags(Qt.WindowType.Widget)
            self.stack.addWidget(board)
            from .activity import STYLE
            apply_theme(board, self.theme_name, base_style=STYLE)
        self.hidden_boards.discard(key)
        if focus or self.stack.currentWidget() is self.empty:
            self.stack.setCurrentWidget(board)
        if self.stack.currentWidget() is board:
            board.showNormal() if board.isMinimized() else board.show()
        board.actions_toggle.hide()
        board.actions_toggle.setChecked(self.toggle.isChecked())
        self.refresh()
        self.showNormal() if self.isMinimized() else self.show()
        if focus:
            self.raise_()
            self.activateWindow()

    def refresh(self):
        current = self.stack.currentWidget()
        self.selector.blockSignals(True)
        self.selector.clear()
        for key, board in self.boards.items():
            if key not in self.hidden_boards:
                self.selector.addItem(board.document["title"], key)
        selected = self.selector.findData(current.document["id"]) if current in self.boards.values() else -1
        self.selector.setCurrentIndex(selected)
        self.selector.blockSignals(False)
        self.previous.setEnabled(self.selector.count() > 1)
        self.next.setEnabled(self.selector.count() > 1)
        self.position.setText(f"{selected + 1} / {self.selector.count()}" if selected >= 0 else "No boards open")
        self.hidden.clear()
        self.hidden.addItem("Show a hidden board…", None)
        if self.manager:
            candidates = self.manager.boards
        else:
            candidates = [{"id": key, "title": board.document["title"]} for key, board in self.boards.items()]
        for board in candidates:
            if board["id"] in self.hidden_boards or board["id"] not in self.boards:
                self.hidden.addItem(board["title"], board["id"])
        self.hidden.setEnabled(self.hidden.count() > 1)
        names = getattr(current, "session_names", {})
        self.sessions.setText("Connected: " + " · ".join(dict.fromkeys(names.values())) if names else "Local board")
        self.sessions.setToolTip(self.sessions.text())
        self.setWindowTitle("Prism — " + current.document["title"] if current in self.boards.values() else "Prism")

    def choose_index(self, index):
        key = self.selector.itemData(index)
        if key in self.boards:
            self.stack.setCurrentWidget(self.boards[key])
            self.boards[key].actions_toggle.setChecked(self.toggle.isChecked())
            self.boards[key].observe_view_later()
            self.refresh()

    def advance(self, offset):
        count = self.selector.count()
        if count:
            self.selector.setCurrentIndex((self.selector.currentIndex() + offset) % count)

    def hide_current(self):
        board = self.stack.currentWidget()
        if board in self.boards.values() and board.close():
            self.board_hidden(board)

    def board_hidden(self, board):
        self.hidden_boards.add(board.document["id"])
        if self.stack.currentWidget() is board:
            other = next((item for key, item in self.boards.items() if key not in self.hidden_boards), self.empty)
            self.stack.setCurrentWidget(other)
        self.refresh()

    def show_hidden(self, index):
        key = self.hidden.itemData(index)
        if not key:
            return
        if self.manager:
            board = next((item for item in self.manager.boards if item["id"] == key), None)
            if board:
                self.manager.operator_open(board)
        elif key in self.boards:
            self.add_board(self.boards[key])

    def show_all(self):
        if self.manager:
            for board in list(self.manager.boards):
                self.manager.operator_open(board)
        else:
            for board in list(self.boards.values()):
                self.add_board(board, focus=False)

    def remove_board(self, board):
        self.board_hidden(board)
        self.stack.removeWidget(board)
        self.boards.pop(board.document["id"], None)
        self.hidden_boards.discard(board.document["id"])
        self.refresh()

    def open_browser(self):
        current = self.stack.currentWidget()
        if not self.manager:
            if current in self.boards.values():
                current.status.setText("Browser sharing requires a connected VISOR board.")
            return
        try:
            if self.web_server is None:
                from .web_access import start_web_access
                start_web_access(self.manager, "127.0.0.1", self.web_port)
            QDesktopServices.openUrl(QUrl(self.web_server.url))
        except (OSError, RuntimeError, ValueError) as exc:
            self.manager.supervisor.status.setText("Browser view unavailable: " + str(exc))

    def closeEvent(self, event):
        for board in self.boards.values():
            if board.document["id"] not in self.hidden_boards and not board.close():
                event.ignore()
                return
        self.hidden_boards.update(self.boards)
        self.save_preferences()
        self.refresh()
        event.accept()

    def save_preferences(self):
        if self.web_server is not None:
            self.web_host = QUrl(self.web_server.origin).host()
            self.web_port = QUrl(self.web_server.origin).port()
            self.web_was_active = True
        try:
            write_private(self.state_dir / "prism-workspace.json", {"geometry": [self.x(), self.y(), self.width(), self.height()],
                "theme": self.theme_name, "web_port": self.web_port,
                "web_host": self.web_host, "web_was_active": self.web_was_active})
        except OSError:
            pass

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "stack"):
            current = self.stack.currentWidget()
            if current in self.boards.values():
                current.observe_view_later()

    def shutdown(self):
        self.save_preferences()
        if self.web_server:
            self.web_server.close()
            self.web_server = None
        self.hide()
