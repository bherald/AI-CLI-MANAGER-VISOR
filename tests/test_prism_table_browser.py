"""Real browser acceptance for table styles, source coordinates and retained state."""
import base64
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from test_prism_web import FakeClient

from visor.activity_document import ActivityPolicy, _validate
from visor.prism_web import PrismWebServer


@unittest.skipUnless(shutil.which("google-chrome") or shutil.which("chromium"), "Chrome unavailable")
class PrismTableBrowserCase(unittest.TestCase):
    def test_portable_styles_selection_copy_refresh_and_card_help(self):
        from websockets.sync.client import connect

        cache = Path(__file__).resolve().parents[1] / ".cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="prism-table-browser-", dir=cache) as temporary:
            work = Path(temporary)
            client = FakeClient(work)
            rows = [["Security", 92, "Ready", True], ["Delivery", 45, "Review", False], ["Restore", 77, "Ready", None]]
            rows += [[f"Item {index}", index, "Queued", False] for index in range(30)]
            identities = ["security", "delivery", "restore"] + [f"item-{index}" for index in range(30)]
            table = {"id": "review-table", "kind": "table", "title": "Release review", "columns": ["Work", "Score", "Status", "Verified"],
                     "rows": rows, "row_ids": identities, "help": "Select rows to review. <b>Markup stays literal.</b>",
                     "data_status": {"state": "stale", "message": "Waiting for the next check", "source": "Synthetic quality feed", "observed_at": "2026-10-27T17:05:00+00:00"},
                     "table_style": {"density": "compact", "striped": True, "grid": "none", "sortable": True, "filterable": True, "copyable": True,
                                     "border": {"color": "#4b6278", "width": 1, "style": "solid"},
                                     "header": {"background": "#123456", "text_color": "#fedcba", "font_size": 15, "bold": True, "border": {"width": 2}},
                                     "columns": [{"column": 0, "width": 180, "wrap": False}, {"column": 1, "width": 100, "align": "right", "font_size": 18}],
                                     "rows": [{"row": 1, "background": "#312349"}],
                                     "cells": [{"row": 1, "column": 2, "background": "#725115", "text_color": "#fff4dc", "badge": "warning", "tooltip": "<b>Review this literally</b>", "border": {"width": 3, "style": "dashed", "color": "#ffaa00"}},
                                               {"row": 0, "column": 1, "border": {"width": 0}, "background": "#214638"}]}}
            client.board["document"]["cards"] = [table,
                {"id": "legacy", "kind": "table", "title": "Plain table", "columns": ["Choice", "Value"], "rows": [["One", True], ["Two", None]]},
                {"id": "notes", "kind": "text", "title": "Notes", "html": "<p>Canonical note</p>"}]
            (work / "board.json").write_text(json.dumps(client.board["document"]), encoding="utf-8")
            server = PrismWebServer(work, client=client).start()
            self.addCleanup(server.close)
            profile = work / "profile"
            process = subprocess.Popen([shutil.which("google-chrome") or shutil.which("chromium"), "--headless", "--no-sandbox", "--disable-gpu", "--no-first-run", "--disable-background-networking", "--remote-debugging-port=0", "--user-data-dir=" + str(profile), server.url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                active = profile / "DevToolsActivePort"
                deadline = time.monotonic() + 15
                while not active.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(active.exists())
                port = active.read_text().splitlines()[0]
                with build_opener(ProxyHandler({})).open(Request(f"http://127.0.0.1:{port}/json/list"), timeout=5) as response:
                    page = next(item for item in json.load(response) if item.get("type") == "page")
                with connect(page["webSocketDebuggerUrl"], open_timeout=5) as wire:
                    serial = 0

                    def command(method, params=None):
                        nonlocal serial
                        serial += 1
                        wire.send(json.dumps({"id": serial, "method": method, "params": params or {}}))
                        while True:
                            result = json.loads(wire.recv(timeout=10))
                            if result.get("id") == serial:
                                self.assertNotIn("error", result)
                                return result.get("result", {})

                    def evaluate(expression):
                        result = command("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
                        self.assertNotIn("exceptionDetails", result)
                        return result["result"].get("value")

                    def until(expression):
                        deadline = time.monotonic() + 8
                        while time.monotonic() < deadline:
                            if evaluate(expression):
                                return
                            time.sleep(.05)
                        self.fail("Browser did not reach: " + expression)

                    command("Emulation.setDeviceMetricsOverride", {"width": 1400, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
                    until("document.querySelectorAll('.rich-table').length===2")
                    evaluate("window.tableCard=()=>document.querySelector('[data-card=\"review-table\"]');window.rich=()=>tableCard().querySelector('.prism-table');window.filterRows=value=>{const input=rich().querySelector('input[type=search]');input.value=value;input.dispatchEvent(new Event('input'));};")
                    self.assertEqual(evaluate("getComputedStyle(rich().querySelector('thead th[data-source-column=\"0\"]')).backgroundColor"), "rgb(18, 52, 86)")
                    self.assertEqual(evaluate("getComputedStyle(rich().querySelector('thead th[data-source-column=\"0\"]')).borderLeftWidth"), "2px")
                    self.assertEqual(evaluate("getComputedStyle(rich().querySelector('tbody tr[data-source-row=\"0\"] td[data-source-column=\"1\"]')).borderLeftWidth"), "0px")
                    self.assertEqual(evaluate("getComputedStyle(rich().querySelector('tbody tr[data-source-row=\"1\"] td[data-source-column=\"2\"]')).borderLeftStyle"), "dashed")
                    self.assertEqual(evaluate("rich().querySelector('tbody tr[data-source-row=\"1\"] td[data-source-column=\"2\"]').title"), "<b>Review this literally</b>")
                    self.assertEqual(evaluate("rich().querySelector('.table-badge').dataset.state"), "warning")
                    self.assertEqual(evaluate("getComputedStyle(rich().querySelector('thead th[data-source-column=\"0\"]')).position"), "sticky")
                    self.assertLessEqual(evaluate("rich().querySelector('.table-viewport').clientHeight"), 420)
                    self.assertIn("Data may be out of date", evaluate("tableCard().querySelector('.card-data-status').innerText"))
                    self.assertNotIn("Synthetic quality feed", evaluate("document.body.innerText"))
                    evaluate("tableCard().querySelector('.card-help-button').click()")
                    self.assertEqual(evaluate("tableCard().querySelector('.card-help-button').getAttribute('aria-expanded')"), "true")
                    self.assertIn("<b>Markup stays literal.</b>", evaluate("tableCard().querySelector('.card-help-panel').textContent"))
                    self.assertEqual(evaluate("tableCard().querySelector('.card-help-panel').children.length"), 0)
                    evaluate("tableCard().querySelector('.card-help-button').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
                    self.assertTrue(evaluate("tableCard().querySelector('.card-help-panel').hidden"))
                    evaluate("filterRows('Ready');rich().querySelector('th[data-source-column=\"1\"] .table-sort').click()")
                    self.assertEqual(evaluate("[...rich().querySelectorAll('tbody tr')].map(row=>row.dataset.sourceRow)"), ["2", "0"])
                    evaluate("rich().querySelector('tbody tr').click();rich().querySelectorAll('tbody tr')[1].dispatchEvent(new MouseEvent('click',{bubbles:true,shiftKey:true}))")
                    self.assertEqual(evaluate("rich().querySelectorAll('tbody tr[aria-selected=true]').length"), 2)
                    evaluate("rich().querySelector('tbody tr').dispatchEvent(new MouseEvent('click',{bubbles:true,ctrlKey:true}))")
                    self.assertEqual(evaluate("rich().querySelectorAll('tbody tr[aria-selected=true]').length"), 1)
                    evaluate("rich().querySelector('tbody tr').dispatchEvent(new MouseEvent('click',{bubbles:true,metaKey:true}))")
                    self.assertEqual(evaluate("rich().querySelectorAll('tbody tr[aria-selected=true]').length"), 2)
                    copied = evaluate("(()=>{const data=new DataTransfer();rich().querySelector('.table-viewport').dispatchEvent(new ClipboardEvent('copy',{bubbles:true,cancelable:true,clipboardData:data}));return data.getData('text/plain');})()")
                    self.assertEqual(copied, "Restore\t77\tReady\t\nSecurity\t92\tReady\tYes")
                    self.assertTrue(evaluate("rich().querySelector('input[type=search]').dispatchEvent(new KeyboardEvent('keydown',{key:'a',ctrlKey:true,bubbles:true,cancelable:true}))"))
                    evaluate("rich().querySelector('[data-resize-column=\"1\"]').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true}))")
                    self.assertEqual(evaluate("rich().querySelectorAll('col')[2].style.width"), "110px")
                    evaluate("filterRows('e');const view=rich().querySelector('.table-viewport');view.scrollTop=120;view.scrollLeft=100;view.dispatchEvent(new Event('scroll'))")
                    self.assertGreater(evaluate("rich().querySelector('.table-viewport').scrollTop"), 0)
                    evaluate("const legacy=document.querySelector('[data-card=\"legacy\"]');legacy.querySelector('tbody tr').click();legacy.querySelector('.table-viewport').dispatchEvent(new KeyboardEvent('keydown',{key:'a',metaKey:true,bubbles:true,cancelable:true}))")
                    self.assertEqual(evaluate("document.querySelector('[data-card=\"legacy\"]').querySelectorAll('tbody tr[aria-selected=true]').length"), 2)
                    self.assertEqual(evaluate("document.querySelector('[data-card=\"legacy\"]').querySelectorAll('.table-toolbar,input[type=checkbox]').length"), 0)
                    evaluate("const notes=document.querySelector('[data-card=\"notes\"]');notes.querySelector('.card-options').open=true;notes.querySelector('.card-options .toolbar button').click();const editor=notes.querySelector('[contenteditable]');editor.innerHTML='<p>Unsent preserved text</p>';editor.dispatchEvent(new Event('input'))")
                    with client.lock:
                        table["rows"] = list(reversed(rows))
                        table["row_ids"] = list(reversed(identities))
                        table["rows"][-1][1] = 93
                        client.board["revision"] = 2
                        client.board["document"]["revision"] = 2
                    evaluate("document.getElementById('refresh').click()")
                    until("rich().querySelector('tbody').innerText.includes('93') && document.querySelector('[data-card=\"notes\"]').querySelectorAll('.edit-area').length===0")
                    self.assertEqual(evaluate("rich().querySelector('input[type=search]').value"), "e")
                    self.assertEqual(evaluate("rich().querySelectorAll('col')[2].style.width"), "110px")
                    self.assertEqual(evaluate("rich().querySelector('th[data-source-column=\"1\"]').getAttribute('aria-sort')"), "ascending")
                    self.assertEqual(evaluate("[...rich().querySelectorAll('tr[aria-selected=true]')].map(row=>row.querySelector('[data-source-column=\"0\"]').textContent).sort()"), ["Restore", "Security"])
                    self.assertGreater(evaluate("rich().querySelector('.table-viewport').scrollTop"), 0)
                    self.assertGreater(evaluate("rich().querySelector('.table-viewport').scrollLeft"), 0)
                    evaluate("const notes2=document.querySelector('[data-card=\"notes\"]');notes2.querySelector('.card-options').open=true;notes2.querySelector('.card-options .toolbar button').click()")
                    self.assertIn("Unsent preserved text", evaluate("document.querySelector('[data-card=\"notes\"] [contenteditable]').textContent"))
                    self.assertTrue(evaluate("[...document.querySelectorAll('[data-card=\"notes\"] .edit-area button')].find(button=>button.textContent==='Save text').disabled"))
                    evaluate("document.querySelector('[data-card=\"notes\"] .card-options').open=false")
                    self.assertEqual(evaluate("window.PrismTables.tsv([['a\\tb','a\\nb','a\"b',true,null]])"), '"a\tb"\t"a\nb"\t"a""b"\tYes\t')
                    self.assertEqual(evaluate("JSON.stringify(window.PrismTables.cellStyle({background:'url(https://invalid.example)',font_size:999,padding:40,border:{width:9,style:'url(x)'},tooltip:'<b>literal</b>',position:'fixed'}))"), '{"tooltip":"<b>literal</b>"}')
                    evaluate("window.fallbackHost=document.createElement('div');document.body.append(fallbackHost);fallbackHost.append(PrismTables.render({id:'fallback',title:'Fallback',columns:['Name'],rows:[['Keep'],['Move']]},{key:'fallback-test'}));fallbackHost.querySelectorAll('tbody tr')[1].click();PrismTables.capture(fallbackHost);fallbackHost.replaceChildren(PrismTables.render({id:'fallback',title:'Fallback',columns:['Name'],rows:[['New'],['Move'],['Keep']]},{key:'fallback-test'}))")
                    self.assertEqual(evaluate("fallbackHost.querySelector('tr[aria-selected=true]').innerText"), "Move")
                    evaluate("fallbackHost.replaceChildren(PrismTables.render({id:'empty',title:'Empty',columns:['Name'],rows:[],table_style:{filterable:true}},{key:'empty-test'}))")
                    self.assertIn("No rows.", evaluate("fallbackHost.innerText"))
                    evaluate("fallbackHost.remove()")
                    # A raised broker policy can accept >1000 style coordinates.
                    # Filter to one row so this checks policy parity without a large DOM.
                    count = 1005
                    tall = {"id": "policy-tall", "kind": "table", "title": "Raised policy",
                            "columns": [f"Column {index}" for index in range(10)],
                            "rows": [["needle" if index == count - 1 else f"row {index}", *range(9)] for index in range(count)],
                            "row_ids": [f"row-{index}" for index in range(count)],
                            "table_style": {"filterable": True, "copyable": True,
                                            "rows": [{"row": index, "bold": True} for index in range(count)],
                                            "cells": [{"row": index, "column": 0, "tooltip": f"source {index}"} for index in range(count)]}}
                    wide = {"id": "policy-wide", "kind": "table", "title": "Wide policy",
                            "columns": [f"Column {index}" for index in range(count)], "rows": [list(range(count))],
                            "table_style": {"columns": [{"column": index, "width": 80} for index in range(count)]}}
                    _validate({"schema_version": 1, "id": "large-policy", "title": "Raised policy", "revision": 1,
                               "cards": [tall, wide]}, ActivityPolicy(max_items_per_card=20000))
                    evaluate("window.policyTall=" + json.dumps(tall) + ";window.policyWide=" + json.dumps(wide))
                    self.assertEqual(evaluate("PrismTables.options(policyWide).columns.length"), count)
                    self.assertEqual(evaluate("PrismTables.options(policyWide).columns.at(-1).column"), count - 1)
                    self.assertEqual(evaluate("PrismTables.options(policyTall).rows.length"), count)
                    self.assertEqual(evaluate("PrismTables.options(policyTall).cells.length"), count)
                    evaluate("localStorage.setItem('prism.table.policy-large-test',JSON.stringify({filter:'needle',selected:policyTall.row_ids}));window.policyHost=document.createElement('div');document.body.append(policyHost);policyHost.append(PrismTables.render(policyTall,{key:'policy-large-test'}))")
                    self.assertEqual(evaluate("policyHost.querySelectorAll('tbody tr').length"), 1)
                    self.assertEqual(evaluate("policyHost.querySelector('tbody tr').dataset.sourceRow"), str(count - 1))
                    self.assertEqual(evaluate("policyHost.querySelector('td[data-source-column=\"0\"]').title"), f"source {count - 1}")
                    self.assertEqual(evaluate("policyHost.querySelectorAll('tbody tr[aria-selected=true]').length"), 1)
                    self.assertEqual(evaluate("JSON.parse(localStorage.getItem('prism.table.policy-large-test')).selected.length"), count)
                    evaluate("localStorage.setItem('prism.table.policy-shrink-test',JSON.stringify({selected:policyTall.row_ids}));policyHost.replaceChildren(PrismTables.render({id:'shrunk',title:'Shrunk',columns:['Name'],rows:[['needle']],row_ids:[policyTall.row_ids.at(-1)]},{key:'policy-shrink-test'}))")
                    self.assertEqual(evaluate("policyHost.querySelectorAll('tbody tr[aria-selected=true]').length"), 1)
                    self.assertEqual(evaluate("JSON.parse(localStorage.getItem('prism.table.policy-shrink-test')).selected.length"), 1)
                    self.assertEqual(evaluate("(()=>{const style=PrismTables.options({columns:['One'],rows:[[1]],table_style:{columns:[{column:2,width:800}],rows:[{row:-1,bold:true}],cells:[{row:0,column:1,bold:true}]}});return [style.columns.length,style.rows.length,style.cells.length]})()"), [0, 0, 0])
                    evaluate("policyHost.remove();localStorage.removeItem('prism.table.policy-large-test');localStorage.removeItem('prism.table.policy-shrink-test')")
                    evaluate("document.getElementById('view').value='canvas';document.getElementById('view').dispatchEvent(new Event('change'))")
                    self.assertTrue(evaluate("rich().querySelector('.table-viewport').getBoundingClientRect().bottom<=tableCard().querySelector('.card-content').getBoundingClientRect().bottom+1"))
                    evaluate("document.getElementById('view').value='read';document.getElementById('view').dispatchEvent(new Event('change'))")
                    (cache / "prism-table-desktop-review.png").write_bytes(base64.b64decode(command("Page.captureScreenshot", {"captureBeyondViewport": True})["data"]))
                    command("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
                    self.assertTrue(evaluate("document.documentElement.scrollWidth<=innerWidth"))
                    self.assertLessEqual(evaluate("rich().querySelector('.table-viewport').getBoundingClientRect().width"), 390)
                    self.assertFalse(any(operation in {"activity_event", "activity_edit", "activity_reply"} for operation, _params in client.calls), "Display, sorting and selection must not send operator intent")
                    (cache / "prism-table-mobile-review.png").write_bytes(base64.b64decode(command("Page.captureScreenshot")["data"]))
                    wire.send(json.dumps({"id": serial + 1, "method": "Browser.close"}))
            finally:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
