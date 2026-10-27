"""Native Prism windows with local previews and authenticated broker collaboration."""
from __future__ import annotations

import copy
import hashlib
import html
import json
import math
import uuid
from dataclasses import fields
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QImageReader,
    QPainter,
    QPalette,
    QPen,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QMainWindow,
    QMdiArea,
    QMdiSubWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .activity_document import (
    ActivityPolicy,
    _text,
    _validate,
    read_document,
    resolve_image,
    save_reply,
)
from .activity_sketch import SketchPad
from .paths import write_private

STYLE = """
QWidget { background: #17232d; color: #d4dee7; font-size: 14px; }
QWidget#prismCard { background: #17232d; }
QLabel { background: transparent; color: #d4dee7; }
QMainWindow, QWidget#activityRoot { background: #111b24; }
QLabel#eyebrow { color: #85c4c5; font-size: 12px; font-weight: bold; }
QLabel#heading { color: #d4dee7; font-size: 14px; font-weight: bold; }
QLabel#metadata { color: #a5b5c3; font-size: 13px; }
QLabel#prismStatus { color: #a5b5c3; font-size: 8pt; }
QToolBar { border: 0; spacing: 3px; padding: 0; }
QToolButton { padding: 2px 5px; border: 1px solid #465b69; border-radius: 3px; }
QPushButton { background: #223440; color: #d4dee7; border: 1px solid #465b69; border-radius: 5px; padding: 7px 10px; }
QPushButton:hover { background: #2b4350; border-color: #739a9e; }
QPushButton#primary { background: #31595c; color: #e0e9ed; border: 1px solid #729b9e; }
QPushButton:disabled { color: #a0aebb; background: #202d38; }
QTextEdit, QTextBrowser, QComboBox { background: #14212b; color: #d4dee7; border: 1px solid #465b69; border-radius: 4px; padding: 5px; }
QComboBox QAbstractItemView { background: #223440; color: #d4dee7; selection-background-color: #31595c; }
QMdiSubWindow { background: #17232d; }
"""


class SafeHTML(HTMLParser):
    """Keep useful typography, never links, embedded resources or active content."""
    tags: ClassVar[set[str]] = {"p", "br", "hr", "h1", "h2", "h3", "h4", "b", "strong", "i", "em", "u",
            "ul", "ol", "li", "table", "thead", "tbody", "tr", "td", "th", "pre", "code",
            "blockquote", "span", "div", "small", "sub", "sup"}
    styles: ClassVar[set[str]] = {"font-size", "font-weight", "font-style",
              "text-align", "padding", "margin", "border-collapse"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "object"}:
            self.suppressed += 1
        if self.suppressed or tag not in self.tags:
            return
        safe = []
        for name, value in attrs:
            if name == "style":
                rules = []
                for rule in (value or "").split(";"):
                    key, sep, val = rule.partition(":")
                    if sep and key.strip().lower() in self.styles and not any(
                            char in val for char in "()\\<>@"):
                        rules.append(key.strip().lower() + ":" + val.strip())
                if rules:
                    safe.append('style="' + html.escape(";".join(rules), quote=True) + '"')
            elif (name in {"colspan", "rowspan"} and value and len(value) <= 3
                  and value.isascii() and value.isdecimal()):
                safe.append(f'{name}="{min(int(value), 100)}"')
        self.parts.append("<" + tag + (" " + " ".join(safe) if safe else "") + ">")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe", "object"}:
            self.suppressed = max(0, self.suppressed - 1)
        elif not self.suppressed and tag in self.tags:
            self.parts.append("</" + tag + ">")

    def handle_data(self, data):
        if not self.suppressed:
            self.parts.append(html.escape(data))


