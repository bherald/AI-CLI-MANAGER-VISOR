"""Native table layout, stable source identity, local controls and actual paint."""
import copy
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel, QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QTableWidget

from visor.activity_document import ActivityPolicy
from visor.activity_gadgets import create_component
from visor.activity_table_widget import AdvancedTable, _contrast, _readable
from visor.prism_theme import apply_theme


class ActivityTableWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widgets = []
        self.card = {"id": "checks", "title": "Review checks", "kind": "table",
                     "columns": ["Check", "Count"], "rows": [["Alpha", 30], ["Bravo", 2], ["Charlie", 10]],
                     "row_ids": ["alpha", "bravo", "charlie"]}

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def make(self, card=None, width=360, height=280):
        widget = create_component(card or self.card, None, ActivityPolicy())
        self.widgets.append(widget)
        widget.resize(width, height)
        widget.show()
        self.app.processEvents()
        self.app.processEvents()
        return widget

    def select(self, table, rows):
        table.clearSelection()
        for row in rows:
            table.selectionModel().select(table.model().index(row, 0),
                                          QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)

    def test_unstyled_cards_remain_plain_native_tables_without_controls(self):
        widget = self.make()
        table = widget.content
        self.assertIsInstance(table, QTableWidget)
        self.assertIsInstance(table, AdvancedTable)
        self.assertTrue(table.toolbar.isHidden())
        self.assertFalse(table.isSortingEnabled())
        self.assertEqual([table.item(row, 0).text() for row in range(3)], ["Alpha", "Bravo", "Charlie"])
        self.assertFalse(table.grab().isNull())

    def test_numeric_sort_and_copy_use_visible_order_and_original_values(self):
        card = {**self.card, "table_style": {"sortable": True, "copyable": True}}
        table = self.make(card).content
        self.assertEqual([table.item(row, 1).text() for row in range(3)], ["30", "2", "10"])
        table.sortItems(1, Qt.SortOrder.AscendingOrder)
        self.assertEqual([table.item(row, 1).text() for row in range(3)], ["2", "10", "30"])
        self.select(table, [0, 2])
        self.assertEqual(table.selected_source_rows(), [1, 0])
        self.assertEqual(table.copy_selected(), "Bravo\t2\nAlpha\t30")
        self.assertEqual(self.app.clipboard().text(), "Bravo\t2\nAlpha\t30")
        self.app.clipboard().setText("")
        table.setFocus()
        QTest.keyClick(table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.app.clipboard().text(), "Bravo\t2\nAlpha\t30")

    def test_filter_ctrl_a_and_copy_exclude_hidden_rows(self):
        card = {**self.card, "table_style": {"filterable": True, "copyable": True, "sortable": True}}
        table = self.make(card).content
        table.filter.setText("bravo")
        self.assertEqual([table.isRowHidden(row) for row in range(3)], [True, False, True])
        table.setFocus()
        QTest.keyClick(table, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(table.selected_source_rows(), [1])
        self.assertEqual(table.copy_selected(), "Bravo\t2")
        table.sortItems(1, Qt.SortOrder.AscendingOrder)
        self.assertEqual([table.item(row, 0).text() for row in range(3) if not table.isRowHidden(row)], ["Bravo"])
        self.assertEqual(table.copy_selected(), "Bravo\t2")
        table.filter.clear()
        self.assertEqual(table.copy_selected(), "Bravo\t2")

    def test_live_refresh_preserves_ids_selection_scroll_width_sort_and_filter(self):
        card = {**self.card, "rows": [[f"Row {index:03}", index] for index in range(100)],
                "row_ids": ["row-" + str(index) for index in range(100)],
                "table_style": {"filterable": True, "sortable": True, "copyable": True}}
        widget = self.make(card, height=240)
        table = widget.content
        table.setColumnWidth(0, 450)
        table.sortItems(1, Qt.SortOrder.DescendingOrder)
        table.filter.setText("Row")
        self.select(table, [30, 40])
        selected_names = {table.item(row, 0).text() for row in [30, 40]}
        table.verticalScrollBar().setValue(45)
        table.horizontalScrollBar().setValue(80)
        scroll = (table.horizontalScrollBar().value(), table.verticalScrollBar().value())
        spy_selection, spy_cell, spy_filter = QSignalSpy(table.itemSelectionChanged), QSignalSpy(table.cellChanged), QSignalSpy(table.filter.textChanged)
        newer = copy.deepcopy(card)
        newer["rows"].reverse()
        newer["row_ids"].reverse()
        widget.set_card(newer)
        self.app.processEvents()
        self.app.processEvents()
        self.assertIs(widget.content, table)
        self.assertEqual(table.columnWidth(0), 450)
        self.assertEqual(table.filter.text(), "Row")
        self.assertEqual(table.item(0, 1).text(), "99")
        self.assertEqual({newer["rows"][row][0] for row in table.selected_source_rows()}, selected_names)
        self.assertEqual((table.horizontalScrollBar().value(), table.verticalScrollBar().value()), scroll)
        self.assertEqual((spy_selection.count(), spy_cell.count(), spy_filter.count()), (0, 0, 0))

    def test_row_ids_keep_selection_even_if_selected_values_change(self):
        table = self.make().content
        self.select(table, [0, 2])
        newer = copy.deepcopy(self.card)
        newer["rows"] = [["Charlie updated", 20], ["Bravo", 2], ["Alpha updated", 31]]
        newer["row_ids"] = ["charlie", "bravo", "alpha"]
        table.set_card(newer)
        self.assertEqual(table.selected_source_rows(), [0, 2])
        self.assertEqual({table.item(index.row(), 0).text() for index in table.selectionModel().selectedRows()},
                         {"Charlie updated", "Alpha updated"})

    def test_fallback_duplicate_identity_and_column_removal_preserve_valid_selection(self):
        card = {**self.card, "rows": [["Alpha", 30], ["Bravo", 2], ["Alpha", 30]]}
        card.pop("row_ids")
        table = self.make(card).content
        self.select(table, [2])
        table.set_card({**card, "rows": [["Alpha", 30], ["Alpha", 30], ["Bravo", 2]]})
        self.assertEqual(table.selected_source_rows(), [1])
        table.set_card({**self.card, "columns": ["Check"], "rows": [["Alpha"], ["Bravo"], ["Charlie"]]})
        self.app.processEvents()
        self.assertEqual(table.columnCount(), 1)
        self.assertFalse(table.grab().isNull())

    def test_wrapped_headers_and_thick_borders_reserve_text_space(self):
        card = {**self.card, "columns": ["A longer wrapped project heading", "Count"],
                "table_style": {"header": {"wrap": True, "font_size": 18, "padding": 0},
                                "columns": [{"column": 0, "width": 110}],
                                "cells": [{"row": 0, "column": 0, "font_size": 18, "padding": 0,
                                           "border": {"width": 8, "color": "#e04040"}}]}}
        table = self.make(card).content
        self.assertGreater(table.horizontalHeader().height(), 45)
        self.assertGreaterEqual(table.rowHeight(0), 18 + 16)
        self.assertFalse(table.grab().isNull())

    def test_header_and_cell_borders_paint_on_original_source_coordinates_after_sort(self):
        card = {**self.card, "table_style": {"sortable": True, "grid": "none",
                "header": {"background": "#24374a", "text_color": "#eff5ff", "font_size": 18,
                           "padding": 7, "border": {"color": "#4080e0", "width": 3}},
                "cells": [{"row": 0, "column": 1, "background": "#101820", "text_color": "#101820",
                           "badge": "warning", "bold": True, "border": {"color": "#e04040", "width": 3}}]}}
        table = self.make(card).content
        table.sortItems(1, Qt.SortOrder.AscendingOrder)
        self.app.processEvents()
        target = table.item(2, 1)
        rectangle = table.visualItemRect(target)
        image = table.viewport().grab().toImage()
        self.assertEqual(image.pixelColor(rectangle.left() + 1, rectangle.center().y()).name(), "#e04040")
        style = table.itemDelegate()._style(table.model().index(2, 1))
        self.assertEqual(style["badge"], "warning")
        header = table.horizontalHeader()
        self.assertGreaterEqual(header.height(), 36)
        header_image = header.viewport().grab().toImage()
        self.assertEqual(header_image.pixelColor(1, header_image.height() // 2).name(), "#4080e0")
        self.assertGreaterEqual(_contrast(_readable(QColor("#101820"), QColor("#101820")), QColor("#101820")), 4.5)

    def test_toolbar_is_reserved_above_grid_and_actions_below_at_narrow_widths(self):
        card = {**self.card, "table_style": {"filterable": True, "copyable": True},
                "actions": [{"id": "review", "label": "Review selected evidence"}]}
        widget = self.make(card)
        table = widget.content
        for width in [180, 260, 400]:
            with self.subTest(width=width):
                widget.resize(width, 280)
                self.app.processEvents()
                self.app.processEvents()
                self.assertLess(table.toolbar.geometry().bottom(), table.geometry().top())
                self.assertLess(table.geometry().bottom(), widget.actions.geometry().top())
                rectangles = [child.geometry().translated(child.mapTo(table.toolbar, QPoint(0, 0)) - child.pos())
                              for child in [table.filter, table.count, table.copy_button] if child.isVisible()]
                for index, rectangle in enumerate(rectangles):
                    self.assertTrue(table.toolbar.rect().contains(rectangle))
                    self.assertFalse(any(rectangle.intersects(other) for other in rectangles[:index]))

    def test_theme_change_retains_local_state_and_tooltips_escape_active_markup(self):
        card = {**self.card, "table_style": {"filterable": True, "copyable": True,
                "cells": [{"row": 0, "column": 0, "tooltip": '<img src="file:///private/secret">'}]}}
        widget = self.make(card)
        table = widget.content
        table.filter.setText("alpha")
        self.select(table, [0])
        self.assertIn("&lt;img", table.item(0, 0).toolTip())
        for name in ["soft-light", "calm-dark"]:
            apply_theme(widget, name)
            self.app.processEvents()
            self.assertEqual(table.filter.text(), "alpha")
            self.assertEqual(table.selected_source_rows(), [0])
            self.assertFalse(table.grab().isNull())


if __name__ == "__main__":
    unittest.main()
