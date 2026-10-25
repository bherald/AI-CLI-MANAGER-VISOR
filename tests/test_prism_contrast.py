"""Prism must stay readable when opened inside the real dark supervisor theme."""
import json
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QLabel

from visor.activity import ActivityWindow
from visor.ui import STYLE as SUPERVISOR_STYLE


def luminance(rgb):
    channels = [value / 255 for value in rgb]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
              for value in channels]
    return sum(value * factor for value, factor in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))


class PrismContrastCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.previous_style = self.app.styleSheet()
        self.app.setStyleSheet(SUPERVISOR_STYLE)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.window = None

    def tearDown(self):
        if self.window is not None:
            self.window.watch.stop()
            self.window.layout_timer.stop()
            self.window.close()
            self.window.deleteLater()
        self.app.processEvents()
        self.app.setStyleSheet(self.previous_style)
        self.temp.cleanup()

    def open(self):
        source = self.root / "synthetic-board.json"
        source.write_text(json.dumps({
            "schema_version": 1, "id": "contrast-board", "title": "Shared Prism workspace",
            "revision": 1, "parent_record": "Example discussion",
            "session_link": {"id": "codex:synthetic", "label": "Example session"},
            "cards": [
                {"id": "notes", "kind": "text", "title": "Typed notes",
                 "html": '<p style="background-color:#ffffff;color:#000000;padding:20px">Write a practical next step.</p>',
                 "caption": "Example contributor notes",
                 "geometry": [10, 10, 440, 390]},
                {"id": "question", "kind": "question", "title": "Discussion question",
                 "prompt": "Which useful improvement should we try next?",
                 "caption": "Example question details", "choices": ["Sketch", "Discuss"],
                 "geometry": [470, 10, 500, 430]},
                {"id": "drawing", "kind": "sketch", "title": "Drawing pad", "canvas": [200, 100],
                 "strokes": [{"points": [[20, 50], [180, 50]], "color": "#111b24", "width": 5}],
                 "geometry": [10, 430, 440, 220]},
            ],
        }), encoding="utf-8")
        self.window = ActivityWindow(source, self.root / "layouts")
        self.window.resize(1200, 800)
        self.window.show()
        self.app.processEvents()
        self.window.watch.stop()
        self.window.layout_timer.stop()
        return self.window

    def assert_rendered_label_readable(self, label, screenshot):
        self.assertTrue(label.isVisible())
        self.assertTrue(label.text())
        position = label.mapTo(self.window, QPoint(0, 0))
        ratio = screenshot.devicePixelRatio()
        region = screenshot.copy(round(position.x() * ratio), round(position.y() * ratio),
                                 round(label.width() * ratio), round(label.height() * ratio))
        pixels = Counter(region.pixel(x, y) & 0xFFFFFF
                         for y in range(region.height()) for x in range(region.width()))
        background = pixels.most_common(1)[0][0]
        background_rgb = ((background >> 16) & 255, (background >> 8) & 255, background & 255)
        foreground = label.palette().color(QPalette.ColorRole.WindowText)
        foreground_rgb = (foreground.red(), foreground.green(), foreground.blue())
        bg_luminance, fg_luminance = luminance(background_rgb), luminance(foreground_rgb)
        self.assertLess(bg_luminance, 0.08, f"Bright background in the muted Prism theme: {label.text()!r}")
        contrast = (max(bg_luminance, fg_luminance) + 0.05) / (min(bg_luminance, fg_luminance) + 0.05)
        self.assertGreaterEqual(contrast, 4.5, f"Insufficient rendered contrast for {label.text()!r}")
        # Palette contrast alone is insufficient: verify text-colored pixels were actually drawn.
        ink_pixels = sum(count for pixel, count in pixels.items()
                         if max(abs(((pixel >> shift) & 255) - channel)
                                for shift, channel in zip((16, 8, 0), foreground_rgb, strict=True)) <= 20)
        self.assertGreater(ink_pixels, 5, f"No visible text drawn for {label.text()!r}")

    def test_labels_render_muted_and_readable_under_actual_supervisor_theme(self):
        window = self.open()
        notes, question = window.cards["notes"], window.cards["question"]
        labels = {
            "heading": window.heading,
            "metadata": window.metadata,
            "status": window.status,
            "eyebrow": window.findChild(QLabel, "eyebrow"),
            "question": question.content,
            "question_caption": question.caption,
            "reply_status": question.response_status,
            "note_caption": notes.caption,
            "edit_status": notes.edit_status,
        }
        screenshot = window.grab().toImage()
        for name, label in labels.items():
            with self.subTest(label=name):
                self.assert_rendered_label_readable(label, screenshot)
        self.assertGreaterEqual(window.metadata.font().pixelSize(), 12)
        self.assertGreaterEqual(window.status.font().pixelSize(), 12)
        self.assertEqual(self.app.styleSheet(), SUPERVISOR_STYLE)

    def test_text_and_drawing_surfaces_do_not_flash_white(self):
        window = self.open()
        surfaces = {
            "formatted_notes": window.cards["notes"].content.viewport(),
            "drawing_canvas": window.cards["drawing"].content,
            "answer_editor": window.cards["question"].reply.viewport(),
        }
        for name, widget in surfaces.items():
            with self.subTest(surface=name):
                rendered = widget.grab().toImage()
                pixels = Counter(rendered.pixel(x, y) & 0xFFFFFF
                                 for y in range(rendered.height()) for x in range(rendered.width()))
                background = pixels.most_common(1)[0][0]
                rgb = ((background >> 16) & 255, (background >> 8) & 255, background & 255)
                self.assertLess(luminance(rgb), 0.08, f"Bright {name} background")
                bright_pixels = sum(count for pixel, count in pixels.items()
                                    if luminance(((pixel >> 16) & 255, (pixel >> 8) & 255, pixel & 255)) > 0.8)
                self.assertLess(bright_pixels / (rendered.width() * rendered.height()), 0.02,
                                f"A large white patch remains in {name}")
        pad = window.cards["drawing"].content
        rendered = pad.grab().toImage()
        ink = rendered.pixelColor(rendered.width() // 2, rendered.height() // 2)
        background = pad.canvas_color
        ink_luminance = luminance((ink.red(), ink.green(), ink.blue()))
        bg_luminance = luminance((background.red(), background.green(), background.blue()))
        contrast = (max(ink_luminance, bg_luminance) + 0.05) / (min(ink_luminance, bg_luminance) + 0.05)
        self.assertGreaterEqual(contrast, 3.0, "Dark stored ink disappears on the dark drawing pad")
        self.assertEqual(pad.strokes[0]["color"], "#111b24", "Rendering must preserve original stroke data")

    def test_chalkboard_is_slightly_lighter_than_its_frame(self):
        pad = self.open().cards["drawing"].content
        image = pad.grab().toImage()
        bounds, _ = pad._bounds()
        canvas = image.pixelColor(int(bounds.left()) + 5, int(bounds.top()) + 5)
        frame = image.pixelColor(0, 0)
        canvas_lum = luminance((canvas.red(), canvas.green(), canvas.blue()))
        frame_lum = luminance((frame.red(), frame.green(), frame.blue()))
        self.assertGreater(canvas_lum, frame_lum + .01)
        self.assertLess(canvas_lum, .08)
