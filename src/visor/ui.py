from __future__ import annotations

import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizeGrip,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .broker import ensure_broker
from .layout import Rect
from .platforms import enable_window_resize, focus_verified

STYLE = """
QWidget { background: #111c24; color: #e1eaf0; font-size: 13px; }
QMainWindow { border: 1px solid #48616f; }
QPushButton, QComboBox, QLineEdit { background: #192a36; border: 1px solid #425d6d;
    border-radius: 4px; padding: 3px 6px; min-height: 18px; }
QPushButton:hover { border-color: #00cdd2; background: #233b47; }
QPushButton:disabled { color: #70818d; border-color: #2a3c46; }
QPushButton#primary { background: #00bfc7; color: #07181d; font-weight: 600; }
QPushButton#section { text-align: left; border: 0; border-bottom: 1px solid #354b59; min-height: 16px; padding: 2px 4px; }
QTableWidget QPushButton { min-height: 16px; padding: 1px 4px; font-size: 12px; }
QTableWidget { background: #101b23; alternate-background-color: #14232d;
    gridline-color: #293e4b; border: 1px solid #354b59; selection-background-color: #214653; }
QHeaderView::section { background: #182b38; color: #b9d0df; border: 0;
    border-right: 1px solid #2b424f; padding: 3px; }
QScrollBar:vertical { width: 12px; background: #14232d; }
QScrollBar::handle:vertical { background: #536d7c; min-height: 24px; border-radius: 4px; }
QSplitter::handle { background: #354b59; height: 4px; }
QToolTip { color: #e1eaf0; background: #263d4b; border: 1px solid #5c7988; }
"""


class Signals(QObject):
    done = Signal(object, object)


class Job(QRunnable):
    def __init__(self, function, callback):
        super().__init__()
        self.function = function
        self.signals = Signals()
        self.signals.done.connect(callback)

    def run(self):
        try:
            result = self.function()
            self.signals.done.emit(result, None)
        except Exception as exc:  # noqa: BLE001 -- worker boundary reports failures to the UI
            self.signals.done.emit(None, str(exc))


def button(text, callback=None, primary=False):
    widget = QPushButton(text)
    if callback:
        widget.clicked.connect(callback)
    if primary:
        widget.setObjectName("primary")
    return widget


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.verticalHeader().hide()
    widget.verticalHeader().setMinimumSectionSize(23)
    widget.verticalHeader().setDefaultSectionSize(25)
    widget.setAlternatingRowColors(True)
    widget.setWordWrap(False)
    widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    widget.horizontalHeader().setStretchLastSection(True)
    widget.horizontalHeader().setMinimumSectionSize(40)
    widget.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
    return widget


class SessionItem(QTableWidgetItem):
    def __init__(self, text, sort_value=None):
        super().__init__(text)
        self.sort_value = text.casefold() if sort_value is None else sort_value

    def __lt__(self, other):
        return self.sort_value < other.sort_value if isinstance(other, SessionItem) else super().__lt__(other)


class Title(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 0, 0)
        label = QLabel("VISOR  ·  Session supervisor")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(label, 1)
        for icon, tip, callback in [(QStyle.StandardPixmap.SP_TitleBarMinButton, "Minimize", window.showMinimized),
                                     (QStyle.StandardPixmap.SP_BrowserReload, "Restore default size", window.default_size),
                                     (QStyle.StandardPixmap.SP_TitleBarCloseButton, "Close supervisor…", window.close)]:
            item = button("", callback)
            item.setIcon(self.style().standardIcon(icon))
            item.setFixedWidth(38)
            item.setToolTip(tip)
            item.setAccessibleName(tip)
            layout.addWidget(item)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.window.windowHandle():
            self.window.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, event):
        self.window.default_size()


