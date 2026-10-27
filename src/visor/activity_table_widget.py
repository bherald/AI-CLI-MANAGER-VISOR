"""Portable Prism table styling with local sorting/filtering/selection only."""
from __future__ import annotations

import copy
import html
import math

from PySide6.QtCore import (
    QItemSelection,
    QItemSelectionModel,
    QPointF,
    QRectF,
    QSignalBlocker,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from .activity_table import cell_style, copy_tsv, display_cell, row_keys, table_options

_SOURCE = int(Qt.ItemDataRole.UserRole) + 1
_ROW_KEY = int(Qt.ItemDataRole.UserRole) + 2
_VALUE = int(Qt.ItemDataRole.UserRole) + 3
_ALIGN = {"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter,
          "right": Qt.AlignmentFlag.AlignRight}
_PENS = {"solid": Qt.PenStyle.SolidLine, "dashed": Qt.PenStyle.DashLine,
         "dotted": Qt.PenStyle.DotLine}
_BADGES = {"neutral": "#8195aa", "info": "#67b5d7", "success": "#64bf9c",
           "warning": "#dfb85e", "error": "#df8a8a", "working": "#6abbb4",
           "waiting": "#b4a0d7", "offline": "#99a4ad"}


def _contrast(first, second):
    def luminance(color):
        values = [part / 12.92 if part <= .04045 else ((part + .055) / 1.055) ** 2.4
                  for part in color.getRgbF()[:3]]
        return sum(value * factor for value, factor in zip(values, (.2126, .7152, .0722), strict=True))
    a, b = luminance(first), luminance(second)
    return (max(a, b) + .05) / (min(a, b) + .05)


def _readable(color, background):
    if _contrast(color, background) >= 4.5:
        return color
    black, white = QColor("#101820"), QColor("#ffffff")
    return black if _contrast(black, background) >= _contrast(white, background) else white


def _font(widget, style):
    font = QFont(widget.font())
    if "font_size" in style:
        font.setPixelSize(style["font_size"])
    if "bold" in style:
        font.setBold(style["bold"])
    return font


def _line_width(style):
    border = style.get("border", {})
    return 0 if border.get("style") == "none" else border.get("width", 1)


def _badge(painter, rectangle, text, style, background, foreground, selected=False):
    accent = QColor(_BADGES[style["badge"]])
    fill = background if selected or "background" in style else QColor(
        *(round(a * .18 + b * .82) for a, b in zip(accent.getRgb()[:3], background.getRgb()[:3], strict=True)))
    width = max(1, min(rectangle.width(), painter.fontMetrics().horizontalAdvance(text) + 16))
    height = max(1, min(rectangle.height(), painter.fontMetrics().height() + 6))
    left = rectangle.left() if style.get("align", "left") == "left" else rectangle.right() - width if style.get("align") == "right" else rectangle.center().x() - width / 2
    pill = QRectF(left, rectangle.center().y() - height / 2, width, height)
    painter.setPen(QPen(_readable(accent, fill), 1))
    painter.setBrush(fill)
    painter.drawRoundedRect(pill, min(8, height / 2), min(8, height / 2))
    painter.setPen(_readable(foreground, fill))
    text = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(1, int(width) - 12))
    painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)


def _border(painter, rectangle, style, grid, palette):
    explicit = bool(style.get("border"))
    border = style.get("border", {})
    width, pattern = border.get("width", 1), border.get("style", "solid")
    if width == 0 or pattern == "none":
        return
    grid_color = "#506378" if palette.base().color().lightness() < 128 else "#9baea5"
    painter.setPen(QPen(QColor(border.get("color", grid_color)), width, _PENS[pattern]))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    inset = width / 2
    rectangle = QRectF(rectangle).adjusted(inset, inset, -inset, -inset)
    if explicit:
        painter.drawRect(rectangle)
    else:
        if grid in {"all", "horizontal"}:
            painter.drawLine(rectangle.bottomLeft(), rectangle.bottomRight())
        if grid in {"all", "vertical"}:
            painter.drawLine(rectangle.topRight(), rectangle.bottomRight())


class _TypedItem(QTableWidgetItem):
    def __lt__(self, other):
        def key(item):
            value = item.data(_VALUE)
            if type(value) in (int, float) and math.isfinite(value):
                return (0, value)
            return (2, "") if value is None else (1, display_cell(value).casefold())
        return key(self) < key(other)


