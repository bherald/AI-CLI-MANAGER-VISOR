"""Explicit, optional browser access to this operator's VISOR and Prism."""
from __future__ import annotations

import ipaddress
import secrets

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .prism_web import PrismWebServer, read_web_access_settings, save_web_access_settings


def validated_bind_host(host):
    if not isinstance(host, str):
        raise ValueError("Choose a specific local IP")  # noqa: TRY004 -- invalid saved preferences are values to reject
    host = host.strip()
    address = ipaddress.ip_address("127.0.0.1" if host == "localhost" else host)
    mapped = getattr(address, "ipv4_mapped", None)
    if (address.is_unspecified or address.is_multicast
            or mapped is not None and (mapped.is_unspecified or mapped.is_multicast)):
        raise ValueError("Choose a specific local IP; wildcard and multicast addresses are unsupported")
    return str(address)


def start_web_access(manager, host, port):
    """Start only after an explicit operator action; use the saved browser mode."""
    workspace = manager.ensure_workspace()
    if workspace.web_server is not None:
        return workspace.web_server
    server = None
    try:
        host = validated_bind_host(host)
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Port must be between zero and 65535")
        server = PrismWebServer(manager.supervisor.root, host=host, port=port)
        server.start()
    except (OSError, RuntimeError, ValueError):
        if getattr(workspace, "web_was_active", False):
            workspace.web_was_active = False
            workspace.save_preferences()
        if server is not None:
            server.close()
        raise
    workspace.web_server = server
    workspace.web_host = QUrl(server.origin).host()
    workspace.web_port = QUrl(server.origin).port()
    workspace.web_was_active = True
    workspace.save_preferences()
    return server


