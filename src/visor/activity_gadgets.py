"""Native Prism gadgets with bounded data and separately laid out intent controls."""
from __future__ import annotations

import copy
import math
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .activity_components import _slider, validate_actions, validate_component, validate_event
from .activity_table_widget import AdvancedTable


def _label(text="", parent=None):
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def _display(value):
    if isinstance(value, float):
        return f"{value:,.5g}"
    return f"{value:,}" if type(value) is int else str(value)


class _IntentButton(QPushButton):
    """Keep long author labels accessible without forcing a huge card width."""

    def __init__(self, label, parent=None):
        super().__init__(parent)
        self.label = label
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.setMinimumWidth(0)
        self.setMinimumHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setText(label.replace("&", "&&"))

    def resizeEvent(self, event):
        label = self.fontMetrics().elidedText(self.label, Qt.TextElideMode.ElideRight,
                                             max(10, self.width() - 24))
        self.setText(label.replace("&", "&&"))
        super().resizeEvent(event)


class DirectActions(QWidget):
    """Reusable direct-intent row, also usable below existing text/image/chart cards.

    The callback receives only {action_id, value}; the board supplies its identity,
    revision, ownership and request deduplication at its existing event boundary.
    """

    def __init__(self, card, policy, parent=None, on_event=None):
        super().__init__(parent)
        self.policy, self.on_event = policy, on_event
        self.card, self.buttons, self.columns = {}, [], 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 2, 0, 0)
        self.grid.setSpacing(5)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.set_card(card)

    def set_card(self, card):
        actions = validate_actions(card, self.policy)
        self.card = copy.deepcopy(card)
        while self.grid.count():
            item = self.grid.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        self.buttons = []
        for action in actions:
            button = _IntentButton(action["label"], self)
            button.setObjectName("prism-action-" + action["id"])
            button.clicked.connect(lambda _checked=False, identity=action["id"]: self._send(identity))
            button.setEnabled(self.on_event is not None)
            if self.on_event is None:
                button.setToolTip(action["label"] + " · Preview only")
            self.buttons.append(button)
        self.columns = 0
        self._arrange()
        self.setVisible(bool(self.buttons))

    def _send(self, identity):
        event = validate_event(self.card, {"action_id": identity, "value": None}, self.policy)
        if self.on_event is not None:
            self.on_event(event)

    def set_policy(self, policy, error=""):
        self.policy = policy
        try:
            validate_actions(self.card, policy)
        except ValueError as exc:
            error = str(exc)
        self.setEnabled(not error)
        self.setToolTip(error)

    def resizeEvent(self, event):
        self._arrange()
        super().resizeEvent(event)

    def _arrange(self):
        columns = max(1, min(len(self.buttons), self.width() // 140))
        if columns == self.columns:
            return
        self.columns = columns
        for index, button in enumerate(self.buttons):
            self.grid.removeWidget(button)
            self.grid.addWidget(button, index // columns, index % columns)
        self.grid.invalidate()
        self.setMinimumHeight(self.grid.minimumSize().height() if self.buttons else 0)
        self.grid.activate()
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None and parent.layout() is not None:
            parent.layout().activate()


class GadgetCanvas(QWidget):
    """Read-only paint surface. No interactive button is drawn on this surface."""

    def __init__(self, card, parent=None):
        super().__init__(parent)
        self.setMinimumSize(120, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self.update)
        self.set_card(card)

    def sizeHint(self):
        return QSize(290, 180)

    def set_card(self, card):
        previous = getattr(self, "card", {})
        self.card = copy.deepcopy(card)
        timer_fields = ("kind", "seconds", "mode", "running")
        if not hasattr(self, "started") or any(previous.get(key) != card.get(key) for key in timer_fields):
            self.started = time.monotonic()
        if card["kind"] == "timer" and card.get("running", False):
            self.clock.start()
        else:
            self.clock.stop()
        text = card.get("text", card.get("label", card.get("detail", "")))
        if card["kind"] in {"gauge", "meter", "progress", "counter", "stat"}:
            text = " ".join(str(item) for item in (card.get("value", card.get("min", 0)),
                                                  card.get("unit", ""), card.get("detail", "")) if item != "")
        elif card["kind"] in {"indicator", "status"}:
            text = card.get("state", "neutral") + ": " + str(text)
        elif card["kind"] == "sparkline":
            values = card["values"]
            text = (f"Latest {_display(values[-1])} {card.get('unit', '')}; {len(values)} samples"
                    if values else "No samples yet")
        self.setAccessibleName(card.get("title", ""))
        self.setAccessibleDescription(str(text))
        self.setToolTip(str(text))
        self.update()

    def _text(self, painter, rectangle, text, size=11, color=None, weight=QFont.Weight.Normal):
        font = QFont(self.font())
        font.setPointSizeF(size)
        font.setWeight(weight)
        painter.setFont(font)
        painter.setPen(color or self.palette().text().color())
        text = painter.fontMetrics().elidedText(str(text), Qt.TextElideMode.ElideRight,
                                               max(1, int(rectangle.width()) - 6))
        painter.drawText(rectangle, Qt.AlignmentFlag.AlignCenter, text)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(6, 5, -6, -5)
        base = self.palette().base().color()
        dark = base.lightness() < 128
        foreground = self.palette().text().color()
        muted = QColor("#acc1cb" if dark else "#526774")
        track = QColor("#2d424e" if dark else "#d8e3e8")
        accent = QColor(self.card.get("color", "#53cdbb" if dark else "#147d75"))
        # Authors can pick accents; keep them visible on the current surface.
        if abs(accent.lightness() - base.lightness()) < 70 or accent.alpha() < 160:
            accent = QColor("#53cdbb" if dark else "#147d75")
        if self.card.get("depth", True):
            gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            gradient.setColorAt(0, base.lighter(125) if dark else base.darker(102))
            gradient.setColorAt(1, base)
            painter.setPen(QPen(track, 1))
            painter.setBrush(gradient)
            painter.drawRoundedRect(rect, 10, 10)
        rect = rect.adjusted(9, 8, -9, -8)
        detail = self.card.get("detail", "")
        body = rect.adjusted(0, 0, 0, -24 if detail else 0)
        kind = self.card["kind"]
        if kind == "gauge":
            self._gauge(painter, body, accent, track, muted)
        elif kind in {"meter", "progress"}:
            self._meter(painter, body, accent, track, muted)
        elif kind in {"counter", "stat"}:
            self._text(painter, body.adjusted(0, 0, 0, -34), _display(self.card.get("value", 0)),
                       min(30, max(16, body.height() / 3)), weight=QFont.Weight.DemiBold)
            self._text(painter, QRectF(body.left(), body.bottom() - 26, body.width(), 24),
                       self.card.get("unit", ""), color=muted)
        elif kind == "sparkline":
            self._sparkline(painter, body, accent, track, muted)
        elif kind in {"indicator", "status"}:
            self._indicator(painter, body, accent, muted)
        elif kind == "timer":
            seconds = self.card.get("seconds", 0)
            if self.card.get("running", False):
                elapsed = time.monotonic() - self.started
                seconds = max(0, seconds - elapsed) if self.card.get("mode") == "countdown" else seconds + elapsed
            seconds = int(seconds)
            days, remainder = divmod(seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            value = (f"{days}d " if days else "") + f"{hours:02}:{minutes:02}:{seconds:02}"
            self._text(painter, body.adjusted(0, 0, 0, -32), value, 23, weight=QFont.Weight.DemiBold)
            self._text(painter, QRectF(body.left(), body.bottom() - 24, body.width(), 24),
                       "Running" if self.card.get("running", False) else "Paused", color=muted)
        elif kind == "arrow":
            self._arrow(painter, body, accent)
        if detail:
            self._text(painter, QRectF(rect.left(), rect.bottom() - 22, rect.width(), 22),
                       detail, 10, muted)
        painter.setPen(foreground)
        painter.end()

    def _gauge(self, painter, rect, accent, track, muted):
        # A half dial leaves dedicated, nonintersecting lines for value/range.
        # The shared detail line is reserved by paintEvent below this rectangle.
        radius = max(18, min((rect.width() - 20) / 2, rect.height() - 56))
        center = QPointF(rect.center().x(), rect.top() + radius + 4)
        circle = QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius)
        thickness = max(5, min(11, radius / 7.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(track, thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(circle, 0, 180 * 16)
        low, high = self.card.get("min", 0), self.card.get("max", 100)
        value = self.card.get("value", low)
        ratio = (value - low) / (high - low)
        painter.setPen(QPen(accent, thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(circle, 180 * 16, -round(180 * ratio * 16))
        angle = math.radians(180 - ratio * 180)
        endpoint = center + QPointF(math.cos(angle) * radius * .72,
                                   -math.sin(angle) * radius * .72)
        painter.setPen(QPen(accent, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, endpoint)
        painter.setBrush(accent)
        painter.drawEllipse(center, 4, 4)
        self._text(painter, QRectF(rect.left(), center.y() + 6, rect.width(), 27),
                   _display(value) + (" " + self.card.get("unit", "") if self.card.get("unit") else ""),
                   18, weight=QFont.Weight.DemiBold)
        self._text(painter, QRectF(rect.left(), center.y() + 34, rect.width(), 16),
                   f"{_display(low)} – {_display(high)}", 8, muted)

    def _meter(self, painter, rect, accent, track, muted):
        low, high = self.card.get("min", 0), self.card.get("max", 100)
        value = self.card.get("value", low)
        ratio = (value - low) / (high - low)
        vertical = self.card.get("orientation") == "vertical"
        if vertical:
            bar = QRectF(rect.center().x() - 12, rect.top() + 5, 24, max(10, rect.height() - 65))
            filled = QRectF(bar.left(), bar.bottom() - bar.height() * ratio, bar.width(), bar.height() * ratio)
        else:
            bar = QRectF(rect.left() + 5, rect.center().y() - 37, rect.width() - 10, 18)
            filled = QRectF(bar.left(), bar.top(), bar.width() * ratio, bar.height())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(bar, 7, 7)
        painter.setBrush(accent)
        if ratio > 0:
            painter.drawRoundedRect(filled, 6, 6)
        y = rect.bottom() - 52 if vertical else bar.bottom() + 9
        self._text(painter, QRectF(rect.left(), y, rect.width(), 28),
                   f"{_display(value)} {self.card.get('unit', '')}".strip(), 18,
                   weight=QFont.Weight.DemiBold)
        self._text(painter, QRectF(rect.left(), y + 28, rect.width(), 20),
                   f"{_display(low)} – {_display(high)}", 9, muted)

    def _sparkline(self, painter, rect, accent, track, muted):
        values = self.card["values"]
        graph = rect.adjusted(6, 27, -6, -30)
        painter.setPen(QPen(track, 1))
        painter.drawLine(graph.bottomLeft(), graph.bottomRight())
        if not values:
            self._text(painter, graph, "No samples yet", color=muted)
            return
        low, high = min(values), max(values)
        span = high - low or 1
        path = QPainterPath()
        for index, value in enumerate(values):
            point = QPointF(graph.left() + graph.width() * index / max(1, len(values) - 1),
                            graph.bottom() - graph.height() * ((value - low) / span if high != low else .5))
            path.moveTo(point) if index == 0 else path.lineTo(point)
        painter.setPen(QPen(accent, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(path)
        painter.setBrush(accent)
        painter.drawEllipse(path.currentPosition(), 3.5, 3.5)
        self._text(painter, QRectF(rect.left(), rect.top(), rect.width(), 24),
                   f"{_display(values[-1])} {self.card.get('unit', '')}".strip(), 15,
                   weight=QFont.Weight.DemiBold)
        self._text(painter, QRectF(rect.left(), rect.bottom() - 23, rect.width(), 23),
                   self.card.get("label", f"{len(values)} samples"), 10, muted)

    def _indicator(self, painter, rect, accent, muted):
        state = self.card.get("state", "neutral")
        colors = {"success": "#54c8a0", "warning": "#d4ac5d", "error": "#e28787",
                  "offline": "#8ca0aa", "waiting": "#c7a1d9"}
        dark = self.palette().base().color().lightness() < 128
        color = QColor(colors.get(state, accent.name()))
        if not dark:
            color = color.darker(155)
        center = QPointF(rect.center().x(), rect.top() + max(21, (rect.height() - 60) / 2))
        painter.setPen(QPen(color, 2))
        fill = QColor(color)
        fill.setAlpha(36)
        painter.setBrush(fill)
        painter.drawEllipse(center, 21, 21)
        symbol = self.card.get("symbol", {"success": "✓", "error": "!", "warning": "!",
                                          "working": "↻", "waiting": "…", "offline": "○"}.get(state, "•"))
        self._text(painter, QRectF(center.x() - 22, center.y() - 22, 44, 44), symbol, 20, color)
        self._text(painter, QRectF(rect.left(), rect.bottom() - 51, rect.width(), 26),
                   self.card.get("text", state.capitalize()), 14, weight=QFont.Weight.DemiBold)
        if self.card.get("text"):
            self._text(painter, QRectF(rect.left(), rect.bottom() - 25, rect.width(), 20),
                       state.capitalize(), 9, muted)

    def _arrow(self, painter, rect, accent):
        center = rect.center() + QPointF(0, -12)
        size = max(12, min(rect.width() * .32, rect.height() * .25))
        painter.save()
        painter.translate(center)
        painter.rotate({"right": 0, "down": 90, "left": 180, "up": 270}[self.card.get("direction", "right")])
        path = QPainterPath(QPointF(-size, -size * .22))
        for x, y in ((.25, -.22), (.25, -.65), (1, 0), (.25, .65), (.25, .22), (-1, .22)):
            path.lineTo(x * size, y * size)
        path.closeSubpath()
        painter.setBrush(accent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)
        painter.restore()
        self._text(painter, QRectF(rect.left(), rect.bottom() - 30, rect.width(), 26),
                   self.card.get("text", ""), 12)


class Gadget(QWidget):
    """One validated display/control plus a reserved, responsive action area."""

    def __init__(self, card, source, policy, parent=None, on_event=None):
        super().__init__(parent)
        self.source, self.policy, self.on_event = source, policy, on_event
        self.card, self.content, self.control = {}, None, None
        self.layout_ = QVBoxLayout(self)
        self.layout_.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(5)
        self.actions = DirectActions({"kind": "text"}, policy, self, on_event)
        self.notice = _label(parent=self)
        self.notice.hide()
        self.layout_.addWidget(self.notice)
        self.layout_.addWidget(self.actions)
        self.set_card(card)

    def set_card(self, card):
        if not validate_component(card, self.policy):
            raise ValueError("Unknown native Prism component")
        kind_changed = self.card.get("kind") != card["kind"]
        self.card = copy.deepcopy(card)
        if kind_changed:
            if self.content is not None:
                if hasattr(self.content, "toolbar"):
                    self.layout_.removeWidget(self.content.toolbar)
                    self.content.toolbar.hide()
                    self.content.toolbar.deleteLater()
                self.layout_.removeWidget(self.content)
                self.content.hide()
                self.content.deleteLater()
            self.content = self._create_content()
            self.layout_.insertWidget(0, self.content, 1)
            if hasattr(self.content, "toolbar"):
                self.layout_.insertWidget(0, self.content.toolbar)
        self._update_content()
        self.actions.set_card(card)
        self.set_policy(self.policy)

    def _create_content(self):
        kind = self.card["kind"]
        self.control = None
        if kind == "table":
            return AdvancedTable(self)
        if kind not in {"action", "toggle", "slider", "select"}:
            return GadgetCanvas(self.card, self)
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(8)
        if kind == "action":
            self.control = _IntentButton(self.card.get("label", "Send request"), container)
            self.control.clicked.connect(lambda _checked=False: self._send(None))
        elif kind == "toggle":
            self.control = QCheckBox(container)
            self.control.clicked.connect(self._send)
        elif kind == "select":
            layout.addWidget(_label(self.card.get("label", "Choose an option"), container))
            self.control = QComboBox(container)
            self.control.setMinimumWidth(0)
            self.control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.control.setMinimumContentsLength(8)
            self.control.activated.connect(lambda _index: self._send(self.control.currentText()))
        else:
            self.value_label = _label(parent=container)
            layout.addWidget(self.value_label)
            self.control = QSlider(Qt.Orientation.Horizontal, container)
            self.control.valueChanged.connect(self._preview_slider)
            layout.addWidget(self.control)
            self.send_button = _IntentButton("Send value", container)
            self.send_button.clicked.connect(lambda _checked=False: self._send(self._slider_value()))
            layout.addWidget(self.send_button)
            self.control.setAccessibleName(self.card.get("label", "Value"))
            layout.addStretch(1)
            return container
        layout.addWidget(self.control)
        layout.addStretch(1)
        return container

    def _update_content(self):
        kind = self.card["kind"]
        if isinstance(self.content, GadgetCanvas) or kind == "table":
            self.content.set_card(self.card)
        elif kind == "action":
            self.control.label = self.card.get("label", "Send request")
            self.control.setText(self.control.label.replace("&", "&&"))
            self.control.setAccessibleName(self.control.label)
            self.control.setToolTip(self.control.label)
        elif kind == "toggle":
            self.control.setText(self.card.get("label", "Enabled").replace("&", "&&"))
            self.control.setChecked(self.card.get("value", False))
        elif kind == "select":
            self.control.clear()
            self.control.addItems(self.card["choices"])
            self.control.setCurrentText(self.card.get("value", self.card["choices"][0]))
            self.content.layout().itemAt(0).widget().setText(self.card.get("label", "Choose an option"))
        elif kind == "slider":
            low, high, value, step = _slider(self.card)
            self.control.setRange(0, round((high - low) / step))
            self.control.setValue(round((value - low) / step))
            self.control.setAccessibleName(self.card.get("label", "Value"))
            self._preview_slider()

    def _slider_value(self):
        value = self.card.get("min", 0) + self.control.value() * self.card.get("step", 1)
        return value

    def _preview_slider(self, _value=None):
        self.value_label.setText(self.card.get("label", "Value") + ": " + _display(self._slider_value()))
        self.control.setToolTip(_display(self._slider_value()))

    def _send(self, value):
        event = validate_event(self.card, {"action_id": self.card["action_id"], "value": value}, self.policy)
        if self.on_event is not None:
            self.on_event(event)

    def set_policy(self, policy, error=""):
        self.policy = policy
        try:
            validate_component(self.card, policy)
        except ValueError as exc:
            error = str(exc)
        self.content.setEnabled(not error and (self.control is None or self.on_event is not None))
        if hasattr(self.content, "toolbar"):
            self.content.toolbar.setEnabled(not error)
        self.actions.set_policy(policy, error)
        self.notice.setText("Component unavailable: " + error if error else "")
        self.notice.setVisible(bool(error))
        self.setToolTip(error or ("Preview only" if self.control is not None and self.on_event is None else ""))


def create_component(card, source, policy, parent=None, on_event=None):
    """Create a native, live-updatable component from the shared validated vocabulary."""
    return Gadget(card, source, policy, parent, on_event)
