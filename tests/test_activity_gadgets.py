import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication

from visor.activity_components import KINDS, component
from visor.activity_document import ActivityPolicy
from visor.activity_gadgets import DirectActions, create_component
from visor.prism_theme import apply_theme


class ActivityGadgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.policy = ActivityPolicy()
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def make(self, card, events=None):
        widget = create_component(card, None, self.policy,
                                  on_event=events.append if events is not None else None)
        self.widgets.append(widget)
        widget.resize(340, 260)
        widget.show()
        self.app.processEvents()
        return widget

    def test_every_component_renders_and_live_updates_without_emitting(self):
        events = []
        for kind in sorted(KINDS):
            with self.subTest(kind=kind):
                widget = self.make(component(kind), events)
                self.assertFalse(widget.grab().isNull())
                widget.set_card(component(kind, title="Updated name"))
                self.app.processEvents()
                self.assertFalse(widget.grab().isNull())
        self.assertEqual(events, [])

    def test_actions_stay_outside_the_painted_surface_at_narrow_and_wide_sizes(self):
        card = component("gauge", actions=[{"id": "check-" + str(i), "label": "Inspect check " + str(i)}
                                            for i in range(8)])
        widget = self.make(card, [])
        for width in [180, 300, 500, 220]:
            with self.subTest(width=width):
                widget.resize(width, 260)
                self.app.processEvents()
                self.assertLess(widget.content.geometry().bottom(), widget.actions.geometry().top())
                button_rectangles = []
                for button in widget.actions.buttons:
                    self.assertTrue(button.isVisible())
                    top_left = button.mapTo(widget, QPoint(0, 0))
                    rectangle = button.rect().translated(top_left)
                    self.assertFalse(widget.content.geometry().intersects(rectangle))
                    self.assertTrue(widget.rect().contains(rectangle))
                    self.assertFalse(any(other.intersects(rectangle) for other in button_rectangles))
                    button_rectangles.append(rectangle)

    def test_native_controls_send_only_declared_operator_intent(self):
        events = []
        action = self.make(component("action"), events)
        QTest.mouseClick(action.control, Qt.MouseButton.LeftButton)
        toggle = self.make(component("toggle"), events)
        QTest.mouseClick(toggle.control, Qt.MouseButton.LeftButton, pos=QPoint(7, toggle.control.height() // 2))
        slider = self.make(component("slider", min=0, max=1, step=.1, value=.2), events)
        slider.control.setValue(6)
        self.assertEqual(len(events), 2, "Slider preview must not emit an event")
        QTest.mouseClick(slider.send_button, Qt.MouseButton.LeftButton)
        select = self.make(component("select"), events)
        select.control.setCurrentIndex(2)
        self.assertEqual(len(events), 3, "Programmatic selection must not emit an event")
        select.control.activated.emit(2)
        self.assertEqual(events[:2], [{"action_id": "view-checks", "value": None},
                                      {"action_id": "change-preview", "value": True}])
        self.assertEqual(events[-1], {"action_id": "choose-stage", "value": "Pilot"})
        self.assertAlmostEqual(events[2]["value"], .6)

    def test_preview_and_invalid_policy_disable_operator_controls(self):
        preview = self.make(component("action"))
        self.assertFalse(preview.control.isEnabled())
        events = []
        widget = self.make(component("slider", actions=[{"id": "inspect", "label": "Inspect"}]), events)
        widget.set_policy(replace(self.policy, max_text_chars=3))
        self.app.processEvents()
        self.assertFalse(widget.control.isEnabled())
        self.assertFalse(widget.actions.buttons[0].isEnabled())
        self.assertTrue(widget.notice.isVisible())
        widget.set_policy(self.policy)
        self.app.processEvents()
        self.assertTrue(widget.control.isEnabled())
        self.assertTrue(widget.actions.buttons[0].isEnabled())
        self.assertFalse(widget.notice.isVisible())
        self.assertEqual(events, [])

    def test_gadget_text_lines_do_not_intersect_at_the_minimum_size(self):
        for kind in ["gauge", "meter", "progress", "stat", "sparkline", "timer", "indicator", "arrow"]:
            with self.subTest(kind=kind):
                widget = self.make(component(kind, detail="Concise project status"))
                canvas = widget.content
                canvas.resize(160, 150)
                lines = []
                draw_text = canvas._text

                def record(painter, rectangle, text, *args, lines=lines, draw_text=draw_text, **kwargs):
                    if text:
                        lines.append(rectangle)
                    return draw_text(painter, rectangle, text, *args, **kwargs)

                canvas._text = record
                canvas.grab()
                for index, rectangle in enumerate(lines):
                    self.assertGreaterEqual(rectangle.top(), 0)
                    self.assertLessEqual(rectangle.bottom(), canvas.height())
                    self.assertFalse(any(other.intersects(rectangle) for other in lines[:index]))

    def test_running_timer_does_not_restart_on_an_unrelated_board_update(self):
        card = component("timer", running=True)
        canvas = self.make(card).content
        started = canvas.started
        canvas.set_card({**card, "detail": "Another card was updated"})
        self.assertEqual(canvas.started, started)
        self.assertTrue(canvas.clock.isActive())
        canvas.set_card({**card, "seconds": 600})
        self.assertGreaterEqual(canvas.started, started)

    def test_theme_switch_preserves_widget_identity_and_preview_draft(self):
        events = []
        widget = self.make(component("slider"), events)
        control = widget.control
        control.setValue(73)
        card = widget.card.copy()
        for name in ["soft-light", "calm-dark", "soft-light"]:
            self.assertEqual(apply_theme(widget, name, "QWidget { color:#d4dee7; background:#17232d; }"), name)
            self.app.processEvents()
            self.assertIs(widget.control, control)
            self.assertEqual(control.value(), 73)
            self.assertEqual(widget.card, card)
            self.assertEqual(widget.property("prismTheme"), name)
            self.assertFalse(widget.grab().isNull())
        self.assertEqual(events, [])
        with self.assertRaises(ValueError):
            apply_theme(widget, "unknown")

    def test_legacy_action_row_and_table_selection_are_native(self):
        events = []
        row = DirectActions({"kind": "text", "actions": [{"id": "inspect", "label": "Inspect"}]},
                            self.policy, on_event=events.append)
        self.widgets.append(row)
        row.show()
        self.app.processEvents()
        QTest.mouseClick(row.buttons[0], Qt.MouseButton.LeftButton)
        self.assertEqual(events, [{"action_id": "inspect", "value": None}])
        table = self.make(component("table")).content
        self.assertEqual(table.selectionMode(), QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setFocus()
        QTest.keyClick(table, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(table.selectionModel().selectedRows()), 2)


if __name__ == "__main__":
    unittest.main()