class Window(QMainWindow):
    def __init__(self, root, client=None):
        super().__init__()
        self.root = root
        self.client = client
        self.pool = QThreadPool(self)
        self.jobs = set()
        self.snapshot = {"sessions": [], "messages": [], "pairs": []}
        self.polling = False
        self.poll_pending = False
        self.refreshing = False
        self.allow_close = False
        self.shutdown_requested = False
        self.setWindowTitle("VISOR")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setMinimumSize(840, 460)
        self.setMouseTracking(True)
        self._resize_cursor_target = None
        base = QWidget()
        self.setCentralWidget(base)
        outer = QVBoxLayout(base)
        outer.setContentsMargins(7, 7, 7, 4)
        outer.setSpacing(4)
        outer.addWidget(Title(self))
        search = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find session or project…")
        self.search.textChanged.connect(self.render_sessions)
        self.filter = QComboBox()
        self.filter.addItems(["All providers", "codex", "claude"])
        self.filter.currentTextChanged.connect(self.render_sessions)
        search.addWidget(self.search, 1)
        search.addWidget(self.filter)
        search.addWidget(button("Refresh", self.refresh))
        search.addWidget(button("+ New", self.new_session, True))
        menu_button = button("...")
        menu_button.setAccessibleName("Supervisor menu")
        menu = QMenu(menu_button)
        for name, callback in [("Register saved session…", self.register),
                               ("Start / reconnect broker", self.connect_broker),
                               ("Claude Code settings…", self.claude_settings),
                               ("Stop broker…", self.stop_broker),
                               ("Clear completed / failed message text…", self.clear_history),
                               ("Exit…", self.close)]:
            menu.addAction(name, callback)
        menu_button.setMenu(menu)
        search.addWidget(menu_button)
        outer.addLayout(search)

        self.arrange_toggle = button("> Arrange windows")
        self.arrange_toggle.setObjectName("section")
        outer.addWidget(self.arrange_toggle)
        self.arrange_box = QWidget()
        row = QHBoxLayout(self.arrange_box)
        row.setContentsMargins(0, 0, 0, 0)
        self.monitors = QComboBox()
        for screen in QApplication.screens():
            self.monitors.addItem(screen.name())
        row.addWidget(self.monitors)
        for mode in ("Grid", "Columns", "Stacked", "Cascade"):
            control = button(mode, lambda checked=False, m=mode: self.arrange("arrange", mode=m))
            control.setToolTip({"Stacked": "Tile windows top-to-bottom without overlap",
                                "Cascade": "Overlap windows with each title bar visible"}.get(mode, ""))
            row.addWidget(control)
        row.addWidget(button("Save layout", lambda: self.arrange("save_layout")))
        row.addWidget(button("Restore", lambda: self.arrange("restore_layout")))
        self.arrange_box.hide()
        self.arrange_toggle.clicked.connect(lambda: self.collapse(self.arrange_box, self.arrange_toggle, "Arrange windows"))
        outer.addWidget(self.arrange_box)

        stats = QHBoxLayout()
        self.counts = QLabel("No sessions registered")
        self.open_only = QCheckBox("VISOR terminals only")
        self.open_only.setToolTip("Show terminals reporting to VISOR through its helper. Sessions running independently are hidden, even when the provider reports them as working.")
        self.open_only.toggled.connect(self.render_sessions)
        stats.addWidget(self.counts, 1)
        stats.addWidget(self.open_only)
        stats.addWidget(button("Show all", self.clear_session_filters))
        outer.addLayout(stats)
        self.session_hint = QLabel()
        self.session_hint.setWordWrap(True)
        self.session_hint.hide()
        outer.addWidget(self.session_hint)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.sessions = table(["Session / project", "Provider", "Status", "Latest update", "Last used", "Actions"])
        self.sessions.setColumnWidth(0, 230)
        self.sessions.setColumnWidth(1, 75)
        self.sessions.setColumnWidth(2, 95)
        self.sessions.setColumnWidth(3, 250)
        self.sessions.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.sessions.horizontalHeader().setStretchLastSection(False)
        self.sessions.setColumnWidth(4, 145)
        self.sessions.setColumnWidth(5, 165)
        self.sessions.setSortingEnabled(True)
        self.sessions.sortItems(4, Qt.SortOrder.DescendingOrder)
        self.sessions.setToolTip("Double-click a session row to bring its linked console to the foreground.")
        self.sessions.itemDoubleClicked.connect(self.focus_session_item)
        self.splitter.addWidget(self.sessions)
        self.communication = QWidget()
        communication = QVBoxLayout(self.communication)
        communication.setContentsMargins(0, 0, 0, 0)
        communication.setSpacing(4)
        heading = QHBoxLayout()
        self.comm_toggle = button("v Session communication")
        self.comm_toggle.setObjectName("section")
        self.agent_toggle = QCheckBox("Agent communication")
        self.agent_toggle.toggled.connect(lambda value: self.rpc("toggle", enabled=value))
        heading.addWidget(self.comm_toggle, 1)
        heading.addWidget(self.agent_toggle)
        communication.addLayout(heading)
        self.comm_body = QWidget()
        body = QVBoxLayout(self.comm_body)
        body.setSpacing(4)
        body.setContentsMargins(0, 0, 0, 0)
        connect = QHBoxLayout()
        self.source, self.target = QComboBox(), QComboBox()
        connect.addWidget(QLabel("Connect"))
        connect.addWidget(self.source, 1)
        connect.addWidget(QLabel("↔"))
        connect.addWidget(self.target, 1)
        connect.addWidget(button("+ Add pair", self.add_pair))
        body.addLayout(connect)
        pairs = QHBoxLayout()
        self.pairs = QComboBox()
        pairs.addWidget(self.pairs, 1)
        pairs.addWidget(button("Remove pair", self.remove_pair))
        body.addLayout(pairs)
        self.messages = table(["Time", "From → To", "Message", "State"])
        self.messages.setColumnWidth(0, 65)
        self.messages.setColumnWidth(1, 200)
        self.messages.setColumnWidth(2, 410)
        self.messages.setMinimumHeight(100)
        self.messages.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.messages.customContextMenuRequested.connect(self.message_menu)
        body.addWidget(self.messages, 1)
        body.addWidget(button("Delete selected messages…", self.delete_messages))
        compose = QHBoxLayout()
        self.recipient = QComboBox()
        self.recipient.setMinimumWidth(140)
        self.composer = QLineEdit()
        self.composer.setPlaceholderText("Send an operator message…")
        self.composer.setMaxLength(16000)
        self.composer.returnPressed.connect(self.send)
        compose.addWidget(QLabel("To:"))
        compose.addWidget(self.recipient)
        compose.addWidget(self.composer, 1)
        compose.addWidget(button("Send", self.send, True))
        body.addLayout(compose)
        note = QLabel("Operator messages always available · Configure Claude live alerts in the supervisor menu")
        note.setWordWrap(True)
        body.addWidget(note)
        communication.addWidget(self.comm_body)
        self.comm_toggle.clicked.connect(lambda: self.collapse(self.comm_body, self.comm_toggle, "Session communication"))
        self.splitter.addWidget(self.communication)
        self.splitter.setChildrenCollapsible(False)
        outer.addWidget(self.splitter, 1)
        footer = QHBoxLayout()
        self.status = QLabel("Broker disconnected" if not client else "Local broker connected")
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        footer.addWidget(QSizeGrip(self))
        outer.addLayout(footer)
        self.default_size()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(2500)
        action = QAction("Exit", self)
        action.setShortcut("Alt+F4")
        action.triggered.connect(self.close)
        self.addAction(action)
        if client:
            self.rpc("ui_state", callback=self.restore_ui)
            self.poll()
            self.refresh()
        for child in self.findChildren(QWidget):
            child.setMouseTracking(True)
        QApplication.instance().installEventFilter(self)

    def job(self, function, callback):
        # Keep the signal owner alive until the queued UI callback has run.
        # QRunnable auto-deletion can otherwise discard a fast worker's result.
        def finished(result, error):
            try:
                callback(result, error)
            finally:
                self.jobs.discard(job)
        job = Job(function, finished)
        self.jobs.add(job)
        self.pool.start(job)

    def rpc(self, action, callback=None, **params):
        if not self.client:
            self.status.setText("Broker disconnected. Use the menu to start / reconnect.")
            return
        client = self.client
        def finished(result, error):
            if error:
                QMessageBox.warning(self, "VISOR", error)
            else:
                if callback:
                    callback(result)
                self.poll()
        self.job(lambda: client.call(action, **params), finished)

    def connect_broker(self):
        def connected(client, error):
            if error:
                self.status.setText(error)
                return
            self.client = client
            self.rpc("ui_state", callback=self.restore_ui)
            self.poll()
            self.refresh()
        self.job(lambda: ensure_broker(self.root), connected)

    def poll(self):
        if self.polling:
            self.poll_pending = True
            return
        if not self.client or QApplication.activePopupWidget() is not None:
            return
        self.polling = True
        def finished(snapshot, error):
            self.polling = False
            if self.poll_pending:
                self.poll_pending = False
                QTimer.singleShot(0, self.poll)
            # Rebuilding rows destroys their menus. A snapshot requested before a
            # popup opened must also wait for the next poll after it closes.
            if QApplication.activePopupWidget() is not None:
                return
            if error:
                self.status.setText("Broker disconnected · " + error)
                return
            self.snapshot = snapshot
            self.filter.setToolTip(snapshot.get("provider_warnings", {}).get("claude", ""))
            self.status.setText(snapshot.get("delivery_error") or snapshot.get("refresh_error") or snapshot.get("receipt_error") or
                                ("Loading Codex sessions…" if self.refreshing else "Local broker connected · Independent terminal windows"))
            self.agent_toggle.blockSignals(True)
            self.agent_toggle.setChecked(snapshot["agent_enabled"])
            self.agent_toggle.blockSignals(False)
            self.render_sessions()
            self.render_messages()
            self.populate_combos()
            self.shutdown_requested = bool(snapshot.get("shutdown_targets"))
            if self.shutdown_requested:
                managed = [s for s in snapshot["sessions"] if s["id"] in snapshot["shutdown_targets"]]
                confirmed = sum(s["shutdown"].startswith("confirmed") for s in managed)
                progress = snapshot.get("wrap_up_progress", {})
                exceeded = sum(p["state"] == "wait exceeded" for p in progress.values())
                self.status.setText(f"Wrap-up: {confirmed}/{len(managed)} confirmed · {exceeded} wait limits exceeded. Background work needs review. Close again for details.")
        self.job(lambda: self.client.call("snapshot"), finished)

    def collapse(self, widget, toggle, title):
        widget.setVisible(not widget.isVisible())
        toggle.setText(("v " if widget.isVisible() else "> ") + title)

    def render_sessions(self, *args):
        selected = {self.sessions.item(i.row(), 0).data(Qt.ItemDataRole.UserRole)
                    for i in self.sessions.selectionModel().selectedRows()}
        query, provider = self.search.text().casefold(), self.filter.currentText()
        sessions = self.snapshot["sessions"]
        visible = [s for s in sessions if query in (s["name"] + " " + s["cwd"] + " " + s["native_id"]).casefold()
                   and (provider == "All providers" or s["provider"] == provider)
                   and (not self.open_only.isChecked() or self.is_open(s))]
        self.sessions.setSortingEnabled(False)
        self.sessions.clearContents()
        self.sessions.setRowCount(len(visible))
        for row, session in enumerate(visible):
            key = session["id"]
            unattached = self.is_open(session) and session["provider"] == "codex" and session["status"] == "disconnected"
            status = "Not attached" if unattached else session["status"]
            latest = "Terminal open; check startup prompts or shared-server connection. Messages wait for attachment." if unattached else session["latest"]
            external = self.snapshot.get("external_focus", {}).get(key)
            if external:
                latest += " · External window linked for Focus; check its selected tab."
            for column, text in enumerate([session["name"], session["provider"], status, latest]):
                item = SessionItem(str(text).replace("\n", " "))
                item.setToolTip(f"{session['cwd']}\n{key}" if column == 0 else str(text))
                item.setData(Qt.ItemDataRole.UserRole, key)
                if column == 2:
                    item.setForeground(QColor("#f2bc48" if text == "waiting" else "#00d2d8" if text == "working" else "#a9bccd"))
                self.sessions.setItem(row, column, item)
            used = session.get("last_used")
            try:
                stamp = float(used) if used is not None else 0
                date = time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp)) if stamp > 0 else "—"
                tooltip = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(stamp)) if stamp > 0 else "Last use is not reported yet"
            except (ValueError, TypeError, OverflowError, OSError):
                stamp, date, tooltip = 0, "—", "Last use is unavailable"
            item = SessionItem(date, stamp)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(tooltip)
            self.sessions.setItem(row, 4, item)
            actions = QWidget()
            layout = QHBoxLayout(actions)
            layout.setContentsMargins(1, 1, 1, 1)
            layout.setSpacing(3)
            active = self.is_open(session) or bool(external)
            main = button("Focus" if active else "Resume", lambda checked=False, k=key, a=active: self.focus_or_resume(k, a))
            main.setToolTip("Focus linked external window; verify its selected tab" if external else
                            "Verify and focus managed terminal" if active else "Resume exact saved session")
            layout.addWidget(main)
            message = button("Msg", lambda checked=False, k=key: self.select_recipient(k))
            message.setAccessibleName("Message session")
            layout.addWidget(message)
            more = button("...")
            menu = QMenu(more)
            menu.addAction("Rename…", lambda k=key: self.rename(k))
            menu.addAction("Copy session ID", lambda sid=session["native_id"]: QApplication.clipboard().setText(sid))
            if external:
                menu.addAction("Unlink external Focus window…", lambda k=key: self.unlink_external(k))
            elif not self.is_open(session):
                menu.addAction("Link external terminal for Focus…", lambda k=key: self.link_external(k))
            if session["provider"] == "codex":
                menu.addAction("Check peer tools", lambda k=key: self.peer_tool_status(k))
            participation = menu.addAction("Participates in agent communication")
            participation.setCheckable(True)
            participation.setChecked(bool(session["participates"]))
            participation.triggered.connect(lambda value, k=key: self.rpc("participate", session=k, enabled=value))
            menu.addAction("Wrap up", lambda k=key: self.rpc("wrap_up", sessions=[k], request_id=uuid.uuid4().hex))
            menu.addAction("Wrap-up wait time…", lambda k=key: self.wrap_up_wait(k))
            menu.addAction("Reconcile closed terminal…", lambda k=key: self.reconcile(k))
            menu.addSeparator()
            menu.addAction("Delete session…", lambda k=key: self.delete_session(k))
            more.setMenu(menu)
            layout.addWidget(more)
            item = SessionItem("Focus" if active else "Resume")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.sessions.setItem(row, 5, item)
            self.sessions.setCellWidget(row, 5, actions)
        self.sessions.setSortingEnabled(True)
        for row in range(self.sessions.rowCount()):
            if self.sessions.item(row, 0).data(Qt.ItemDataRole.UserRole) in selected:
                for col in range(self.sessions.columnCount()):
                    self.sessions.item(row, col).setSelected(True)
        self.counts.setText(f"{len(visible)} of {len(sessions)} sessions shown    {sum(self.is_open(s) for s in sessions)} VISOR terminals    "
                            f"{sum(s['status'] == 'working' for s in visible)} working shown    "
                            f"{sum(s['status'] == 'waiting' for s in visible)} need you shown")
        self.counts.setToolTip("VISOR terminals have a current helper connection. Working and waiting counts apply to the displayed rows and use the provider's reported status.")
        self.session_hint.setText(
            "No sessions match these filters. VISOR terminals only hides independent terminals, even if they are working. Click Show all to restore the list."
            if self.open_only.isChecked() else "No sessions match these filters. Click Show all to restore the list.")
        self.session_hint.setVisible(bool(sessions) and not visible)

    def clear_session_filters(self):
        for widget in (self.search, self.filter, self.open_only):
            widget.blockSignals(True)
        self.search.clear()
        self.filter.setCurrentText("All providers")
        self.open_only.setChecked(False)
        for widget in (self.search, self.filter, self.open_only):
            widget.blockSignals(False)
        self.render_sessions()

    def wrap_up_wait(self, key):
        def received(current):
            seconds, accepted = QInputDialog.getInt(self, "Wrap-up wait time",
                "Seconds before reporting that this session's wait time was exceeded.\n"
                "This does not stop work or release held messages. Applies to the next request.",
                current, 30, 86400, 30)
            if accepted:
                self.rpc("wrap_up_timeout", session=key, seconds=seconds)
        self.rpc("wrap_up_timeout", session=key, callback=received)

    def peer_tool_status(self, key):
        def received(result):
            QMessageBox.information(self, "Codex peer tools",
                "Advertised: " + (", ".join(result["advertised"]) or "none") +
                "\nMissing: " + (", ".join(result["missing"]) or "none") +
                "\nRuntime: " + ", ".join(result["runtime"]) + "\n\n" + result["note"])
        self.rpc("peer_tool_status", session=key, callback=received)

    def link_external(self, key):
        def received(items):
            if not items:
                QMessageBox.information(self, "External terminal", "No supported terminal windows are available.")
                return
            labels = [f"{index + 1}. {item['title'][:160]} (window {item['identity'].get('hwnd', item['identity'].get('handle'))}, PID {item['identity']['pid']})"
                      for index, item in enumerate(items)]
            selected, accepted = QInputDialog.getItem(self, "Link external terminal for Focus",
                "Choose the window you use for this session. Focus selects the window; check its active tab.",
                labels, 0, False)
            if not accepted:
                return
            item = items[labels.index(selected)]
            if QMessageBox.question(self, "Confirm external Focus link",
                "Link the selected window for Focus?\n\n"
                "You identify which session it contains. This link grants window focus only. "
                "Arrangement and terminal closure still require managed ownership.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
                self.rpc("external_focus_link", session=key, identity=item["identity"], confirmed=True)
        self.rpc("external_window_candidates", callback=received)

    def unlink_external(self, key):
        if QMessageBox.question(self, "Unlink external window", "Remove the Focus link? The terminal stays open.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
            self.rpc("external_focus_unlink", session=key, confirmed=True)

    @staticmethod
    def is_open(session):
        return bool(session.get("heartbeat") and time.time() - session["heartbeat"] < 20)

    def populate_combos(self):
        names = {s["id"]: s["name"] for s in self.snapshot["sessions"]}
        for combo in (self.source, self.target, self.recipient):
            current = combo.currentData()
            combo.clear()
            for key, name in names.items():
                combo.addItem(name, key)
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
        current = self.pairs.currentData()
        self.pairs.clear()
        for a, b in self.snapshot["pairs"]:
            self.pairs.addItem(f"{names.get(a,a)} ↔ {names.get(b,b)}", [a, b])
        index = self.pairs.findData(current)
        if index >= 0:
            self.pairs.setCurrentIndex(index)

    def render_messages(self):
        self.messages.setRowCount(len(self.snapshot["messages"]))
        names = {s["id"]: s["name"] for s in self.snapshot["sessions"]}
        for row, message in enumerate(self.snapshot["messages"]):
            values = [time.strftime("%H:%M", time.localtime(message["created"])),
                      f"{names.get(message['sender'], message['sender'])} → {names.get(message['recipient'], message['recipient'])}",
                      message["body"], message["state"]]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value.replace("\n", " "))
                item.setToolTip(value + ("\n" + message["detail"] if column == 3 else ""))
                item.setData(Qt.ItemDataRole.UserRole, message["id"])
                self.messages.setItem(row, column, item)

    def message_menu(self, point):
        item = self.messages.itemAt(point)
        if not item:
            return
        message = next(m for m in self.snapshot["messages"] if m["id"] == item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        menu.addAction("View message", lambda: QMessageBox.information(self, "Message", message["body"] + "\n\n" + message["detail"]))
        if message["state"] == "held":
            menu.addAction("Release held message", lambda: self.rpc("release", message=message["id"]))
        menu.addAction("Delete message…", lambda: self.delete_messages([message["id"]]))
        menu.exec(self.messages.viewport().mapToGlobal(point))

    def select_recipient(self, key):
        self.comm_body.show()
        self.comm_toggle.setText("v Session communication")
        self.recipient.setCurrentIndex(self.recipient.findData(key))
        self.composer.setFocus()

    def send(self):
        if self.recipient.currentData() and self.composer.text().strip():
            body = self.composer.text()
            def sent(result):
                if self.composer.text() == body:
                    self.composer.clear()
            self.rpc("send", recipient=self.recipient.currentData(), body=body, request_id=uuid.uuid4().hex, callback=sent)

    def add_pair(self):
        if self.source.currentData() and self.target.currentData():
            self.rpc("pair", a=self.source.currentData(), b=self.target.currentData())

    def remove_pair(self):
        pair = self.pairs.currentData()
        if pair:
            self.rpc("pair", a=pair[0], b=pair[1], enabled=False)

    def refresh(self):
        if not self.client:
            self.connect_broker()
            return
        if self.refreshing:
            return
        self.refreshing = True
        self.status.setText("Reading Codex and Claude session metadata…")
        client = self.client
        def finished(result, error):
            self.refreshing = False
            if error:
                self.status.setText("Session discovery failed · " + error)
            self.poll()
        self.job(lambda: client.call("refresh"), finished)

    def new_session(self):
        provider, ok = QInputDialog.getItem(self, "New session", "Provider", ["codex", "claude"], 0, False)
        if not ok:
            return
        cwd = QFileDialog.getExistingDirectory(self, "Choose project directory")
        if cwd:
            self.rpc("new", provider=provider, cwd=cwd, name=Path(cwd).name)

    def claude_settings(self):
        self.rpc("claude_options", callback=self.edit_claude_settings)

    def edit_claude_settings(self, options):
        dialog = QDialog(self)
        dialog.setWindowTitle("Claude Code messaging")
        layout = QFormLayout(dialog)
        note = QLabel("Applies when a Claude session is next opened. Inbox mode requires Claude to check messages. "
                      "Local preview enables live alerts and asks for confirmation in Claude. "
                      "Approved plugin mode requires VISOR's plugin to be installed and allowed by your organization. "
                      "Claude's tool approval rules still apply.")
        note.setWordWrap(True)
        note.setMaximumWidth(520)
        layout.addRow(note)
        mode = QComboBox()
        for label, value in (("Inbox only", "inbox"), ("Local preview channel", "development"), ("Approved plugin", "plugin")):
            mode.addItem(label, value)
        mode.setCurrentIndex(max(0, mode.findData(options.get("mode", "inbox"))))
        plugin = QLineEdit(options.get("plugin", ""))
        plugin.setPlaceholderText("plugin:visor@your-marketplace")
        plugin.setEnabled(mode.currentData() == "plugin")
        mode.currentIndexChanged.connect(lambda: plugin.setEnabled(mode.currentData() == "plugin"))
        layout.addRow("Message delivery", mode)
        layout.addRow("Approved plugin", plugin)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        layout.addRow(controls)
        if dialog.exec():
            self.rpc("claude_options", value={"mode": mode.currentData(), "plugin": plugin.text().strip()})

    def register(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Register exact saved session")
        layout = QFormLayout(dialog)
        provider = QComboBox()
        provider.addItems(["codex", "claude"])
        native_id, name, cwd = QLineEdit(), QLineEdit(), QLineEdit()
        layout.addRow("Provider", provider)
        layout.addRow("Session UUID", native_id)
        layout.addRow("Display name", name)
        layout.addRow("Project directory", cwd)
        layout.addRow(button("Choose directory…", lambda: cwd.setText(QFileDialog.getExistingDirectory(dialog))))
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        layout.addRow(controls)
        if dialog.exec():
            self.rpc("register", provider=provider.currentText(), native_id=native_id.text(), name=name.text() or native_id.text(), cwd=cwd.text())

    def rename(self, key):
        name, ok = QInputDialog.getText(self, "Rename session", "Display name")
        if ok:
            self.rpc("rename", session=key, name=name)

    def delete_session(self, key):
        session = next((s for s in self.snapshot["sessions"] if s["id"] == key), None)
        if not session:
            return
        question = QMessageBox(self)
        question.setWindowTitle("Permanently delete session")
        question.setText(f"Delete {session['name']} from VISOR and {session['provider']}?")
        question.setInformativeText("Its linked terminal will close immediately without Wrap up. Running work may be lost. "
                                    "The saved provider conversation, VISOR messages and connections will be permanently deleted. "
                                    "Codex also deletes spawned descendant conversations. Project source files are preserved. "
                                    "This cannot be undone.")
        remove = question.addButton("Close terminal and delete", QMessageBox.ButtonRole.DestructiveRole)
        cancel = question.addButton(QMessageBox.StandardButton.Cancel)
        question.setDefaultButton(cancel)
        question.exec()
        if question.clickedButton() == remove:
            self.rpc("delete_session", session=key, confirmed=True)

    def focus_session_item(self, item):
        key = item.data(Qt.ItemDataRole.UserRole)
        if key:
            self.focus_or_resume(key, True)

    def focus_or_resume(self, key, active):
        if active:
            self.rpc("focus_target", session=key, callback=self.activate_terminal)
        elif QMessageBox.question(self, "Resume exact session", "Confirm this session is not already running in another terminal. Resume it in a new window?") == QMessageBox.StandardButton.Yes:
            self.rpc("resume", session=key)

    def activate_terminal(self, target):
        try:
            # The clicked supervisor owns foreground permission; its background broker
            # does not. Revalidate the exact HWND/process here, then focus on the UI thread.
            focus_verified(target)
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Terminal focus", str(exc))

    def reconcile(self, key):
        if QMessageBox.question(self, "Reconcile terminal ownership", "Confirm the previously managed terminal is closed and no worker for this session is running. Clear its stale window association?") == QMessageBox.StandardButton.Yes:
            self.rpc("forget_closed_window", session=key, confirmed_closed=True)

    def arrange(self, action, **extra):
        screens = QApplication.screens()
        screen = next((s for s in screens if s.name() == self.monitors.currentText()), self.screen())
        area = screen.availableGeometry()
        keys = [self.sessions.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                for index in self.sessions.selectionModel().selectedRows()]
        self.rpc(action, sessions=keys, area=asdict(Rect(area.x(), area.y(), area.width(), area.height())),
                 monitor=screen.name(), **extra)

    def delete_messages(self, ids=None):
        if not isinstance(ids, list):
            ids = [self.messages.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                   for index in self.messages.selectionModel().selectedRows()]
        if not ids:
            return
        if QMessageBox.question(self, "Delete messages",
                                f"Delete {len(ids)} selected message(s) from Visor? "
                                "Queued messages will be cancelled. Anything already sent "
                                "cannot be retracted from the receiving session.") == QMessageBox.StandardButton.Yes:
            self.rpc("delete_messages", messages=ids)

    def clear_history(self):
        if QMessageBox.question(self, "Clear message text", "Clear completed and failed message text? Pending and uncertain messages, and deduplication records, will remain.") == QMessageBox.StandardButton.Yes:
            self.rpc("clear_history")

    def stop_broker(self):
        if QMessageBox.question(self, "Stop broker", "Stop background delivery? Terminal sessions will continue. Operator sends need a broker restart.") == QMessageBox.StandardButton.Yes:
            self.rpc("stop", callback=lambda result: self.status.setText("Broker stopped · Terminal sessions continue"))

    def default_size(self):
        self.showNormal()
        screen = self.screen() or QApplication.primaryScreen()
        area = screen.availableGeometry()
        self.resize(min(1080, area.width()), min(760, area.height()))
        self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)),
                  max(area.top(), min(self.y(), area.bottom() - self.height() + 1)))

    def restore_ui(self, value):
        if not value:
            return
        area = self.screen().availableGeometry()
        requested = Rect(*value.get("geometry", [area.x(), area.y(), 1080, 760]))
        screen = next((s for s in QApplication.screens() if s.availableGeometry().contains(requested.x, requested.y)), self.screen())
        g = screen.availableGeometry()
        rect = requested.clamped(Rect(g.x(), g.y(), g.width(), g.height()))
        self.setGeometry(rect.x, rect.y, rect.width, rect.height)
        widths = value.get("columns", [])[:6]
        if len(widths) == 5:
            widths.insert(4, 145)  # Existing settings predate Last used.
        for index, width in enumerate(widths):
            self.sessions.setColumnWidth(index, width)
        column = value.get("sort_column", 4)
        if isinstance(column, int) and 0 <= column < self.sessions.columnCount():
            order = Qt.SortOrder.DescendingOrder if value.get("sort_descending", True) else Qt.SortOrder.AscendingOrder
            self.sessions.sortItems(column, order)
        self.splitter.setSizes(value.get("splitter", [400, 260]))
        self.arrange_box.setVisible(value.get("arrange", False))
        self.arrange_toggle.setText(("v " if value.get("arrange") else "> ") + "Arrange windows")
        self.comm_body.setVisible(value.get("communication", True))
        self.comm_toggle.setText(("v " if value.get("communication", True) else "> ") + "Session communication")

    def closeEvent(self, event):
        if self.allow_close:
            event.accept()
            return
        event.ignore()
        question = QMessageBox(self)
        question.setWindowTitle("Close supervisor")
        question.setText("Choose what happens to managed sessions.")
        question.setInformativeText("Leave running keeps the broker and approved communication active. Graceful shutdown holds agent messages and requests wrap-up; terminals remain open. No process is force-killed.")
        leave = question.addButton("Leave running and close", QMessageBox.ButtonRole.AcceptRole)
        graceful = None if self.shutdown_requested else question.addButton("Request graceful shutdown", QMessageBox.ButtonRole.ActionRole)
        finish = None
        if self.shutdown_requested:
            managed = [s for s in self.snapshot["sessions"] if s["id"] in self.snapshot.get("shutdown_targets", [])]
            pending = [s for s in managed if not s["shutdown"].startswith("confirmed")]
            progress = self.snapshot.get("wrap_up_progress", {})
            lines = []
            for session in managed:
                item = progress.get(session["id"], {})
                lines.append(f"{session['name']}: {session['shutdown'] or 'not confirmed'}; "
                    f"{item.get('state', 'waiting')}, {item.get('elapsed_seconds', 0)} seconds; "
                    f"message {item.get('message_state', 'unknown')}; "
                    f"observed workers unresolved: {item.get('observed_workers_unresolved', 'unknown')}; "
                    f"reported background/scheduled: {item.get('reported_background')}/{item.get('reported_scheduled')}")
            summary = "\n".join(lines)
            question.setDetailedText(summary or "No managed sessions")
            question.setInformativeText("Wrap-up requests already sent cannot be retracted. Agent messages remain held. Inspect each terminal for remaining background work. You can keep waiting or close with remaining sessions running.")
            leave.setText("Close with remaining sessions running" if pending else "Close; keep messages held")
            if not pending:
                finish = leave
        cancel = question.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        question.exec()
        if graceful is not None and question.clickedButton() == graceful:
            self.rpc("wrap_up", request_id=uuid.uuid4().hex,
                     callback=lambda result: setattr(self, "shutdown_requested", True))
        elif question.clickedButton() == leave or (finish is not None and question.clickedButton() == finish):
            self.save_and_close()
        elif question.clickedButton() == cancel and self.shutdown_requested:
            self.rpc("cancel_shutdown_tracking")

    def save_and_close(self):
        value = {"geometry": [self.x(), self.y(), self.width(), self.height()],
                 "columns": [self.sessions.columnWidth(i) for i in range(self.sessions.columnCount())],
                 "sort_column": self.sessions.horizontalHeader().sortIndicatorSection(),
                 "sort_descending": self.sessions.horizontalHeader().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder,
                 "splitter": self.splitter.sizes(), "arrange": self.arrange_box.isVisible(),
                 "communication": self.comm_body.isVisible()}
        def done(result, error):
            self.allow_close = True
            self.close()
        if self.client:
            self.job(lambda: self.client.call("ui_state", value=value), done)
        else:
            done(None, None)

    def resize_edges(self, pos):
        edges = Qt.Edge(0)
        if not self.rect().contains(pos) or self.isMaximized() or self.isFullScreen():
            return edges
        if pos.x() < 7:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - 7:
            edges |= Qt.Edge.RightEdge
        if pos.y() < 7:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - 7:
            edges |= Qt.Edge.BottomEdge
        return edges

    def update_resize_cursor(self, widget=None, pos=None):
        edges = self.resize_edges(pos) if pos is not None else Qt.Edge(0)
        shape = {Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
                 Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
                 Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
                 Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
                 Qt.Edge.LeftEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeFDiagCursor,
                 Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
                 Qt.Edge.RightEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeBDiagCursor,
                 Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeBDiagCursor}.get(edges)
        target = widget if shape is not None else None
        previous = self._resize_cursor_target
        if previous is not target:
            if previous is not None:
                if self._resize_cursor_explicit:
                    previous.setCursor(self._resize_cursor_previous)
                else:
                    previous.unsetCursor()
            self._resize_cursor_target = target
            if target is not None:
                self._resize_cursor_explicit = target.testAttribute(Qt.WidgetAttribute.WA_SetCursor)
                self._resize_cursor_previous = target.cursor()
        if target is not None:
            target.setCursor(shape)

    def eventFilter(self, watched, event):
        own_widget = isinstance(watched, QWidget) and QWidget.window(watched) is self
        if own_widget and event.type() == QEvent.Type.Show:
            watched.setMouseTracking(True)
        if own_widget and event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            pos = self.mapFromGlobal(event.globalPosition().toPoint())
            self.update_resize_cursor(self.childAt(pos) or self, pos)
        elif event.type() == QEvent.Type.Leave and watched is self._resize_cursor_target or watched is self and event.type() == QEvent.Type.WindowStateChange:
            self.update_resize_cursor()
        if (event.type() == QEvent.Type.MouseButtonPress
                and isinstance(watched, QWidget) and QWidget.window(watched) is self
                and event.button() == Qt.MouseButton.LeftButton and self.windowHandle()):
            edges = self.resize_edges(self.mapFromGlobal(event.globalPosition().toPoint()))
            if edges and self.windowHandle().startSystemResize(edges):
                return True
        return super().eventFilter(watched, event)

    def nativeEvent(self, event_type, message):
        if sys.platform == "win32" and event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x83 and msg.wParam:  # WM_NCCALCSIZE: keep custom full-client frame.
                return True, 0
            if msg.message == 0x84:  # WM_NCHITTEST: let Windows own border drag/cursors.
                edges = self.resize_edges(self.mapFromGlobal(QCursor.pos()))
                hit = {Qt.Edge.LeftEdge: 10, Qt.Edge.RightEdge: 11,
                       Qt.Edge.TopEdge: 12, Qt.Edge.BottomEdge: 15,
                       Qt.Edge.LeftEdge | Qt.Edge.TopEdge: 13,
                       Qt.Edge.RightEdge | Qt.Edge.TopEdge: 14,
                       Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: 16,
                       Qt.Edge.RightEdge | Qt.Edge.BottomEdge: 17}.get(edges)
                if hit:
                    return True, hit
        return super().nativeEvent(event_type, message)

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32" and QApplication.platformName() == "windows":
            try:
                enable_window_resize(int(self.winId()))
            except OSError as exc:
                self.status.setText(str(exc))


