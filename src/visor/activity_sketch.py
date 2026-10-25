"""Native Prism freehand canvas with bounded, portable vector strokes."""
from __future__ import annotations

import copy
import math
import re
from dataclasses import replace
from itertools import pairwise

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .activity_document import ActivityPolicy, _validate


def display_ink(value, background):
    """Keep stored stroke colors intact while making them legible on dark canvases."""
    color = QColor(value)
    def luminance(item):
        channels = [c / 255 for c in item.getRgb()[:3]]
        linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
        return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722), strict=True))
    back = luminance(background)
    if back >= .2:
        return color
    original = color.getRgb()[:3]
    target = (212, 222, 231)
    for step in range(11):
        shade = luminance(color)
        if (max(shade, back) + .05) / (min(shade, back) + .05) >= 4.5:
            break
        factor = (step + 1) / 11
        color = QColor(*(round(a + (b - a) * factor) for a, b in zip(original, target, strict=True)))
    return color


class SketchPad(QWidget):
    changed = Signal()

    def __init__(self, card, policy=None, parent=None):
        super().__init__(parent)
        self.policy = policy or ActivityPolicy()
        self.policy_error = ""
        self.color = "#147d80"
        self.pen_width = 3.0
        self.dirty = False
        self._active = None
        self._strokes = []
        self._points = 0
        self.setMinimumSize(120, 90)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.set_card(card)

    @property
    def strokes(self):
        """Return a detached snapshot; caller mutation cannot alter the canvas."""
        return copy.deepcopy(self._strokes)

    def set_policy(self, policy, error=""):
        if policy is not None:
            self.policy = policy
        self.policy_error = error
        self._active = None

    def set_card(self, card, *, restore_draft=False):
        # Recovery preserves a structurally valid local draft even after limits
        # decrease. These count bounds never replace the active drawing policy.
        policy = self.policy
        strokes = card.get("strokes")
        if restore_draft and isinstance(strokes, list):
            points = sum(len(stroke["points"]) for stroke in strokes
                         if isinstance(stroke, dict) and isinstance(stroke.get("points"), list))
            policy = replace(policy, max_items_per_card=max(policy.max_items_per_card, len(strokes)),
                             max_drawing_points=max(policy.max_drawing_points, points))
        _validate({"schema_version": 1, "id": "sketch-preview", "title": "Sketch",
                   "revision": 1, "cards": [card]}, policy)
        if card["kind"] != "sketch":
            raise ValueError("SketchPad requires a sketch card")
        self.canvas = list(card["canvas"])
        self._strokes = copy.deepcopy(card["strokes"])
        self._points = sum(len(stroke["points"]) for stroke in self._strokes)
        self._active = None
        self.dirty = False
        self.update()

    def _bounds(self):
        width, height = self.canvas
        scale = min(max(1, self.width() - 12) / width, max(1, self.height() - 12) / height)
        return QRectF((self.width() - width * scale) / 2,
                      (self.height() - height * scale) / 2,
                      width * scale, height * scale), scale

    def _point(self, position):
        bounds, scale = self._bounds()
        return [min(self.canvas[0], max(0.0, (position.x() - bounds.x()) / scale)),
                min(self.canvas[1], max(0.0, (position.y() - bounds.y()) / scale))]

    def _notify(self):
        self.dirty = True
        self.update()
        self.changed.emit()

    def undo(self):
        self._active = None
        if self._strokes:
            self._points -= len(self._strokes.pop()["points"])
            self._notify()

    def clear(self):
        self._active = None
        if self._strokes:
            self._strokes.clear()
            self._points = 0
            self._notify()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        if (self.policy_error or self._points >= self.policy.max_drawing_points
                or len(self._strokes) >= self.policy.max_items_per_card):
            return
        if (not isinstance(self.color, str)
                or not re.fullmatch(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?", self.color)
                or type(self.pen_width) not in (int, float)
                or not math.isfinite(self.pen_width)
                or not 0 < self.pen_width <= min(self.canvas)):
            return
        bounds, _ = self._bounds()
        if not bounds.contains(event.position()):
            return  # Letterbox space does not start an unintended border stroke.
        self._active = {"points": [self._point(event.position())],
                        "color": self.color, "width": self.pen_width}
        self._strokes.append(self._active)
        self._points += 1
        self._notify()
        event.accept()

    def _append(self, event):
        if self.policy_error or self._active is None or self._points >= self.policy.max_drawing_points:
            return
        point = self._point(event.position())
        if point != self._active["points"][-1]:
            self._active["points"].append(point)
            self._points += 1
            self._notify()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._append(event)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._append(event)
            self._active = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    @property
    def canvas_color(self):
        base = self.palette().base().color()
        return self.palette().alternateBase().color() if base.lightnessF() < .5 else base

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.palette().window())
        bounds, scale = self._bounds()
        painter.fillRect(bounds, self.canvas_color)
        painter.setClipRect(bounds)
        painter.translate(bounds.topLeft())
        painter.scale(scale, scale)
        for stroke in self._strokes:
            pen = QPen(display_ink(stroke["color"], self.canvas_color), stroke["width"], Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            points = stroke["points"]
            if len(points) == 1:
                painter.drawPoint(QPointF(*points[0]))
            else:
                for start, end in pairwise(points):
                    painter.drawLine(QPointF(*start), QPointF(*end))
        painter.end()