class _CellDelegate(QStyledItemDelegate):
    def __init__(self, table):
        super().__init__(table)
        self.table = table

    def _style(self, index):
        source = index.data(_SOURCE)
        return cell_style(self.table.card, source, index.column()) if source is not None else {}

    def sizeHint(self, option, index):
        style = self._style(index)
        padding = style.get("padding", self.table.padding) + _line_width(style)
        option.font = _font(self.table, style)
        metrics = QFontMetrics(option.font)
        width = max(20, self.table.columnWidth(index.column()) - 2 * padding - 4)
        text = str(index.data() or "")
        height = metrics.boundingRect(0, 0, width, 100000, Qt.TextFlag.TextWordWrap, text).height() if style.get("wrap", False) else metrics.height()
        return QSize(metrics.horizontalAdvance(text) + 2 * padding + 6, max(metrics.height(), height) + 2 * padding + 2)

    def paint(self, painter, option, index):
        painter.save()
        painter.setClipRect(option.rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        style = self._style(index)
        palette = self.table.palette()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        striped = self.table.options["striped"] and index.row() % 2 == 1
        background = QColor(style.get("background", palette.alternateBase().color() if striped else palette.base().color()))
        if selected:
            background = palette.highlight().color()
        foreground = palette.highlightedText().color() if selected else QColor(style.get("text_color", palette.text().color()))
        foreground = _readable(foreground, background)
        painter.fillRect(option.rect, background)
        painter.setFont(_font(self.table, style))
        padding = style.get("padding", self.table.padding) + _line_width(style)
        rectangle = QRectF(option.rect).adjusted(padding + 1, padding, -padding - 1, -padding)
        alignment = _ALIGN[style.get("align", "left")] | Qt.AlignmentFlag.AlignVCenter
        text = str(index.data() or "")
        if style.get("badge"):
            _badge(painter, rectangle, text, style, background, foreground, selected)
        else:
            painter.setPen(foreground)
            if style.get("wrap", False):
                painter.drawText(rectangle, int(alignment) | int(Qt.TextFlag.TextWordWrap), text)
            else:
                text = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(1, int(rectangle.width())))
                painter.drawText(rectangle, alignment, text)
        _border(painter, option.rect, style, self.table.options["grid"], palette)
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(_readable(palette.highlightedText().color(), background), 1, Qt.PenStyle.DotLine))
            painter.drawRect(QRectF(option.rect).adjusted(2, 2, -2, -2))
        painter.restore()


class _Header(QHeaderView):
    def __init__(self, table):
        super().__init__(Qt.Orientation.Horizontal, table)
        self.table = table
        self.setSectionsClickable(True)
        self.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.setMinimumSectionSize(40)

    def sizeHint(self):
        result = super().sizeHint()
        heights = []
        for column in range(min(self.table.columnCount(), len(self.table.card["columns"]))):
            style = cell_style(self.table.card, -1, column, header=True)
            font = _font(self, style)
            metrics = QFontMetrics(font)
            padding = style.get("padding", self.table.padding) + _line_width(style)
            width = max(20, self.sectionSize(column) - 2 * padding - 20)
            text = self.table.card["columns"][column]
            height = metrics.boundingRect(0, 0, width, 100000, Qt.TextFlag.TextWordWrap, text).height() if style.get("wrap", False) else metrics.height()
            heights.append(height + 2 * padding + 4)
        return QSize(result.width(), max([result.height(), *heights]))

    def paintSection(self, painter, rectangle, logical):
        if logical < 0 or logical >= min(self.table.columnCount(), len(self.table.card["columns"])):
            return
        painter.save()
        painter.setClipRect(rectangle)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        style = cell_style(self.table.card, -1, logical, header=True)
        palette = self.palette()
        background = QColor(style.get("background", palette.button().color()))
        foreground = _readable(QColor(style.get("text_color", palette.buttonText().color())), background)
        painter.fillRect(rectangle, background)
        painter.setPen(foreground)
        painter.setFont(_font(self, style))
        padding = style.get("padding", self.table.padding) + _line_width(style)
        text_rect = QRectF(rectangle).adjusted(padding + 1, padding, -padding - 1, -padding)
        sorting = self.table.isSortingEnabled() and self.sortIndicatorSection() == logical
        if sorting:
            text_rect.adjust(0, 0, -16, 0)
        item = self.table.horizontalHeaderItem(logical)
        text = item.text() if item else self.table.card["columns"][logical]
        alignment = _ALIGN[style.get("align", "left")] | Qt.AlignmentFlag.AlignVCenter
        if style.get("badge"):
            _badge(painter, text_rect, text, style, background, foreground)
        elif style.get("wrap", False):
            painter.drawText(text_rect, int(alignment) | int(Qt.TextFlag.TextWordWrap), text)
        else:
            text = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(1, int(text_rect.width())))
            painter.drawText(text_rect, alignment, text)
        if sorting:
            center = QPointF(rectangle.right() - 9, rectangle.center().y())
            down = self.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
            path = QPainterPath(center + QPointF(-4, -2 if down else 2))
            path.lineTo(center + QPointF(4, -2 if down else 2))
            path.lineTo(center + QPointF(0, 3 if down else -3))
            path.closeSubpath()
            painter.setBrush(foreground)
            painter.drawPath(path)
        _border(painter, rectangle, style, self.table.options["grid"], palette)
        painter.restore()


