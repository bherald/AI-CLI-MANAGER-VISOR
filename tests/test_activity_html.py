"""Actual native activation and shared HTML boundaries, using synthetic boards only."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QTextCursor, QTextTable
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from visor.activity import ActivityWindow, safe_html
from visor.activity_html import safe_link
from visor.prism_web import safe_html as web_safe_html

HTML = ('<div style="font-size:9pt;color:#17364c">'
        '<table width="100%" border="1" bordercolor="#cbd5e1" cellspacing="0" cellpadding="4">'
        '<tr bgcolor="#dceef3"><th width="35%" style="text-align:left;padding:3px 6px">Project</th>'
        '<th width="18%">Status</th><th width="47%">Next</th></tr>'
        '<tr><td bgcolor="#ffffff"><a href="https://example.test/details?item=project%3Aone">'
        'Open details</a></td><td bgcolor="#ecfeff">Reported</td>'
        '<td bgcolor="#ffffff">Review source evidence.</td></tr></table></div>')


class ActivityHTMLCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = None

    def tearDown(self):
        if self.window:
            self.window.watch.stop()
            self.window.layout_timer.stop()
            self.window.close()
            self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def open(self, value=HTML):
        root = Path(self.temp.name)
        source = root / "board.json"
        source.write_text(json.dumps({
            "schema_version": 1, "id": "synthetic-details", "title": "Project details",
            "revision": 1, "session_link": {"id": "codex:fixture", "label": "Fixture"},
            "cards": [{"id": "overview", "kind": "text", "title": "Overview",
                       "html": value, "geometry": [5, 5, 760, 500]}],
        }))
        self.window = ActivityWindow(source, root / "layouts")
        self.window.resize(900, 650)
        self.window.show()
        self.app.processEvents()
        self.window.watch.stop()
        self.window.layout_timer.stop()
        return self.window.cards["overview"].content

    def test_native_and_web_preserve_valid_links_and_table_format(self):
        native, web = safe_html(HTML), web_safe_html(HTML)
        self.assertEqual(native, web)
        for value in ('href="https://example.test/', 'width="100%"', 'border="1"',
                      'bgcolor="#ffffff"', 'color:#17364c', 'padding:3px 6px'):
            self.assertIn(value, native)

    def test_real_mouse_click_opens_exact_http_target_only_on_activation(self):
        with patch("PySide6.QtGui.QDesktopServices.openUrl", return_value=True) as opener:
            browser = self.open()
            self.assertFalse(browser.openLinks())
            self.assertFalse(browser.openExternalLinks())
            opener.assert_not_called()
            point = next((QPoint(x, y) for y in range(browser.viewport().height())
                          for x in range(browser.viewport().width())
                          if browser.anchorAt(QPoint(x, y))), None)
            self.assertIsNotNone(point, "Rendered anchor must exist, not just stored HTML")
            QTest.mouseClick(browser.viewport(), Qt.MouseButton.LeftButton, pos=point)
            self.app.processEvents()
            opener.assert_called_once()
            self.assertEqual(opener.call_args.args[0].toString(),
                             "https://example.test/details?item=project%3Aone")
            self.assertTrue(browser.source().isEmpty())

    def test_keyboard_activation_and_handler_revalidation(self):
        with patch("PySide6.QtGui.QDesktopServices.openUrl", return_value=True) as opener:
            browser = self.open('<p><a href="http://example.test/keyboard">Keyboard details</a></p>')
            browser.setFocus()
            browser.moveCursor(QTextCursor.MoveOperation.Start)
            QTest.keyClick(browser, Qt.Key.Key_Tab)
            QTest.keyClick(browser, Qt.Key.Key_Return)
            self.app.processEvents()
            opener.assert_called_once()
            self.assertEqual(opener.call_args.args[0].toString(), "http://example.test/keyboard")
            opener.reset_mock()
            for value in ("javascript:alert(1)", "file:///tmp/private", ("https://" "user:pass@example.test")):
                browser.anchorClicked.emit(QUrl(value))
            opener.assert_not_called()

    def test_credential_query_and_fragment_never_activate_but_ordinary_queries_do(self):
        blocked = (
            "https://example.test/path?access_token=SYNTHETIC_PLACEHOLDER",
            "https://example.test/path?%61ccess%5Ftoken=SYNTHETIC_PLACEHOLDER",
            "https://example.test/path?X-Amz-Signature=SYNTHETIC_PLACEHOLDER",
            "https://example.test/path#token=SYNTHETIC_PLACEHOLDER",
            "https://example.test/path#/route?client_secret=SYNTHETIC_PLACEHOLDER",
        )
        with patch("PySide6.QtGui.QDesktopServices.openUrl", return_value=True) as opener:
            browser = self.open("")
            for value in blocked:
                with self.subTest(url=value):
                    browser.setHtml(safe_html(f'<p><a href="{value}">Credential link</a></p>'))
                    self.app.processEvents()
                    cursor = browser.document().find("Credential")
                    cursor.setPosition(cursor.selectionStart() + 2)
                    point = browser.cursorRect(cursor).center()
                    QTest.mouseClick(browser.viewport(), Qt.MouseButton.LeftButton, pos=point)
                    self.app.processEvents()
                    opener.assert_not_called()
                    self.assertFalse(browser.anchorAt(point))
                    # The activation boundary also rejects stale/forged anchors.
                    browser.anchorClicked.emit(QUrl(value))
                    opener.assert_not_called()
                    self.assertIsNone(safe_link(value))
            ordinary = "https://example.test/details?item=project%3Aone&q=tokenization&page=2#section-1"
            browser.setHtml(safe_html(f'<p><a href="{ordinary}">Ordinary link</a></p>'))
            self.app.processEvents()
            cursor = browser.document().find("Ordinary")
            cursor.setPosition(cursor.selectionStart() + 2)
            QTest.mouseClick(browser.viewport(), Qt.MouseButton.LeftButton, pos=browser.cursorRect(cursor).center())
            self.app.processEvents()
            opener.assert_called_once()
            self.assertEqual(opener.call_args.args[0].toString(), ordinary)

    def test_native_document_contains_rendered_border_color_width_and_padding(self):
        browser = self.open()
        tables = [frame for frame in browser.document().rootFrame().childFrames()
                  if isinstance(frame, QTextTable)]
        self.assertEqual(len(tables), 1)
        table = tables[0]
        self.assertEqual(table.format().border(), 1)
        self.assertEqual(table.format().borderBrush().color().name(), "#cbd5e1")
        self.assertEqual(table.format().cellPadding(), 4)
        self.assertEqual(table.format().width().rawValue(), 100)
        self.assertEqual(table.cellAt(1, 0).format().background().color().name(), "#ffffff")
        self.assertEqual(table.cellAt(0, 0).format().toTableCellFormat().leftPadding(), 6)

    def test_shared_sanitizer_rejects_active_resources_ambiguous_urls_and_unbounded_styles(self):
        rejected = ("javascript:alert(1)", "data:text/html,x", "file:///tmp/private", "qrc:/x",
                    "//example.test/x", "/relative", ("https://" "user:pass@example.test/x"),
                    "https://user%40example.test/x", "https://example.test:99999/x",
                    " https://example.test", "https://example.test/\npath",
                    "https://example.test/%0apath", "https:\\example.test")
        for value in rejected:
            with self.subTest(url=value):
                self.assertIsNone(safe_link(value))
                self.assertNotIn("href=", safe_html(f'<a href="{value}">Label</a>'))
        value = ('<svg><foreignObject><p>HIDDEN</p></foreignObject></svg>'
                 '<script>HIDDEN</script><iframe srcdoc="HIDDEN">HIDDEN</iframe>'
                 '<img src="https://example.test/track"><video src="file:///private"></video>'
                 '<a href="https://example.test/?a=1&amp;b=2" onclick="evil()" '
                 'ping="https://example.test/track" download target="opener">Safe link</a>'
                 '<table border="999" cellpadding="100" width="999999%" '
                 'style="background-image:url(https://example.test);position:fixed;'
                 'color:expression(evil());padding:999px;border:9px solid #ffffff">'
                 '<tr><td bgcolor="red" style="color:#123456;unknown:x">Readable</td></tr></table>')
        native = safe_html(value)
        self.assertEqual(native, web_safe_html(value))
        for forbidden in ("HIDDEN", "<img", "<video", "<script", "<svg", "<iframe", "onclick",
                          "ping=", "download", "opener\"", "url(", "expression", "position",
                          "999", "padding:100", "border:9", 'bgcolor="red"', "unknown:"):
            self.assertNotIn(forbidden, native)
        self.assertIn('rel="noopener noreferrer"', native)
        self.assertIn('target="_blank"', native)
        self.assertIn('href="https://example.test/?a=1&amp;b=2"', native)
        self.assertIn("color:#123456", native)

    def test_sanitizer_is_idempotent_and_preserves_editor_style_roundtrip(self):
        browser = self.open()
        clean = safe_html(browser.document().toHtml())
        self.assertEqual(safe_html(clean), clean)
        self.assertIn('href="https://example.test/', clean)
        self.assertIn("#cbd5e1", clean)


if __name__ == "__main__":
    unittest.main()
