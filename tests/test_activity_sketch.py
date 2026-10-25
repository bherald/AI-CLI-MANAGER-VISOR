"""Sketch schema, bounded pointer input and native rendering acceptance."""
import copy
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import QApplication

from visor.activity_document import ActivityPolicy, _validate
from visor.activity_sketch import SketchPad


def card():
    return {"id": "drawing", "kind": "sketch", "title": "Draw here",
            "canvas": [200, 100], "strokes": []}


def document(value):
    return {"schema_version": 1, "id": "topic", "title": "Topic", "revision": 1,
            "cards": [value]}


def stroke(points=None):
    return {"points": points or [[30, 40]], "color": "#147d80", "width": 4}


class SketchValidationCase(unittest.TestCase):
    def test_empty_canvas_and_single_point_are_valid(self):
        value = card()
        _validate(document(value), ActivityPolicy())
        value["strokes"] = [stroke(), stroke([[0, 0], [200, 100]])]
        self.assertEqual(_validate(document(value), ActivityPolicy())["cards"][0], value)

    def test_rejects_invalid_dimensions_coordinates_and_styles(self):
        variants = []
        for canvas in ([0, 100], [1e-320, 100], [201], [float("inf"), 100], [100001, 100], [True, 100]):
            value = card()
            value["canvas"] = canvas
            variants.append(value)
        for points in ([[201, 50]], [[-1, 5]], [[30]], [[float("nan"), 50]], [[30, True]]):
            value = card()
            value["strokes"] = [stroke(points)]
            variants.append(value)
        for key, content in (("points", []), ("color", "url(file:///secret)"), ("color", "red"),
                             ("width", 0), ("width", 101), ("width", 10 ** 1000)):
            value = card()
            value["strokes"] = [stroke()]
            value["strokes"][0][key] = content
            variants.append(value)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                _validate(document(value), ActivityPolicy())

    def test_total_point_and_stroke_limits_apply_across_strokes(self):
        value = card()
        value["strokes"] = [stroke([[1, 1], [2, 2]]), stroke([[3, 3], [4, 4]])]
        _validate(document(value), ActivityPolicy(max_drawing_points=4))
        with self.assertRaisesRegex(ValueError, "point limit"):
            _validate(document(value), ActivityPolicy(max_drawing_points=3))
        with self.assertRaisesRegex(ValueError, "strokes"):
            _validate(document(value), ActivityPolicy(max_items_per_card=1))


class SketchPointerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pad = SketchPad(card())
        self.pad.resize(412, 212)
        self.pad.show()
        self.app.processEvents()
        self.changed = []
        self.pad.changed.connect(lambda: self.changed.append(True))

    def tearDown(self):
        self.pad.close()
        self.pad.deleteLater()
        self.app.processEvents()

    def event(self, kind, x, y, button=Qt.MouseButton.LeftButton, buttons=None):
        if buttons is None:
            buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else button
        event = QMouseEvent(kind, QPointF(x, y), QPointF(x, y), button, buttons,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.pad, event)

    def test_pointer_stroke_clamps_to_canvas_and_survives_resize(self):
        self.event(QEvent.Type.MouseButtonPress, 26, 26)
        self.event(QEvent.Type.MouseMove, 900, -40, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        self.event(QEvent.Type.MouseButtonRelease, 900, -40)
        points = self.pad.strokes[0]["points"]
        self.assertEqual(points, [[10.0, 10.0], [200, 0.0]])
        self.assertTrue(self.pad.dirty)
        self.assertEqual(len(self.changed), 2)
        snapshot = self.pad.strokes
        self.pad.resize(812, 412)
        self.app.processEvents()
        self.assertEqual(snapshot, self.pad.strokes)
        _validate(document({**card(), "strokes": self.pad.strokes}), self.pad.policy)

    def test_single_click_paints_a_dot_and_snapshot_is_detached(self):
        self.pad.color = "#b02030"
        self.pad.pen_width = 8
        self.event(QEvent.Type.MouseButtonPress, 206, 106)
        self.event(QEvent.Type.MouseButtonRelease, 206, 106)
        self.app.processEvents()
        self.assertEqual(self.pad.strokes[0]["points"], [[100.0, 50.0]])
        image = self.pad.grab().toImage()
        self.assertEqual(image.pixelColor(206, 106).name(), "#b02030")
        self.assertEqual(image.pixelColor(40, 40).name(), "#ffffff")
        snapshot = self.pad.strokes
        snapshot[0]["points"][0][0] = 0
        self.assertEqual(self.pad.strokes[0]["points"][0][0], 100.0)

    def test_undo_clear_and_set_card_do_not_alias_source(self):
        value = card()
        value["strokes"] = [stroke(), stroke([[60, 50]])]
        self.pad.set_card(value)
        self.assertFalse(self.pad.dirty)
        value["strokes"][0]["points"][0][0] = 1
        self.assertEqual(self.pad.strokes[0]["points"][0][0], 30)
        self.pad.undo()
        self.assertEqual(len(self.pad.strokes), 1)
        self.pad.clear()
        self.assertEqual(self.pad.strokes, [])
        count = len(self.changed)
        self.pad.clear()
        self.assertEqual(count, len(self.changed))
        self.assertTrue(self.pad.dirty)
        self.pad.set_card(card())
        self.assertFalse(self.pad.dirty)

    def test_point_cap_and_stroke_cap_preserve_valid_drawing(self):
        self.pad.policy = ActivityPolicy(max_drawing_points=2, max_items_per_card=1)
        self.event(QEvent.Type.MouseButtonPress, 20, 20)
        self.event(QEvent.Type.MouseMove, 30, 30, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        self.event(QEvent.Type.MouseMove, 40, 40, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        self.event(QEvent.Type.MouseButtonRelease, 50, 50)
        self.event(QEvent.Type.MouseButtonPress, 60, 60)
        self.event(QEvent.Type.MouseButtonRelease, 60, 60)
        self.assertEqual(len(self.pad.strokes), 1)
        self.assertEqual(len(self.pad.strokes[0]["points"]), 2)
        _validate(document({**card(), "strokes": self.pad.strokes}), self.pad.policy)
        self.pad.undo()
        self.event(QEvent.Type.MouseButtonPress, 60, 60)
        self.event(QEvent.Type.MouseButtonRelease, 60, 60)
        self.assertEqual(len(self.pad.strokes), 1)

    def test_letterbox_right_click_and_invalid_brush_do_not_mutate(self):
        self.pad.resize(412, 412)
        self.event(QEvent.Type.MouseButtonPress, 20, 20)
        self.event(QEvent.Type.MouseButtonRelease, 20, 20)
        self.event(QEvent.Type.MouseButtonPress, 200, 200, Qt.MouseButton.RightButton)
        self.event(QEvent.Type.MouseButtonRelease, 200, 200, Qt.MouseButton.RightButton)
        self.pad.color = "invalid"
        self.event(QEvent.Type.MouseButtonPress, 200, 200)
        self.event(QEvent.Type.MouseButtonRelease, 200, 200)
        self.assertEqual(self.pad.strokes, [])
        self.assertFalse(self.pad.dirty)

    def test_invalid_update_keeps_previous_canvas(self):
        previous = copy.deepcopy(self.pad.canvas)
        value = card()
        value["canvas"] = [0, 0]
        with self.assertRaises(ValueError):
            self.pad.set_card(value)
        self.assertEqual(self.pad.canvas, previous)
        self.assertEqual(QColor(self.pad.color).name(), "#147d80")