class AdvancedTable(QTableWidget):
    """QTableWidget-compatible display; toolbar is a separate reserved sibling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.card = {"columns": [], "rows": []}
        self.options = table_options(self.card)
        self.padding = 6
        self._updating = False
        self._widths = {}
        self._column_keys = []
        self._sort_key = None
        self._sort_order = Qt.SortOrder.AscendingOrder
        self._wrap_columns = set()
        self._wrap_headers = False
        self._scroll_position = (0, 0)
        self.scroll_timer = QTimer(self)
        self.scroll_timer.setSingleShot(True)
        self.scroll_timer.timeout.connect(self._restore_scroll)
        self.rows_timer = QTimer(self)
        self.rows_timer.setSingleShot(True)
        self.rows_timer.setInterval(50)
        self.rows_timer.timeout.connect(self._refresh_row_geometry)
        self.setHorizontalHeader(_Header(self))
        self.setItemDelegate(_CellDelegate(self))
        self.horizontalHeader().sectionResized.connect(self._resized)
        self.horizontalHeader().sortIndicatorChanged.connect(self._sorted)
        self.model().layoutChanged.connect(self._layout_changed)
        self.itemSelectionChanged.connect(self._selection_changed)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setShowGrid(False)
        self.verticalHeader().hide()
        self.setMinimumSize(120, 100)
        self.toolbar = QWidget(parent)
        row = QHBoxLayout(self.toolbar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        self.filter = QLineEdit(self.toolbar)
        self.filter.setMinimumWidth(60)
        self.filter.setPlaceholderText("Filter rows")
        self.filter.setAccessibleName("Filter table rows")
        self.filter.textChanged.connect(self._apply_filter)
        row.addWidget(self.filter, 1)
        self.count = QLabel(self.toolbar)
        self.count.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.count)
        self.copy_button = QPushButton("Copy rows", self.toolbar)
        self.copy_button.setAccessibleName("Copy selected visible table rows")
        self.copy_button.clicked.connect(self.copy_selected)
        row.addWidget(self.copy_button)
        self.toolbar.hide()

    @staticmethod
    def _columns(columns):
        seen = {}
        result = []
        for label in columns:
            seen[label] = seen.get(label, 0) + 1
            result.append((label, seen[label]))
        return result

    def _resized(self, logical, _old, size):
        if not self._updating and logical < len(self._column_keys):
            self._widths[self._column_keys[logical]] = size
            if logical in self._wrap_columns or self._wrap_headers:
                self.rows_timer.start()

    def _refresh_row_geometry(self):
        self.resizeRowsToContents()
        self.horizontalHeader().updateGeometry()
        self.updateGeometries()

    def _sorted(self, logical, order):
        if not self._updating and 0 <= logical < len(self._column_keys):
            self._sort_key = self._column_keys[logical]
            self._sort_order = order

    def _layout_changed(self):
        if not self._updating:
            self._apply_filter()

    def set_card(self, card):
        selected = {self.item(index.row(), 0).data(_ROW_KEY) for index in self.selectionModel().selectedRows()}
        current = self.currentItem()
        focus = (current.data(_ROW_KEY), current.column()) if current is not None else None
        self._scroll_position = (self.horizontalScrollBar().value(), self.verticalScrollBar().value())
        blockers = [QSignalBlocker(self), QSignalBlocker(self.selectionModel()), QSignalBlocker(self.horizontalHeader())]
        self._updating = True
        try:
            self.setSortingEnabled(False)
            self.card = copy.deepcopy(card)
            self.rows_timer.stop()
            self._wrap_columns = set()
            self._wrap_headers = False
            self.options = table_options(card)
            self.padding = 3 if self.options["density"] == "compact" else 6
            self._column_keys = self._columns(card["columns"])
            self.clearContents()
            self.setRowCount(len(card["rows"]))
            self.setColumnCount(len(card["columns"]))
            self.setHorizontalHeaderLabels(card["columns"])
            identities = row_keys(card)
            for row, cells in enumerate(card["rows"]):
                for column, value in enumerate(cells):
                    item = _TypedItem(display_cell(value))
                    item.setData(_SOURCE, row)
                    item.setData(_ROW_KEY, identities[row])
                    item.setData(_VALUE, value)
                    style = cell_style(card, row, column)
                    if style.get("wrap", False):
                        self._wrap_columns.add(column)
                    tooltip = style.get("tooltip", display_cell(value))
                    item.setToolTip("<qt>" + html.escape(tooltip).replace("\n", "<br>") + "</qt>")
                    item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, tooltip)
                    self.setItem(row, column, item)
            widths = {entry["column"]: entry["width"] for entry in self.options["columns"] if "width" in entry}
            for column, key in enumerate(self._column_keys):
                header_style = cell_style(card, -1, column, header=True)
                self._wrap_headers |= header_style.get("wrap", False)
                tooltip = header_style.get("tooltip", card["columns"][column])
                self.horizontalHeaderItem(column).setToolTip("<qt>" + html.escape(tooltip) + "</qt>")
                if key in self._widths:
                    width = self._widths[key]
                elif column in widths:
                    width = widths[column]
                else:
                    self.resizeColumnToContents(column)
                    width = min(240, max(80, self.columnWidth(column)))
                self.setColumnWidth(column, width)
            self.resizeRowsToContents()
            self.horizontalHeader().setSortIndicator(-1, self._sort_order)
            self.setSortingEnabled(self.options["sortable"])
            if self.options["sortable"] and self._sort_key in self._column_keys:
                self.sortItems(self._column_keys.index(self._sort_key), self._sort_order)
            self._apply_filter()
            selection = QItemSelection()
            for row in range(self.rowCount()):
                key = self.item(row, 0).data(_ROW_KEY)
                if key in selected:
                    selection.select(self.model().index(row, 0), self.model().index(row, self.columnCount() - 1))
                if focus and key == focus[0]:
                    self.selectionModel().setCurrentIndex(self.model().index(row, min(focus[1], self.columnCount() - 1)), QItemSelectionModel.SelectionFlag.NoUpdate)
            self.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            self.filter.setVisible(self.options["filterable"])
            self.count.setVisible(self.options["filterable"])
            self.copy_button.setVisible(self.options["copyable"])
            self.toolbar.setVisible(self.options["filterable"] or self.options["copyable"])
            border = self.options["border"]
            if border:
                color = border.get("color", self.palette().mid().color().name())
                self.setStyleSheet("QTableWidget { border:" + str(border.get("width", 1)) + "px " + border.get("style", "solid") + " " + color + "; }")
            else:
                self.setStyleSheet("")
            self.updateGeometries()
            self._restore_scroll()
            self.scroll_timer.start(0)
        finally:
            self._updating = False
            for blocker in blockers:
                blocker.unblock()
        self._selection_changed()
        self.horizontalHeader().updateGeometry()
        self.horizontalHeader().viewport().update()
        self.viewport().update()

    def _restore_scroll(self):
        horizontal, vertical = self._scroll_position
        self.horizontalScrollBar().setValue(horizontal)
        self.verticalScrollBar().setValue(vertical)

    def _apply_filter(self, _text=None):
        if not self.card["columns"]:
            return
        query = self.filter.text().casefold() if self.options["filterable"] else ""
        visible = 0
        for row in range(self.rowCount()):
            match = not query or any(query in self.item(row, column).text().casefold() for column in range(self.columnCount()))
            self.setRowHidden(row, not match)
            visible += match
        self.count.setText(f"{visible}/{self.rowCount()}")
        self.count.setAccessibleName("Visible table rows")
        self.count.setToolTip(f"{visible} visible of {self.rowCount()} rows")
        self._selection_changed()

    def _selection_changed(self):
        self.copy_button.setEnabled(self.options["copyable"] and bool(self.selected_source_rows()))

    def selected_source_rows(self):
        return [self.item(row, 0).data(_SOURCE) for row in sorted({index.row() for index in self.selectionModel().selectedRows()}) if not self.isRowHidden(row)]

    def copy_selected(self):
        if not self.options["copyable"]:
            return ""
        text = copy_tsv(self.card, self.selected_source_rows())
        if text:
            QApplication.clipboard().setText(text)
        return text

    def selectAll(self):
        selection = QItemSelection()
        for row in range(self.rowCount()):
            if not self.isRowHidden(row):
                selection.select(self.model().index(row, 0), self.model().index(row, self.columnCount() - 1))
        self.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy) and self.options["copyable"]:
            self.copy_selected()
            event.accept()
        elif event.matches(QKeySequence.StandardKey.SelectAll):
            self.selectAll()
            event.accept()
        else:
            super().keyPressEvent(event)