def launch(root, smoke=False, discover=False):
    if smoke:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication(sys.argv[:1])
    if sys.platform == "win32":
        import os
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
        if smoke and font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
        app.setFont(QFont("Segoe UI", 10))
    app.setApplicationName("VISOR")
    app.setStyleSheet(STYLE)
    instance = None
    if not smoke:
        from .instance import SupervisorInstance
        instance = SupervisorInstance(root)
        try:
            if not instance.start():
                return
        except (OSError, RuntimeError) as exc:
            QMessageBox.warning(None, "VISOR already running", str(exc))
            return
    window = Window(root)
    if instance:
        def activate():
            if window.isMinimized():
                window.showNormal()
            window.show()
            window.raise_()
            window.activateWindow()
            QApplication.alert(window)
        instance.activated.connect(activate)
    window.show()
    if smoke:
        window.resize(1080, 760)
        deadline = time.monotonic() + 45
        if discover:
            window.connect_broker()
        def finish():
            if discover:
                if not window.client or window.refreshing or window.polling or "agent_enabled" not in window.snapshot:
                    if time.monotonic() < deadline:
                        QTimer.singleShot(100, finish)
                        return
                    print("Session loading timed out: " + window.status.text(), flush=True)
                    app.exit(1)
                    return
                if window.snapshot.get("refresh_error"):
                    print("Session discovery failed: " + window.snapshot["refresh_error"], flush=True)
                    app.exit(1)
                    return
                print(f"Loaded {window.sessions.rowCount()} session rows and {window.recipient.count()} message recipients", flush=True)
            if not window.grab().save(str(root / "ui-smoke.png")):
                app.exit(1)
                return
            window.allow_close = True
            window.close()
        QTimer.singleShot(300, finish)
    else:
        window.connect_broker()
    try:
        result = app.exec()
    finally:
        if instance:
            instance.close()
    if result:
        raise SystemExit(result)
