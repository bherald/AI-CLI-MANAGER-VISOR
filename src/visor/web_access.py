"""Explicit, optional browser access to this operator's VISOR and Prism."""
from __future__ import annotations

import ipaddress

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
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

from .prism_web import PrismWebServer


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
    """Start only after an explicit operator action; never reuse a saved token."""
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
        "A new private link will be created. Your browser will stay closed.",
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
    manager.supervisor.status.setText("Web access is on. Open Web access… for the new private link.")


class WebAccessDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.workspace = manager.ensure_workspace()
        self.setWindowTitle("Web access")
        self.resize(610, 300)
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
            self.status.setText("On · Private operator access. Stop revokes this link.")
        else:
            self.private_link.clear()
            self.status.setText("Off · Start when you need browser access.")

    def start_access(self):
        if self.workspace.web_server is not None:
            self.refresh()
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