def restore_web_access(manager, parent=None):
    """Offer a prior GUI listener once; the launcher owns invocation timing."""
    workspace = manager.ensure_workspace()
    if workspace.web_server is not None or not workspace.web_was_active:
        return
    host, port = workspace.web_host, workspace.web_port
    address = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    accepted = QMessageBox.question(parent, "Start web access again?",
        f"Web access was on when VISOR last closed. Start it again on {address}?\n\n"
        "Your saved browser token mode will be used. Your browser will stay closed.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
    if accepted != QMessageBox.StandardButton.Yes:
        workspace.web_was_active = False
        workspace.save_preferences()
        manager.supervisor.status.setText("Web access is off. Use Web access… to start it when needed.")
        return
    try:
        start_web_access(manager, host, port)
    except (OSError, RuntimeError, ValueError) as exc:
        manager.supervisor.status.setText("Web access is off; could not start again: " + str(exc))
        return
    kind = "saved" if read_web_access_settings(manager.supervisor.root)["mode"] == "saved_static" else "new"
    manager.supervisor.status.setText(f"Web access is on. Open Web access… for the {kind} private link.")


class WebAccessDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.workspace = manager.ensure_workspace()
        self.setWindowTitle("Web access")
        self.resize(650, 410)
        layout = QVBoxLayout(self)
        note = QLabel("Optional access to VISOR sessions and Prism in your browser. "
                      "Loopback is local to this computer. For another device, choose a specific LAN/VPN IP. "
                      "HTTP is unencrypted; use your trusted network or VPN. Keep the private link private.")
        note.setWordWrap(True)
        layout.addWidget(note)
        fields = QFormLayout()
        self.bind_ip = QLineEdit("127.0.0.1")
        self.bind_ip.setAccessibleName("Web access bind IP")
        fields.addRow("Bind IP", self.bind_ip)
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setSpecialValueText("Automatic")
        saved = self.workspace.web_port
        self.port.setValue(saved if type(saved) is int and 0 <= saved <= 65535 else 0)
        self.port.setAccessibleName("Web access port")
        fields.addRow("Port", self.port)
        self.token_mode = QComboBox()
        self.token_mode.setAccessibleName("Browser token mode")
        self.token_mode.addItem("New token each start", "per_start")
        self.token_mode.addItem("Saved static token", "saved_static")
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setAccessibleName("Saved browser token override")
        self.token_edit.setPlaceholderText("Generate a strong token or enter an override")
        self.generate_button = QPushButton("Generate")
        self.generate_button.setAccessibleName("Generate and save browser token")
        self.token_error = False
        try:
            self.token_settings = read_web_access_settings(self.manager.supervisor.root)
        except (OSError, ValueError):
            self.token_settings = {"mode": "per_start", "token": None}
            self.token_error = True
        self.token_mode.setCurrentIndex(self.token_mode.findData(self.token_settings["mode"]))
        self.token_edit.setText(self.token_settings["token"] or "")
        self.token_edit.setEnabled(self.token_settings["mode"] == "saved_static")
        self.generate_button.setEnabled(self.token_settings["mode"] == "saved_static")
        fields.addRow("Browser token", self.token_mode)
        token_row = QHBoxLayout()
        token_row.addWidget(self.token_edit, 1)
        token_row.addWidget(self.generate_button)
        fields.addRow("Static token", token_row)
        self.token_mode.currentIndexChanged.connect(self.change_token_mode)
        self.token_edit.editingFinished.connect(self.save_token)
        self.generate_button.clicked.connect(self.generate_token)
        layout.addLayout(fields)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        controls = QHBoxLayout()
        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self.start_access)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_access)
        self.open_visor = QPushButton("Open VISOR")
        self.open_visor.clicked.connect(lambda: self.open_page("visor"))
        self.open_prism = QPushButton("Open Prism")
        self.open_prism.clicked.connect(lambda: self.open_page("prism"))
        for control in (self.start_button, self.stop_button, self.open_visor, self.open_prism):
            controls.addWidget(control)
        layout.addLayout(controls)
        link_row = QHBoxLayout()
        self.private_link = QLineEdit()
        self.private_link.setReadOnly(True)
        self.private_link.setAccessibleName("Private VISOR browser link")
        self.private_link.setPlaceholderText("Start web access to create a private link")
        self.copy_button = QPushButton("Copy link")
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.private_link.text()))
        link_row.addWidget(self.private_link, 1)
        link_row.addWidget(self.copy_button)
        layout.addLayout(link_row)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh()
        if self.token_error:
            self.status.setText("Saved browser token settings could not be read. Choose a mode and save a valid token before starting.")

    def change_token_mode(self, index=None):
        saved = self.token_mode.currentData() == "saved_static"
        self.token_edit.setEnabled(saved)
        self.generate_button.setEnabled(saved)
        if saved and not self.token_edit.text():
            active = self.workspace.web_server
            self.token_edit.setText(active.token if active is not None else secrets.token_urlsafe(32))
        self.save_token()

    def generate_token(self):
        self.token_edit.setText(secrets.token_urlsafe(32))
        self.save_token()

    def save_token(self):
        mode = self.token_mode.currentData()
        token = self.token_edit.text() if mode == "saved_static" else self.token_settings["token"]
        try:
            settings = save_web_access_settings(self.manager.supervisor.root, mode, token)
        except ValueError as exc:
            self.status.setText(str(exc) + ". Current browser token is unchanged.")
            return False
        except OSError:
            self.status.setText("Browser token could not be saved. Current browser token is unchanged; try again.")
            return False
        changed = settings != self.token_settings
        self.token_settings = settings
        self.token_error = False
        server = self.workspace.web_server
        if server is not None and (changed or mode == "saved_static"):
            try:
                server.rotate_browser_token(token if mode == "saved_static" else secrets.token_urlsafe(32),
                                            saved_static=mode == "saved_static")
            except (OSError, ValueError):
                self.status.setText("Browser token was saved, but the active link could not update. Stop and start web access to reload it.")
                return True
        self.refresh()
        self.status.setText("Browser token mode saved privately" + ("; active link updated." if changed and server is not None else "."))
        return True

    def refresh(self):
        server = self.workspace.web_server
        active = server is not None
        self.start_button.setEnabled(not active)
        self.stop_button.setEnabled(active)
        self.open_visor.setEnabled(active)
        self.open_prism.setEnabled(active)
        self.copy_button.setEnabled(active)
        self.bind_ip.setEnabled(not active)
        self.port.setEnabled(not active)
        if active:
            origin = QUrl(server.origin)
            self.bind_ip.setText(origin.host())
            self.port.setValue(origin.port())
            self.private_link.setText(server.origin + "/visor#token=" + server.token)
            suffix = "Saved static token; Generate or override revokes the old token." if self.token_settings["mode"] == "saved_static" else "New token each start."
            self.status.setText("On · Private operator access. " + suffix)
        else:
            self.private_link.clear()
            self.status.setText("Off · Start when you need browser access.")

    def start_access(self):
        if self.workspace.web_server is not None:
            self.refresh()
            return
        if self.token_error:
            self.status.setText("Choose a browser token mode and save valid settings before starting.")
            return
        mode = self.token_mode.currentData()
        changed = mode != self.token_settings["mode"] or mode == "saved_static" and self.token_edit.text() != self.token_settings["token"]
        if changed and not self.save_token():
            return
        try:
            start_web_access(self.manager, self.bind_ip.text(), self.port.value())
        except (OSError, RuntimeError, ValueError) as exc:
            self.status.setText("Web access could not start: " + str(exc))
            return
        self.refresh()

    def stop_access(self):
        server = self.workspace.web_server
        if server is None:
            self.workspace.web_was_active = False
            self.workspace.save_preferences()
            self.refresh()
            return
        try:
            server.close()
        except (OSError, RuntimeError) as exc:
            self.status.setText("Web access could not stop: " + str(exc))
            return
        self.workspace.web_server = None
        self.workspace.web_was_active = False
        self.workspace.save_preferences()
        self.refresh()

    def open_page(self, page):
        server = self.workspace.web_server
        if server is None:
            self.refresh()
            return
        url = server.origin + "/visor#token=" + server.token if page == "visor" else server.url
        if not QDesktopServices.openUrl(QUrl(url)):
            self.status.setText("Your browser could not open. Copy the private link to open it yourself.")