def question_fingerprint(card):
    value = json.dumps([card["prompt"], card.get("choices", [])], ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()


def safe_html(value):
    parser = SafeHTML()
    parser.feed(value)
    return "".join(parser.parts)


class LocalTextDocument(QTextDocument):
    def loadResource(self, resource_type, name):
        return QImage()  # No fallback filesystem/network resource loader.


class Visual(QWidget):
    """Scalable illustration, chart or approved raster image, without web addons."""
    def __init__(self, card, source, policy):
        super().__init__()
        self.card, self.source, self.policy = card, source, policy
        self.image = QImage()
        self.error = ""
        self.original_size = False
        self.setMinimumSize(120, 90)
        self.set_card(card)

    def set_card(self, card):
        self.card, self.error = card, ""
        self.image = QImage()
        if card["kind"] == "image":
            try:
                path = resolve_image(self.source, card["path"], self.policy)
                reader = QImageReader(str(path))
                size = reader.size()
                if not size.isValid() or size.width() * size.height() > self.policy.max_image_pixels:
                    raise ValueError("Picture dimensions are invalid or exceed the configured limit")
                self.image = reader.read()
                if self.image.isNull():
                    raise ValueError("Picture could not be decoded")
            except (OSError, ValueError) as exc:
                self.error = str(exc)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), self.palette().base())
        if self.error:
            p.setPen(QColor("#a13f3f"))
            p.drawText(self.rect().adjusted(12, 12, -12, -12), Qt.TextFlag.TextWordWrap, self.error)
        elif self.card["kind"] == "image":
            box = QRectF(self.rect().adjusted(10, 10, -10, -10))
            if not self.image.isNull():
                scale = min(box.width() / self.image.width(), box.height() / self.image.height())
                w, h = self.image.width() * scale, self.image.height() * scale
                p.drawImage(QRectF(box.center().x() - w / 2, box.center().y() - h / 2, w, h), self.image)
        elif self.card["kind"] == "chart":
            self.draw_chart(p)
        else:
            self.draw_diagram(p)
        p.end()

    def draw_chart(self, p):
        values, labels = self.card["values"], self.card["labels"]
        box = QRectF(100, 18, max(40, self.width() - 170), max(40, self.height() - 40))
        if not values:
            return
        magnitude = max(abs(v) for v in values) or 1
        scaled = [v / magnitude for v in values]
        low, high = min(0, min(scaled)), max(0, max(scaled))
        span = high - low or 1
        zero = box.left() + (0 - low) / span * box.width()
        step = box.height() / len(values)
        for i, (label, value) in enumerate(zip(labels, values, strict=True)):
            y = box.top() + i * step
            p.setPen(self.palette().text().color())
            p.drawText(QRectF(5, y, 88, step), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
            end = box.left() + (value / magnitude - low) / span * box.width()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#168486" if value >= 0 else "#be6a50"))
            p.drawRoundedRect(QRectF(min(zero, end), y + step * .2, max(1, abs(end - zero)), step * .6), 4, 4)
            p.setPen(self.palette().text().color())
            p.drawText(QRectF(box.right() + 8, y, 65, step), Qt.AlignmentFlag.AlignVCenter,
                       f"{value:g}{self.card.get('unit', '')}")

    def draw_diagram(self, p):
        nodes = self.card["nodes"]
        if not nodes:
            return
        left = min(n["x"] for n in nodes) - 20
        top = min(n["y"] for n in nodes) - 20
        right = max(n["x"] + n["w"] for n in nodes) + 20
        bottom = max(n["y"] + n["h"] for n in nodes) + 20
        scale = min(self.width() / (right - left), self.height() / (bottom - top))
        p.translate((self.width() - (right - left) * scale) / 2,
                    (self.height() - (bottom - top) * scale) / 2)
        p.scale(scale, scale)
        p.translate(-left, -top)
        by_id = {n["id"]: n for n in nodes}
        for edge in self.card.get("edges", []):
            a, b = by_id[edge["from"]], by_id[edge["to"]]
            start = QPointF(a["x"] + a["w"] / 2, a["y"] + a["h"] / 2)
            end = QPointF(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
            p.setPen(QPen(QColor("#658492"), 2))
            p.drawLine(start, end)
            dx, dy = end.x() - start.x(), end.y() - start.y()
            length = math.hypot(dx, dy) or 1
            mid = (start + end) / 2
            ux, uy = dx / length, dy / length
            p.drawLine(mid, mid - QPointF(ux * 10 - uy * 5, uy * 10 + ux * 5))
            p.drawLine(mid, mid - QPointF(ux * 10 + uy * 5, uy * 10 - ux * 5))
            if edge.get("label"):
                p.drawText(QRectF(mid.x() - 60, mid.y() - 24, 120, 20), Qt.AlignmentFlag.AlignCenter, edge["label"])
        for node in nodes:
            color = QColor(node.get("color", "#e5f2f1"))
            if not color.isValid():
                color = QColor("#e5f2f1")
            base = self.palette().base().color()
            if base.lightnessF() < .5:
                color = QColor(*(round(.3 * c + .7 * b) for c, b in zip(color.getRgb()[:3], base.getRgb()[:3], strict=True)))
            p.setBrush(color)
            p.setPen(QPen(QColor("#6b929b"), 1.2))
            rect = QRectF(node["x"], node["y"], node["w"], node["h"])
            p.drawRoundedRect(rect, 9, 9)
            p.setPen(self.palette().text().color())
            p.drawText(rect.adjusted(8, 5, -8, -5), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, node["label"])


class Card(QMdiSubWindow):
    def __init__(self, board, data):
        super().__init__()
        self.board, self.data = board, data
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMinimumSize(210, 145)
        self.inner = QWidget()
        self.inner.setObjectName("prismCard")
        self.layout = QVBoxLayout(self.inner)
        self.layout.setContentsMargins(12, 10, 12, 10)
        if data["kind"] == "question":
            self.question_scroll = QScrollArea()
            self.question_scroll.setWidgetResizable(True)
            self.question_scroll.setWidget(self.inner)
            self.layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
            self.setWidget(self.question_scroll)
        else:
            self.setWidget(self.inner)
        self.reply = None
        self.question_changed = False
        self.edit_dirty = False
        self.edit_revision = board.document["revision"]
        self.pending_reply = None
        self.pending_edit = None
        self.accepted_edit = None
        self.editor = None
        self.edit_sending = self.reply_sending = False
        kind = data["kind"]
        if kind == "text":
            self.content = QTextBrowser()
            self.content.setOpenLinks(False)
            self.content.setOpenExternalLinks(False)
            self.content.setDocument(LocalTextDocument(self.content))
        elif kind == "sketch":
            self.content = SketchPad(data, board.policy)
            self.content.setPalette(board.palette())
        elif kind == "question":
            self.content = QLabel()
            self.content.setTextFormat(Qt.TextFormat.PlainText)
            self.content.setWordWrap(True)
            self.content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            self.choices = QComboBox()
            self.reply = QTextEdit()
            self.reply.setPlaceholderText("Your answer or a change you would like…")
            self.reply.setMinimumHeight(65)
        else:
            self.content = Visual(data, board.source, board.policy)
        self.layout.addWidget(self.content, 0 if kind == "question" else 1)
        self.caption = QLabel()
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        self.caption.setObjectName("metadata")
        self.caption.setWordWrap(True)
        self.caption.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.layout.addWidget(self.caption)
        if kind in {"text", "sketch"}:
            if kind == "text":
                self.editor = QTextEdit()
                self.editor.setDocument(LocalTextDocument(self.editor))
                self.editor.setHtml(safe_html(data["html"]))
                self.editor.hide()
                self.layout.addWidget(self.editor, 1)
                self.editor.textChanged.connect(self.mark_edited)
            row = QHBoxLayout()
            if kind == "text":
                edit = QPushButton("Type / edit")
                edit.clicked.connect(lambda: self.editor.setVisible(not self.editor.isVisible()))
                row.addWidget(edit)
            else:
                self.content.changed.connect(self.mark_edited)
                color = QComboBox()
                for label, value in [("Teal", "#087d80"), ("Navy", "#20354b"), ("Red", "#bf503e"), ("Gold", "#bc8c23")]:
                    color.addItem(label, value)
                color.currentIndexChanged.connect(lambda: setattr(self.content, "color", color.currentData()))
                row.addWidget(color)
                undo = QPushButton("Undo stroke")
                undo.clicked.connect(self.content.undo)
                row.addWidget(undo)
            self.save_edit_button = QPushButton("Save changes")
            self.save_edit_button.clicked.connect(self.save_edit)
            row.addWidget(self.save_edit_button)
            discard = QPushButton("Reload card")
            self.reload_edit_button = discard
            discard.clicked.connect(self.discard_edit)
            row.addWidget(discard)
            self.layout.addLayout(row)
            self.edit_status = QLabel("Type a note or draw; save to share your changes.")
            self.edit_status.setTextFormat(Qt.TextFormat.PlainText)
            self.edit_status.setWordWrap(True)
            self.layout.addWidget(self.edit_status)
        if kind == "question":
            self.layout.addWidget(self.choices)
            self.layout.addWidget(self.reply, 1)
            row = QHBoxLayout()
            self.send = QPushButton("Save reply for session")
            self.send.setObjectName("primary")
            self.send.clicked.connect(self.save_answer)
            self.review = QPushButton("I reviewed the updated question")
            self.review.clicked.connect(self.review_question)
            self.review.hide()
            row.addWidget(self.send)
            self.layout.addLayout(row)
            self.layout.addWidget(self.review)
            self.response_status = QLabel("Replies are saved for the contributing session to read." if board.submit else "Replies are saved locally; no VISOR delivery is claimed.")
            self.response_status.setTextFormat(Qt.TextFormat.PlainText)
            self.response_status.setWordWrap(True)
            self.response_status.setObjectName("metadata")
            self.response_status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            self.layout.addWidget(self.response_status)
            self.reply.textChanged.connect(board.schedule_layout_save)
            self.choices.currentIndexChanged.connect(board.schedule_layout_save)
            self.reply.textChanged.connect(self.check_policy)
            self.choices.currentIndexChanged.connect(self.check_policy)
        self.policy_notice = QLabel()
        self.policy_notice.setTextFormat(Qt.TextFormat.PlainText)
        self.policy_notice.setWordWrap(True)
        self.policy_notice.setObjectName("metadata")
        self.policy_notice.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.layout.addWidget(self.policy_notice)
        self.update_card(data, initial=True)

    def update_card(self, data, initial=False):
        previous = self.data
        self.data = data
        accepted = self.accepted_edit
        if accepted and self.board.document["revision"] >= accepted["revision"]:
            self.accepted_edit = None
            self.edit_status.setText("Saved revision " + str(accepted["revision"]) + " · current document loaded."
                                     + (" Your newer draft remains unsaved." if self.edit_dirty else ""))
        self.setWindowTitle(data["title"])
        kind = data["kind"]
        if kind == "text":
            scroll = self.content.verticalScrollBar().value()
            self.content.setHtml(safe_html(data["html"]))
            self.content.verticalScrollBar().setValue(scroll)
            if not self.edit_dirty and not self.accepted_edit:
                self.editor.blockSignals(True)
                self.editor.setHtml(safe_html(data["html"]))
                self.editor.blockSignals(False)
                self.edit_revision = self.board.document["revision"]
        elif kind == "sketch":
            if not self.edit_dirty and not self.accepted_edit:
                self.content.set_card(data)
                self.edit_revision = self.board.document["revision"]
        elif kind == "question":
            changed = (previous.get("prompt"), previous.get("choices")) != (data["prompt"], data.get("choices"))
            choice = self.choices.currentText()
            self.content.setText(data["prompt"])
            self.choices.clear()
            self.choices.addItem("Choose an option (optional)")
            self.choices.addItems(data.get("choices", []))
            if choice in data.get("choices", []):
                self.choices.setCurrentText(choice)
            self.choices.setVisible(bool(data.get("choices")))
            if changed and not initial and (self.reply.toPlainText() or choice in previous.get("choices", [])):
                self.question_changed = True
                self.send.setEnabled(False)
                self.review.show()
                self.response_status.setText("The question changed. Your draft is preserved; review before saving it.")
        else:
            self.content.set_card(data)
        caption = data.get("caption", "")
        if data.get("contributor"):
            caption += (" · " if caption else "") + "Contributor: " + data["contributor"]
        self.caption.setText(caption)
        self.caption.setVisible(bool(caption))
        if self.edit_dirty and previous != data:
            self.edit_status.setText("This card changed while you were editing. Your draft is preserved; compare the current content before saving.")
        self.check_policy()

    def mark_edited(self):
        if not self.edit_dirty:
            self.edit_revision = self.accepted_edit["revision"] if self.accepted_edit else self.board.document["revision"]
        self.edit_dirty = True
        self.pending_edit = None
        self.check_policy()
        self.board.schedule_layout_save()

    def discard_edit(self):
        if self.accepted_edit:
            return
        self.edit_dirty = False
        self.pending_edit = None
        self.update_card(self.data)
        self.edit_status.setText("Current card loaded. New changes will use the current revision.")
        self.board.schedule_layout_save()

    def answer(self):
        answer = self.reply.toPlainText().strip()
        if self.choices.currentIndex() > 0:
            answer = self.choices.currentText() + ("\n" + answer if answer else "")
        return answer

    def validate_edit(self, content):
        candidate = copy.deepcopy(self.board.document)
        card = next(item for item in candidate["cards"] if item["id"] == self.data["id"])
        card["html" if self.editor else "strokes"] = content
        candidate["revision"] += 1
        candidate["last_editor"] = "operator"
        _validate(candidate, self.board.policy)
        encoded = json.dumps(candidate, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > self.board.policy.max_document_bytes:
            raise ValueError("Activity document exceeds the byte limit")

    def check_policy(self):
        error = self.board.policy_error
        if not error:
            try:
                if self.reply:
                    _text(self.answer(), "answer", self.board.policy, limit=self.board.policy.max_answer_chars)
                    if self.pending_reply and not self.pending_reply.get("confirmed"):
                        _text(self.pending_reply["params"]["answer"], "pending answer", self.board.policy,
                              limit=self.board.policy.max_answer_chars)
                elif self.editor or self.data["kind"] == "sketch":
                    content = safe_html(self.editor.toHtml()) if self.editor else self.content.strokes
                    self.validate_edit(content if self.edit_dirty else self.data["html" if self.editor else "strokes"])
                elif self.data["kind"] == "image":
                    error = self.content.error
            except (ValueError, TypeError, StopIteration) as exc:
                error = str(exc)
        self.policy_notice.setText("On hold: " + error + ". Your content is preserved." if error else "")
        self.policy_notice.setVisible(bool(error))
        if self.reply:
            self.send.setEnabled(not error and not self.question_changed and not self.reply_sending)
        elif self.editor or self.data["kind"] == "sketch":
            self.save_edit_button.setEnabled(not error and not self.edit_sending and not self.accepted_edit)
            self.reload_edit_button.setEnabled(not self.accepted_edit)
        return not error

    def save_edit(self):
        if self.board.retired:
            return
        if self.accepted_edit or not self.edit_dirty or not self.check_policy():
            return
        content = safe_html(self.editor.toHtml()) if self.editor else copy.deepcopy(self.content.strokes)
        params = {"id": self.board.document["id"], "card_id": self.data["id"],
                  "revision": self.edit_revision, "content": content}
        if not self.pending_edit or self.pending_edit["params"] != params:
            self.pending_edit = {"params": params, "request_id": uuid.uuid4().hex}
        request = copy.deepcopy(self.pending_edit)
        self.edit_sending = True
        self.check_policy()
        self.board.schedule_layout_save()
        def finished(result, error):
            self.edit_sending = False
            self.check_policy()
            if error:
                self.edit_status.setText("Not confirmed: " + str(error) + ". Draft kept. Reload card only to discard it.")
                return
            newer_draft = self.pending_edit != request
            self.accepted_edit = {"revision": result["revision"], "request_id": request["request_id"],
                                  "content": copy.deepcopy(request["params"]["content"])}
            if not newer_draft:
                self.edit_dirty = False
                self.pending_edit = None
            self.edit_revision = result["revision"]
            self.board.poll_document()
            if self.accepted_edit:
                if self.editor:
                    self.editor.show()
                self.edit_status.setText("Saved revision " + str(result["revision"]) + " · shared display update pending."
                                        + (" Your newer draft remains unsaved." if newer_draft else ""))
            elif newer_draft:
                self.edit_status.setText("Earlier changes saved · your newer draft is unsaved.")
            self.check_policy()
            self.board.schedule_layout_save()
        self.board.request("activity_edit", finished, request_id=request["request_id"], **params)

    def review_question(self):
        self.question_changed = False
        self.check_policy()
        self.review.hide()
        self.response_status.setText("Draft reviewed. Saving creates a local reply for the session to read.")

    def save_answer(self):
        if self.board.retired:
            return
        if self.question_changed or not self.check_policy():
            return
        answer = self.answer()
        if not answer:
            self.response_status.setText("Choose an option or enter an answer first.")
            return
        if self.board.submit:
            params = {"id": self.board.document["id"], "card_id": self.data["id"],
                      "revision": self.board.document["revision"], "answer": answer}
            fingerprint = question_fingerprint(self.data)
            if (not self.pending_reply or (self.pending_reply.get("confirmed") and
                    (self.pending_reply["params"]["answer"] != answer or self.pending_reply.get("fingerprint") != fingerprint))):
                self.pending_reply = {"params": params, "request_id": uuid.uuid4().hex, "fingerprint": fingerprint}
            # An uncertain receipt is reconciled with its exact original request, even after updates.
            request = copy.deepcopy(self.pending_reply)
            self.reply_sending = True
            self.check_policy()
            self.board.schedule_layout_save()
            def finished(event, error):
                self.reply_sending = False
                self.check_policy()
                if error:
                    self.response_status.setText("Reply not confirmed: " + str(error) + ". Retry reuses the same request.")
                    return
                self.board.last_reply = event
                if self.pending_reply and self.pending_reply["request_id"] == request["request_id"]:
                    self.pending_reply["confirmed"] = True
                    self.pending_reply["event_id"] = event["id"]
                self.response_status.setText("Saved for the contributor · waiting for acknowledgment")
                self.board.schedule_layout_save()
            self.board.request("activity_reply", finished, request_id=request["request_id"], **request["params"])
            return
        try:
            event = save_reply(self.board.state_dir, self.board.document, self.data, answer, self.board.policy)
        except (OSError, ValueError) as exc:
            self.response_status.setText("Reply not saved: " + str(exc))
            return
        self.response_status.setText("Saved locally · waiting for the session to read it")
        self.board.last_reply = event
        self.board.schedule_layout_save()

    def closeEvent(self, event):
        self.hide()
        event.ignore()
        self.board.schedule_layout_save()

    def moveEvent(self, event):
        super().moveEvent(event)
        self.board.schedule_layout_save()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.board.schedule_layout_save()


class SingleLineStatus(QLabel):
    def setText(self, text):
        self.setToolTip("<qt>" + html.escape(text).replace("\n", "<br>") + "</qt>")
        super().setText(" ".join(text.split()))


class ActivityWindow(QMainWindow):
    def __init__(self, source, state_dir, policy=None, submit=None, on_view=None):
        super().__init__()
        self.submit = submit
        self.on_view = on_view
        self.retired = False
        self.suppress_view = False
        self.closing_view = False
        self.setStyleSheet(STYLE)
        palette = self.palette()
        for role, color in ((QPalette.ColorRole.Window, "#17232d"),
                            (QPalette.ColorRole.WindowText, "#d4dee7"),
                            (QPalette.ColorRole.Base, "#14212b"),
                            (QPalette.ColorRole.AlternateBase, "#243743"),
                            (QPalette.ColorRole.Text, "#d4dee7"),
                            (QPalette.ColorRole.Button, "#223440"),
                            (QPalette.ColorRole.ButtonText, "#d4dee7"),
                            (QPalette.ColorRole.Highlight, "#31595c"),
                            (QPalette.ColorRole.HighlightedText, "#e0e9ed"),
                            (QPalette.ColorRole.PlaceholderText, "#a5b5c3")):
            palette.setColor(role, QColor(color))
        self.setPalette(palette)
        self.source, self.state_dir = Path(source).resolve(), Path(state_dir).resolve()
        self.policy = policy or ActivityPolicy()
        self.policy_error = ""
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.document = read_document(self.source, self.policy)
        self.cards, self.other_windows = {}, []
        self.last_reply = None
        self.receipts_busy = False
        self.loading = True
        self.signature = None
        self.layout_timer = QTimer(self)
        self.layout_timer.setSingleShot(True)
        self.layout_timer.setInterval(300)
        self.layout_timer.timeout.connect(self.save_layout)
        self.setWindowTitle(self.document["title"] + " · Prism Window")
        self.resize(1260, 850)
        root = QWidget()
        root.setObjectName("activityRoot")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(8, 5, 8, 4)
        outer.setSpacing(3)
        self.heading = QLabel()
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setObjectName("heading")
        self.heading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        outer.addWidget(self.heading)
        self.metadata = QLabel(root)
        self.metadata.setTextFormat(Qt.TextFormat.PlainText)
        self.metadata.setObjectName("metadata")
        self.metadata.setWordWrap(True)
        self.metadata.hide()  # Retain provenance in the heading tooltip, not the header.
        self.actions_toggle = QToolButton()
        self.actions_toggle.setText("Actions")
        self.actions_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.actions_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.actions_toggle.setCheckable(True)
        self.actions_toggle.setAccessibleName("Show or hide Prism actions")
        outer.addWidget(self.actions_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.actions_bar = QToolBar("Prism actions")
        self.actions_bar.setMovable(False)
        self.actions_bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        for label, callback in [("Open topic…", self.open_topic), ("Show all cards", self.show_cards),
                                ("Tile cards", self.tile_cards), ("Reset layout", self.reset_layout),
                                ("Clear view", self.clear_view), ("Save layout", self.save_layout),
                                ("Export picture…", self.export_picture)]:
            self.actions_bar.addAction(label, callback)
        outer.addWidget(self.actions_bar)
        self.actions_bar.hide()
        self.actions_toggle.toggled.connect(self.toggle_actions)
        self.area = QMdiArea()
        self.area.setBackground(QColor("#0e1820"))
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.area.setActivationOrder(QMdiArea.WindowOrder.CreationOrder)
        outer.addWidget(self.area, 1)
        self.status = SingleLineStatus()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setObjectName("prismStatus")
        self.status.setWordWrap(False)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        outer.addWidget(self.status)
        self.setCentralWidget(root)
        self.apply_document(self.document)
        self.restore_layout()
        self.loading = False
        self.watch = QTimer(self)
        self.watch.setInterval(700)
        self.watch.timeout.connect(self.poll_document)
        self.watch.start()
        self.receipt_timer = QTimer(self)
        self.receipt_timer.setInterval(3000)
        self.receipt_timer.timeout.connect(self.refresh_receipts)
        if self.submit:
            self.receipt_timer.start()
        self.capture_signature()
        self.status.setText("Ready · drag or resize cards; changes require Save.")

    def toggle_actions(self, expanded):
        self.actions_bar.setVisible(expanded)
        self.actions_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.schedule_layout_save()

    @property
    def layout_path(self):
        return self.state_dir / (self.document["id"] + ".layout.json")

    def capture_signature(self):
        try:
            st = self.source.stat()
            self.signature = (st.st_mtime_ns, st.st_size)
        except OSError:
            self.signature = None

    def apply_document(self, document):
        self.document = document
        try:
            updated = datetime.fromisoformat(document.get("updated_at", ""))
            if updated.tzinfo is None:
                raise ValueError("Update timestamp needs a timezone")
        except (TypeError, ValueError):
            updated = datetime.fromtimestamp(self.source.stat().st_mtime).astimezone()
        local = updated.astimezone()
        stamp = local.strftime("%m/%d/%Y  %I:%M ") + ("am" if local.hour < 12 else "pm")
        title = " ".join(document["title"].split()) + " - Updated: " + stamp
        self.heading.setText(title)
        self.setWindowTitle(title)
        linked = document.get("session_link", {})
        session = linked.get("label", linked.get("id", "Local document"))
        self.metadata.setText(f"{session}  ·  revision {document['revision']}  ·  {document.get('parent_record', 'Working topic')}\n"
                              "Session link is document metadata. This preview does not authenticate or control a running session.")
        if self.submit:
            contributors = sorted(set(document.get("contributors", {}).values()))
            self.metadata.setText(f"Owner: {linked.get('id', session)} · revision {document['revision']} · {document.get('parent_record', 'Working topic')}\n"
                                  + "Contributors: " + ", ".join(contributors) + " · Last edit: " + document.get("last_editor", "session"))
        self.heading.setToolTip("<qt>" + html.escape(self.metadata.text()).replace("\n", "<br>") + "</qt>")
        active = set()
        for i, data in enumerate(document["cards"]):
            key = data["id"]
            active.add(key)
            card = self.cards.get(key)
            if card and card.data["kind"] != data["kind"]:
                raise ValueError("Changing a card kind requires a new card ID")
            if card:
                card.update_card(data)
            else:
                card = Card(self, data)
                self.cards[key] = card
                self.area.addSubWindow(card)
                card.setGeometry(*data.get("geometry", [20 + i % 3 * 380, 20 + i // 3 * 300, 360, 280]))
                card.show()
        for key, card in self.cards.items():
            if key not in active:
                card.hide()  # Keep unsent drafts; never delete source or task records.

    def apply_policy(self, policy, error=""):
        changed = policy is not None and policy != self.policy
        if policy is not None:
            self.policy = policy
        self.policy_error = error
        for card in self.cards.values():
            if card.data["kind"] == "sketch":
                card.content.set_policy(policy, error)
            elif isinstance(card.content, Visual) and policy is not None:
                card.content.policy = policy
                if changed or card.content.error:
                    card.content.set_card(card.data)
            card.check_policy()
        if error:
            self.status.setText("On hold: " + error + " · drafts preserved.")
        else:
            self.signature = None

    def poll_document(self):
        if self.policy_error:
            return
        try:
            st = self.source.stat()
            signature = (st.st_mtime_ns, st.st_size)
            if signature == self.signature:
                return
            candidate = read_document(self.source, self.policy)
            if candidate["id"] != self.document["id"]:
                raise ValueError("Topic identity changed; open it as a separate topic")
            if candidate != self.document and candidate["revision"] <= self.document["revision"]:
                raise ValueError("Changed content requires a newer revision; keeping the last accepted view")
            for data in candidate["cards"]:
                if data["id"] in self.cards and self.cards[data["id"]].data["kind"] != data["kind"]:
                    raise ValueError("Changed card kind requires a new card ID")
            self.apply_document(candidate)
            self.signature = signature
            self.observe_view_later()
            self.status.setText(f"Updated · revision {candidate['revision']} · drafts preserved.")
            self.schedule_layout_save()
        except (OSError, ValueError, KeyError) as exc:
            self.status.setText("Update unavailable · last accepted content: " + str(exc))

    def schedule_layout_save(self):
        if not self.loading and not self.retired:
            self.layout_timer.start()

    def save_layout(self):
        if self.loading or self.retired:
            return
        try:
            cards = {}
            for key, card in self.cards.items():
                cards[key] = {"geometry": [card.x(), card.y(), card.width(), card.height()],
                              "visible": not card.isHidden()}
                if card.editor or card.data["kind"] == "sketch":
                    cards[key].update(edit_dirty=card.edit_dirty, edit_revision=card.edit_revision,
                                      edit_content=safe_html(card.editor.toHtml()) if card.editor else card.content.strokes,
                                      pending_edit=card.pending_edit, accepted_edit=card.accepted_edit)
                if card.reply:
                    cards[key]["pending_reply"] = card.pending_reply
                    cards[key].update(draft=card.reply.toPlainText(), choice=card.choices.currentText(),
                                      prompt_hash=question_fingerprint(card.data), needs_review=card.question_changed)
            write_private(self.layout_path, {"board_id": self.document["id"], "cards": cards,
                                             "actions_expanded": self.actions_toggle.isChecked(),
                                             "geometry": [self.x(), self.y(), self.width(), self.height()]})
        except (OSError, ValueError) as exc:
            self.status.setText("Layout not saved: " + str(exc))

    def restore_layout(self):
        try:
            saved = json.loads(self.layout_path.read_text())
            if not isinstance(saved, dict) or saved.get("board_id") != self.document["id"]:
                return
            self.actions_toggle.setChecked(saved.get("actions_expanded") is True)
            geometry = saved.get("geometry")
            if self.valid_geometry(geometry):
                screen = QApplication.primaryScreen().availableGeometry()
                x, y, w, h = geometry
                self.setGeometry(max(screen.left(), min(x, screen.right() - 200)),
                                 max(screen.top(), min(y, screen.bottom() - 100)),
                                 min(w, screen.width()), min(h, screen.height()))
            saved_cards = saved.get("cards", {})
            if not isinstance(saved_cards, dict):
                saved_cards = {}
            for key, card in self.cards.items():
                state = saved_cards.get(key, {})
                if not isinstance(state, dict):
                    continue
                if self.valid_geometry(state.get("geometry")):
                    card.setGeometry(*state["geometry"])
                card.setVisible(state.get("visible", True))
                accepted = state.get("accepted_edit")
                if (not isinstance(accepted, dict) or type(accepted.get("revision")) is not int
                        or accepted["revision"] <= self.document["revision"]
                        or not isinstance(accepted.get("request_id"), str)
                        or not isinstance(accepted.get("content"), str if card.editor else list)):
                    accepted = None
                if (state.get("edit_dirty") or accepted) and (card.editor or card.data["kind"] == "sketch"):
                    draft = copy.deepcopy(card.data)
                    draft["html" if card.editor else "strokes"] = state.get("edit_content")
                    candidate = copy.deepcopy(self.document)
                    candidate["cards"] = [draft]
                    try:
                        if card.editor:
                            if not isinstance(draft["html"], str):
                                raise ValueError("Saved text draft must be text")
                            card.editor.setHtml(safe_html(draft["html"]))
                            card.editor.show()
                        else:
                            card.content.set_card(draft, restore_draft=True)
                        card.edit_dirty = bool(state.get("edit_dirty"))
                        card.accepted_edit = accepted
                        card.edit_revision = state.get("edit_revision", 0)
                        card.pending_edit = self.valid_pending(state.get("pending_edit"), key, "content")
                        card.edit_status.setText("Saved revision " + str(accepted["revision"]) + " · shared display update pending."
                                                 if accepted else "Saved draft restored. Current content may have changed; saving checks its revision.")
                    except (ValueError, TypeError):
                        pass
                if card.reply:
                    card.pending_reply = self.valid_pending(state.get("pending_reply"), key, "answer")
                    draft = state.get("draft", "")
                    card.reply.setPlainText(draft if isinstance(draft, str) else "")
                    if state.get("choice") in card.data.get("choices", []):
                        card.choices.setCurrentText(state["choice"])
                    if (state.get("needs_review", False) or (card.reply.toPlainText() or state.get("choice") in card.data.get("choices", []))
                            and state.get("prompt_hash") != question_fingerprint(card.data)):
                        card.question_changed = True
                        card.send.setEnabled(False)
                        card.review.show()
                        card.response_status.setText("The question changed since this draft. Review before saving.")
                card.check_policy()
        except (OSError, ValueError, TypeError, KeyError):
            return  # Missing/corrupt private layout never blocks the document.

    def valid_pending(self, value, card_id, content_key):
        if not isinstance(value, dict) or not isinstance(value.get("request_id"), str):
            return None
        if not value["request_id"] or len(value["request_id"]) > 128:
            return None
        params = value.get("params")
        if (not isinstance(params, dict) or set(params) != {"id", "card_id", "revision", content_key}
                or params.get("id") != self.document["id"] or params.get("card_id") != card_id
                or type(params.get("revision")) is not int or params["revision"] < 1):
            return None
        if content_key == "answer" and not isinstance(params[content_key], str):
            return None
        return value

    @staticmethod
    def valid_geometry(value):
        return (isinstance(value, list) and len(value) == 4 and all(type(v) is int for v in value)
                and all(abs(v) <= 100000 for v in value) and value[2] >= 100 and value[3] >= 80)

    def show_cards(self):
        active = {c["id"] for c in self.document["cards"]}
        for key, card in self.cards.items():
            if key in active:
                card.show()
        self.schedule_layout_save()

    def clear_view(self):
        for card in self.cards.values():
            card.hide()
        self.status.setText("View cleared · Actions → Show all cards to restore.")
        self.schedule_layout_save()

    def tile_cards(self):
        self.show_cards()
        self.area.tileSubWindows()
        self.schedule_layout_save()

    def reset_layout(self):
        self.show_cards()
        for i, data in enumerate(self.document["cards"]):
            self.cards[data["id"]].setGeometry(*data.get("geometry", [20 + i % 3 * 380, 20 + i // 3 * 300, 360, 280]))
        self.schedule_layout_save()

    def refresh_receipts(self):
        pending = {card.pending_reply.get("event_id"): card for card in self.cards.values()
                   if isinstance(card.pending_reply, dict) and card.pending_reply.get("event_id")}
        if not self.submit or not pending or self.receipts_busy or self.isHidden():
            return
        self.receipts_busy = True
        def finished(events, error):
            self.receipts_busy = False
            if error:
                for card in pending.values():
                    card.response_status.setText("Receipt status unavailable · checking again; your saved reply is preserved.")
                return
            for event in events:
                card = pending.get(event["id"])
                if card and event["state"] == "read":
                    card.response_status.setText("Read by " + event["recipient"] + " · acknowledgment is not task completion")
                elif card and event["state"] == "pending":
                    card.response_status.setText("Saved for the contributor · waiting for acknowledgment")
        self.request("activity_replies", finished, board_id=self.document["id"])

    def request(self, action, callback, **params):
        if self.retired:
            return
        if action in {"activity_edit", "activity_reply"}:
            try:
                if self.policy_error:
                    raise ValueError(self.policy_error)
                if action == "activity_reply":
                    _text(params["answer"], "answer", self.policy, nonempty=True, limit=self.policy.max_answer_chars)
                else:
                    self.cards[params["card_id"]].validate_edit(params["content"])
            except (ValueError, TypeError, KeyError, StopIteration) as exc:
                callback(None, "Current policy holds this operation: " + str(exc))
                return
        if self.submit:
            def finished(result, error):
                if not self.retired:
                    callback(result, error)
            self.submit(action, params, finished)
            return
        try:
            if action != "activity_edit":
                raise ValueError("This action requires a connected Prism window")
            current = read_document(self.source, self.policy)
            if current != self.document or params["revision"] != current["revision"]:
                raise ValueError("Revision conflict; reload the current card")
            card = next(c for c in current["cards"] if c["id"] == params["card_id"])
            card["html" if card["kind"] == "text" else "strokes"] = params["content"]
            current["revision"] += 1
            current["last_editor"] = "operator"
            _validate(current, self.policy)
            write_private(self.source, current)
            callback({"revision": current["revision"]}, None)
        except (OSError, ValueError, StopIteration) as exc:
            callback(None, str(exc))

    def open_topic(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open activity topic", str(self.source.parent), "Activity documents (*.json)")
        if path:
            try:
                window = ActivityWindow(path, self.state_dir, self.policy)
                self.other_windows.append(window)
                window.show()
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Cannot open topic", str(exc))

    def export_picture(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save current view", self.document["id"] + ".png", "PNG image (*.png)")
        if path and not self.grab().save(path, "PNG"):
            QMessageBox.warning(self, "Picture not saved", "Check the destination and try again.")

    def observe_view_later(self):
        if self.on_view and not self.suppress_view:
            def observed():
                if self.on_view and not self.suppress_view and not self.closing_view:
                    self.on_view("visible" if self.isVisible() and not self.isMinimized() else "hidden",
                                 self.document["revision"])
            QTimer.singleShot(0, observed)

    def showEvent(self, event):
        super().showEvent(event)
        self.closing_view = False
        self.observe_view_later()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.observe_view_later()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.observe_view_later()

    def closeEvent(self, event):
        self.layout_timer.stop()
        self.save_layout()
        if self.on_view and not self.suppress_view and self.on_view("closed", self.document["revision"]) is False:
            event.ignore()
            return
        self.closing_view = True
        event.accept()


def launch_activity(paths, state_dir, policy=None, screenshot=None):
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setFont(QFont("Sans Serif", 10))
    app.setStyleSheet(STYLE)
    windows = [ActivityWindow(path, state_dir, policy) for path in paths]
    for i, window in enumerate(windows):
        window.move(window.x() + i * 32, window.y() + i * 32)
        window.show()
    if screenshot:
        def capture():
            ok = windows[0].grab().save(str(screenshot), "PNG")
            for window in windows:
                window.close()
            app.exit(0 if ok else 1)
        QTimer.singleShot(500, capture)
    return app.exec()


class ActivityManager:
    """Track actual Qt presentation separately from requested views and operator intent."""
    def __init__(self, supervisor):
        self.supervisor = supervisor
        self.windows, self.sequences, self.observed_sequences = {}, {}, {}
        self.boards = []
        self.pending, self.dismissed, self.inflight, self.reported = {}, {}, {}, {}
        self.reopen_waiting, self.reopen_requests = set(), {}
        self.generations = {}
        self.stopping = False
        self.local_error = False
        self.policy_error = ""
        self.state_path = Path(supervisor.root) / "prism-view-state.json"
        self.read_state()

    def read_state(self):
        try:
            if not self.state_path.exists():
                return
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or not isinstance(saved.get("dismissed"), dict) or not isinstance(saved.get("pending"), dict):
                raise ValueError("Invalid saved Prism view state")  # noqa: TRY004 -- private state validation
            if any(not isinstance(key, str) or type(seq) is not int or seq < 0 for key, seq in saved["dismissed"].items()):
                raise ValueError("Invalid saved Prism dismissal")
            pending = {}
            for key, request in saved["pending"].items():
                if (not isinstance(request, dict) or request.get("id") != key
                        or request.get("state") not in {"closed", "visible", "hidden"}
                        or type(request.get("revision")) is not int or request["revision"] < 1
                        or type(request.get("view_seq")) is not int or request["view_seq"] < 0
                        or not isinstance(request.get("request_id"), str) or not request["request_id"]):
                    raise ValueError("Invalid saved Prism presentation request")
                if request["state"] == "closed":
                    pending[key] = {name: request[name] for name in ("id", "state", "revision", "view_seq", "request_id")}
            self.dismissed, self.pending = saved["dismissed"], pending
        except (OSError, ValueError, TypeError) as exc:
            self.local_error = True
            self.supervisor.status.setText("Prism saved view state unavailable; choose a board explicitly: " + str(exc))

    def persist(self):
        try:
            write_private(self.state_path, {"dismissed": self.dismissed, "pending": self.pending})
            self.local_error = False
            return True
        except (OSError, ValueError) as exc:
            self.supervisor.status.setText("Prism view state was not saved: " + str(exc))
            return False

    def submit(self, action, params, callback):
        client = self.supervisor.client
        if client is None:
            callback(None, "VISOR is disconnected")
            return
        self.supervisor.job(lambda: client.call(action, **params), callback)

    def board(self, key):
        return next((board for board in self.boards if board["id"] == key), None)

    def record_view(self, key, state, revision):
        if self.stopping:
            return True
        window = self.windows.get(key)
        board = self.board(key)
        if window is None and (state != "closed" or not board or not board.get("view_state", {}).get("operator_dismissed")):
            return True
        sequence = window.view_sequence if window else board["view_seq"]
        fingerprint = (state, revision, sequence)
        previous = self.pending.get(key)
        if previous and previous["state"] == "closed" and state == "closed":
            self.flush(key)
            return True
        if state != "closed" and (key in self.dismissed or previous and previous["state"] == "closed"):
            return True
        if (previous and (previous["state"], previous["revision"], previous["view_seq"]) == fingerprint
                or self.reported.get(key) == fingerprint):
            self.flush(key)
            return True
        old_dismissed = self.dismissed.get(key)
        request = {"id": key, "state": state, "revision": revision, "view_seq": sequence, "request_id": uuid.uuid4().hex}
        self.pending[key] = request
        if state == "closed":
            self.dismissed[key] = sequence
        if not self.persist():
            if previous is None:
                self.pending.pop(key, None)
            else:
                self.pending[key] = previous
            if old_dismissed is None:
                self.dismissed.pop(key, None)
            else:
                self.dismissed[key] = old_dismissed
            return False
        self.flush(key)
        self.update_indicator()
        return True

    def flush(self, key):
        if self.stopping or key in self.inflight or key not in self.pending or self.supervisor.client is None:
            return
        request = copy.deepcopy(self.pending[key])
        self.inflight[key] = request["request_id"]
        generation = self.generations.get(key, 0)
        def finished(result, error):
            if generation != self.generations.get(key, 0) or self.stopping:
                return
            self.inflight.pop(key, None)
            current = self.pending.get(key)
            if current is None or current["request_id"] != request["request_id"]:
                self.flush(key)
                return
            if (error or not isinstance(result, dict) or result.get("id") != key
                    or type(result.get("accepted")) is not bool or type(result.get("view_seq")) is not int
                    or not isinstance(result.get("view_state"), dict)):
                self.supervisor.status.setText("Prism presentation report unconfirmed; exact retry retained.")
                return
            self.pending.pop(key, None)
            if result["view_seq"] >= self.observed_sequences.get(key, -1):
                self.observed_sequences[key] = result["view_seq"]
                if result["view_state"].get("operator_dismissed"):
                    self.dismissed[key] = result["view_seq"]
                elif result["view_state"].get("last_operator_open_seq", 0) > self.dismissed.get(key, -1):
                    self.dismissed.pop(key, None)
                self.boards = [{**board, "view_seq": result["view_seq"], "view_state": result["view_state"]}
                               if board["id"] == key else board for board in self.boards]
            if result["accepted"]:
                self.reported[key] = (request["state"], request["revision"], request["view_seq"])
            self.persist()
            self.update_indicator()
            if key in self.reopen_waiting:
                self.reopen_waiting.discard(key)
                board = self.board(key)
                if board:
                    self.operator_open(board)
        self.submit("activity_view", request, finished)

    def hold_policy(self, error):
        self.policy_error = "Prism policy unavailable: " + str(error)
        for window in self.windows.values():
            window.apply_policy(None, self.policy_error)
        self.supervisor.status.setText(self.policy_error)

    def update(self, boards, policy=None):
        if self.stopping:
            return
        try:
            if not isinstance(policy, dict) or policy.keys() != {field.name for field in fields(ActivityPolicy)}:
                raise ValueError("A complete validated activity_policy snapshot is required")
            validated = ActivityPolicy(**policy)
        except (ValueError, TypeError) as exc:
            self.hold_policy(exc)
            return
        self.policy = validated
        self.policy_error = ""
        for window in self.windows.values():
            if window.policy != validated or window.policy_error:
                window.apply_policy(validated)
        present = {board["id"] for board in boards}
        # A successful complete inventory can close missing projections, but only
        # an explicit deletion receipt permits discarding local layout and drafts.
        for key in list(self.windows):
            if key not in present:
                self.remove_window(key)
        previous = {board["id"]: board for board in self.boards}
        boards = [previous.get(board["id"], board)
                  if board["view_seq"] < self.observed_sequences.get(board["id"], -1) else board for board in boards]
        self.boards = list(boards)
        for board in boards:
            if board.get("projection_error"):
                self.supervisor.status.setText("Prism projection unavailable: " + board["projection_error"])
                continue
            key, sequence = board["id"], board["view_seq"]
            if sequence < self.observed_sequences.get(key, -1):
                continue
            self.observed_sequences[key] = sequence
            state = board.get("view_state", {})
            changed = False
            pending = self.pending.get(key)
            operator_open = state.get("last_operator_open_seq", 0)
            if pending and (operator_open > pending["view_seq"]
                            or pending["state"] != "closed" and sequence > pending["view_seq"]):
                self.pending.pop(key, None)
                changed = True
            if state.get("operator_dismissed"):
                if self.dismissed.get(key) != sequence:
                    self.dismissed[key] = sequence
                    changed = True
            elif key in self.dismissed and operator_open > self.dismissed[key] and key not in self.pending:
                self.dismissed.pop(key, None)
                changed = True
            if changed:
                self.persist()
            window = self.windows.get(key)
            if window:
                window.view_sequence = sequence
                window.poll_document()
                if not state.get("operator_dismissed") and state.get("presentation") in {"unknown", "requested"}:
                    self.reported.pop(key, None)
                    window.observe_view_later()
            self.flush(key)
            if key in self.dismissed or self.local_error:
                if window and window.isVisible():
                    self.hide_window(window)
                    if state.get("operator_dismissed"):
                        self.record_view(key, "closed", window.document["revision"])
                elif window is None and state.get("operator_dismissed") and state.get("presentation") != "closed":
                    self.record_view(key, "closed", board["revision"])
                self.sequences[key] = sequence
                continue
            if self.sequences.get(key) == sequence:
                continue
            self.sequences[key] = sequence
            try:
                action = board["view_action"]
                if action in {"open", "focus"}:
                    self.open(board, focus=True)
                elif window:
                    if action == "clear":
                        window.clear_view()
                        window.observe_view_later()
                    elif action == "close":
                        self.hide_window(window)
                        self.record_view(key, "hidden", window.document["revision"])
            except (OSError, ValueError) as exc:
                self.supervisor.status.setText("Prism window unavailable: " + str(exc))
                self.sequences.pop(key, None)
        self.update_indicator()

    def open(self, board, focus=True):
        key = board["id"]
        if key not in self.windows:
            self.windows[key] = ActivityWindow(board["path"], Path(self.supervisor.root) / "prism-layouts",
                                              getattr(self, "policy", ActivityPolicy()), submit=self.submit,
                                              on_view=lambda state, revision: self.record_view(key, state, revision))
        window = self.windows[key]
        if self.policy_error:
            window.apply_policy(None, self.policy_error)
        window.view_sequence = board["view_seq"]
        window.poll_document()
        window.showNormal() if window.isMinimized() else window.show()
        if focus:
            window.raise_()
            window.activateWindow()
        window.observe_view_later()
        return window

    def operator_open(self, board):
        if self.stopping:
            return
        key = board["id"]
        if self.pending.get(key, {}).get("state") == "closed":
            self.reopen_waiting.add(key)
            self.flush(key)
            return
        request = self.reopen_requests.setdefault(key, {"id": key, "action": "open", "request_id": uuid.uuid4().hex})
        if request.get("sending"):
            return
        request["sending"] = True
        generation = self.generations.get(key, 0)
        def finished(result, error):
            if generation != self.generations.get(key, 0) or self.stopping:
                return
            request.pop("sending", None)
            if (error or not isinstance(result, dict) or result.get("id") != key
                    or type(result.get("view_seq")) is not int):
                self.supervisor.status.setText("Prism reopen unconfirmed; choose it again to retry the same request.")
                return
            sequence = result["view_seq"]
            if sequence < self.observed_sequences.get(key, -1):
                return
            self.observed_sequences[key] = sequence
            self.dismissed.pop(key, None)
            self.pending.pop(key, None)
            self.persist()
            accepted = {**board, **result}
            self.sequences[key] = sequence
            self.boards = [accepted if item["id"] == key else item for item in self.boards]
            projection = result.get("projection", {})
            if projection.get("state") == "pending":
                accepted["projection_error"] = projection.get("error", "Projection pending")
                self.supervisor.status.setText("Prism reopen accepted; display update pending. Choose it again to retry repair.")
                self.update_indicator()
                return
            def present(current, failure):
                if generation != self.generations.get(key, 0) or self.stopping:
                    return
                if failure:
                    self.supervisor.status.setText("Prism reopen accepted; display unavailable: " + str(failure))
                    return
                self.reopen_requests.pop(key, None)
                current.pop("projection_error", None)
                self.boards = [current if item["id"] == key else item for item in self.boards]
                try:
                    self.open(current)
                except (OSError, ValueError) as exc:
                    self.supervisor.status.setText("Prism reopen unavailable: " + str(exc))
                self.update_indicator()
            if "path" not in accepted or accepted.get("projection_error"):
                self.submit("activity_get", {"id": key}, present)
            else:
                present(accepted, None)
        self.submit("activity_control", {name: request[name] for name in ("id", "action", "request_id")}, finished)

    @staticmethod
    def markers(board):
        state = board.get("view_state", {})
        return ([label for field, label in (("unshown_revision", "Updated"), ("reopen_requested", "Reopen requested")) if state.get(field)]
                + (["Display pending"] if board.get("projection_error") else []))

    def update_indicator(self):
        indicator = getattr(self.supervisor, "prism_button", None)
        if indicator is not None:
            count = sum(bool(self.markers(board)) for board in self.boards)
            indicator.setText(f"Prism ({count})" if count else "Prism")
            indicator.setToolTip(f"{count} saved boards have updates or reopen requests. Choose a board to open it." if count else "Open a saved Prism board")

    def choose(self):
        if not self.boards:
            QMessageBox.information(self.supervisor, "Prism windows", "No Prism windows yet. Enable Prism access from a session's menu, then ask that session to publish a board.")
            return
        labels = [board["title"] + " · " + board["id"]
                  + (" · " + ", ".join(self.markers(board)) if self.markers(board) else "") for board in self.boards]
        label, accepted = QInputDialog.getItem(self.supervisor, "Prism windows", "Open a shared workspace", labels, 0, False)
        if accepted:
            self.operator_open(self.boards[labels.index(label)])

    @staticmethod
    def hide_window(window):
        window.suppress_view = True
        try:
            window.close()
        finally:
            window.suppress_view = False

    def remove_window(self, key):
        self.generations[key] = self.generations.get(key, 0) + 1
        self.inflight.pop(key, None)
        window = self.windows.pop(key, None)
        if window:
            window.save_layout()
            window.retired = True
            window.on_view = None
            window.submit = None
            self.hide_window(window)
            window.watch.stop()
            window.receipt_timer.stop()
            window.layout_timer.stop()
            window.on_view = None
            # Python cycles may be collected by a worker; destroy Qt objects on their owner thread.
            window.deleteLater()
        self.sequences.pop(key, None)
        self.reported.pop(key, None)

    def deleted(self, key):
        """Call only after an authenticated, exact activity_delete success receipt."""
        self.remove_window(key)
        self.pending.pop(key, None)
        self.dismissed.pop(key, None)
        self.reopen_waiting.discard(key)
        self.reopen_requests.pop(key, None)
        self.boards = [board for board in self.boards if board["id"] != key]
        path = Path(self.supervisor.root) / "prism-layouts" / (key + ".layout.json")
        if path.parent == Path(self.supervisor.root) / "prism-layouts":
            path.unlink(missing_ok=True)
        if not self.persist():
            raise OSError("Deleted Prism view closed, but local presentation state cleanup failed")
        self.update_indicator()

    def bind(self, session):
        def finished(result, error):
            if error:
                QMessageBox.warning(self.supervisor, "Prism access", str(error))
                return
            box = QMessageBox(self.supervisor)
            box.setWindowTitle("Prism access enabled")
            box.setText("This session can now use Prism without restarting.")
            box.setInformativeText("Use VISOR --activity with --session " + session + ". This grant is limited to Prism. Shared boards require approved session connections.")
            box.setDetailedText("Credential file (private): " + result["credential_file"])
            box.exec()
        self.submit("activity_bind", {"session": session, "confirmed": True}, finished)

    def close(self):
        self.stopping = True
        for key in list(self.windows):
            self.remove_window(key)
