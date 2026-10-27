"""Portable table bounds, style inheritance and stable source-coordinate helpers."""
import copy
import csv
import io
import json
import unittest
from dataclasses import replace

from visor.activity_components import catalog, component, validate_component
from visor.activity_document import ActivityPolicy, _validate
from visor.activity_table import (
    cell_style,
    copy_tsv,
    display_cell,
    row_keys,
    table_options,
    table_presets,
    table_style_schema,
    validate_table,
)


class ActivityTableCase(unittest.TestCase):
    def setUp(self):
        self.policy = ActivityPolicy()
        self.card = {"id": "checks", "kind": "table", "title": "Project checks",
                     "columns": ["Check", "Owner", "Ready"],
                     "rows": [["Permissions", "Quality lead", True], ["Pilot", "Operator", False]],
                     "row_ids": ["permissions", "pilot"]}

    def valid(self, card=None, policy=None):
        actual = card or self.card
        validate_table(actual, policy or self.policy)
        self.assertTrue(validate_component(actual, policy or self.policy))
        return actual

    def test_old_boards_keep_theme_defaults_and_optional_controls_off(self):
        self.valid()
        options = table_options(self.card)
        self.assertEqual(options, {"density": "comfortable", "striped": False, "grid": "all",
            "border": {}, "header": {}, "columns": [], "rows": [], "cells": [],
            "sortable": False, "filterable": False, "copyable": False})
        self.assertEqual(cell_style(self.card, 0, 0), {})
        options["columns"].append({"column": 0, "bold": True})
        self.assertEqual(table_options(self.card)["columns"], [])
        self.assertNotIn("table_style", self.card)

    def test_full_valid_style_and_source_coordinate_cascade(self):
        style = {"density": "compact", "striped": True, "grid": "horizontal",
                 "border": {"color": "#456789", "width": 1, "style": "solid"},
                 "header": {"bold": True, "background": "#263A46", "align": "center", "tooltip": "Header help"},
                 "columns": [{"column": 0, "width": 240, "text_color": "#E7EFEA", "font_size": 12,
                              "align": "left", "border": {"style": "dotted"}}],
                 "rows": [{"row": 1, "background": "#3A3028", "bold": False, "border": {"width": 2}}],
                 "cells": [{"row": 1, "column": 0, "background": "#425E55", "badge": "warning",
                            "wrap": True, "padding": 8, "tooltip": "Literal <b>text</b>",
                            "border": {"color": "#CCAA66"}}],
                 "sortable": True, "filterable": True, "copyable": True}
        self.card["table_style"] = style
        self.valid()
        before = copy.deepcopy(self.card)
        body = cell_style(self.card, 1, 0)
        self.assertEqual(body["background"], "#425E55")
        self.assertEqual(body["border"], {"color": "#CCAA66", "width": 2, "style": "dotted"})
        self.assertEqual(body["badge"], "warning")
        self.assertFalse(body["bold"])
        self.assertTrue(body["wrap"])
        self.assertNotIn("width", body)
        header = cell_style(self.card, -1, 0, header=True)
        self.assertEqual(header["background"], "#263A46")
        self.assertEqual(header["align"], "left")
        self.assertTrue(header["bold"])
        self.assertNotIn("badge", header)
        self.assertEqual(header["border"], {"color": "#456789", "width": 1, "style": "dotted"})
        self.assertEqual(self.card, before)
        body["border"]["width"] = 7
        self.assertEqual(cell_style(self.card, 1, 0)["border"]["width"], 2)

    def test_style_properties_are_bounded_typed_and_do_not_accept_css_or_commands(self):
        invalid = [{"background": "red"}, {"text_color": "#FFFFFF00"}, {"background": "#123456\n"},
                   {"font_size": 9}, {"font_size": 29}, {"font_size": True}, {"font_size": 12.0},
                   {"padding": -1}, {"padding": 17}, {"padding": 1.5}, {"bold": 1}, {"wrap": "yes"},
                   {"align": "justify"}, {"badge": {"state": "success"}}, {"badge": "approved"},
                   {"tooltip": "x" * 1001}, {"tooltip": "bad\x00help"}, {"tooltip": []},
                   {"border": {"width": -1}}, {"border": {"width": 8.1}}, {"border": {"width": True}},
                   {"border": {"width": float("nan")}}, {"border": {"width": float("inf")}},
                   {"border": {"style": "double"}}, {"border": {"color": "var(--accent)"}},
                   {"border": {"radius": 2}}, {"css": "background:red"}, {"onclick": "run()"},
                   {"command": "run"}, {"url": "https://example.invalid"}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_table({**self.card, "table_style": {"header": value}}, self.policy)
        for value in ({"density": "dense"}, {"grid": "diagonal"}, {"striped": 0},
                      {"sortable": "yes"}, {"filterable": 1}, {"copyable": None},
                      {"preset": "project"}, {"background": "#123456"}, {"css": "body{}"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_table({**self.card, "table_style": value}, self.policy)

    def test_border_extremes_and_every_declared_status_are_valid(self):
        statuses = ("neutral", "info", "success", "warning", "error", "working", "waiting", "offline")
        for status in statuses:
            for width in (0, .5, 8):
                with self.subTest(status=status, width=width):
                    self.valid({**self.card, "table_style": {"header": {"badge": status, "font_size": 10,
                        "padding": 0, "wrap": False, "border": {"width": width, "style": "none", "color": "#AaBbCc"}}}})

    def test_override_coordinates_are_exact_in_bounds_unique_and_have_fixed_fields(self):
        invalid = [
            {"columns": [{"column": 3}]}, {"columns": [{"column": -1}]}, {"columns": [{"column": True}]},
            {"columns": [{"column": 0, "width": 79}]}, {"columns": [{"column": 0, "width": 801}]},
            {"columns": [{"column": 0, "width": 100.0}]}, {"columns": [{"column": 0}, {"column": 0}]},
            {"rows": [{"row": 2}]}, {"rows": [{"row": 0, "width": 200}]}, {"rows": [{"row": 0.0}]},
            {"rows": [{"row": 0}, {"row": 0}]}, {"rows": [{"column": 0}]},
            {"cells": [{"row": 0, "column": 3}]}, {"cells": [{"row": 2, "column": 0}]},
            {"cells": [{"row": 0}]}, {"cells": [{"row": 0, "column": 0, "width": 200}]},
            {"cells": [{"row": 0, "column": 0}, {"row": 0, "column": 0}]},
            {"columns": {}}, {"rows": None}, {"cells": [None]}, {"header": []}, {"border": []}]
        for style in invalid:
            with self.subTest(style=style), self.assertRaises(ValueError):
                validate_table({**self.card, "table_style": style}, self.policy)
        self.valid({**self.card, "table_style": {"columns": [{"column": 0, "width": 80}, {"column": 2, "width": 800}],
                                                 "cells": [{"row": 0, "column": 0}, {"row": 1, "column": 0}]}})

    def test_data_and_aggregate_style_budgets_and_text_policy_remain_authoritative(self):
        small = replace(self.policy, max_items_per_card=2)
        card = {"kind": "table", "columns": ["Check"], "rows": [[True]]}
        validate_table(card, small)
        with self.assertRaisesRegex(ValueError, "overrides"):
            validate_table({**card, "table_style": {"columns": [{"column": 0}], "rows": [{"row": 0}],
                                                    "cells": [{"row": 0, "column": 0}]}}, small)
        for changes in ({"rows": [[1], [2]]}, {"columns": []}, {"rows": [[{}]]},
                        {"rows": [[float("nan")]]}, {"rows": [[10**1000]]}, {"rows": [["bad\x00text"]]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_table({**card, **changes}, small)
        with self.assertRaisesRegex(ValueError, "tooltip"):
            validate_table({**card, "table_style": {"header": {"tooltip": "123456"}}},
                           replace(self.policy, max_text_chars=5))

    def test_row_ids_are_unique_safe_exact_length_and_work_on_empty_tables(self):
        for identities in (["same", "same"], ["only-one"], ["one", "two", "three"], ["good", "not safe"],
                           ["good", "x" * 97], ["good", 1], "a,b", None):
            with self.subTest(identities=identities), self.assertRaises(ValueError):
                validate_table({**self.card, "row_ids": identities}, self.policy)
        self.valid({**self.card, "rows": [], "row_ids": []})
        self.valid({**self.card, "row_ids": ["a", "B-._9"]})
        self.assertEqual(row_keys(self.card), ["permissions", "pilot"])
        changed = copy.deepcopy(self.card)
        changed["rows"][0][2] = False
        self.assertEqual(row_keys(changed), row_keys(self.card))

    def test_fingerprint_keys_follow_unchanged_rows_and_distinguish_duplicates(self):
        card = {"rows": [["α", True], ["β", None], ["α", True]]}
        keys = row_keys(card)
        self.assertEqual(len(set(keys)), 3)
        self.assertTrue(keys[0].endswith("-1"))
        self.assertTrue(keys[2].endswith("-2"))
        reordered = row_keys({"rows": [["new", 1], card["rows"][1], card["rows"][0], card["rows"][2]]})
        self.assertEqual(reordered[1:], [keys[1], keys[0], keys[2]])
        self.assertNotEqual(row_keys({"rows": [["α", False]]})[0], keys[0])
        self.assertEqual(row_keys({"rows": []}), [])

    def test_copy_uses_source_order_without_rounding_or_headers_and_quotes_literal_content(self):
        card = {"columns": ["Name", "Ready", "Amount", "Note"],
                "rows": [["One", True, 1234.56789123456, None],
                         ["Two\tparts", False, 2.0, 'Line\r\n"quote"'], ["Three", None, -0.0, "A\rB"]]}
        text = copy_tsv(card, [2, 0, 1, 0])
        parsed = list(csv.reader(io.StringIO(text, newline=""), delimiter="\t"))
        self.assertEqual(parsed, [["Three", "", "0", "A\rB"],
                                  ["One", "Yes", "1234.56789123456", ""],
                                  ["Two\tparts", "No", "2", 'Line\r\n"quote"']])
        self.assertFalse(text.endswith("\n"))
        self.assertEqual(copy_tsv(card, []), "")
        for indices in ([-1], [3], [True], [1.0]):
            with self.subTest(indices=indices), self.assertRaises(ValueError):
                copy_tsv(card, indices)
        self.assertEqual([display_cell(value) for value in (None, True, False, 1, 1.0, "😀")],
                         ["", "Yes", "No", "1", "1", "😀"])

    def test_schema_and_presets_are_discoverable_independent_valid_recipes(self):
        capabilities = json.loads(json.dumps(catalog(), allow_nan=False))
        entry = next(item for item in capabilities["components"] if item["kind"] == "table")
        schema = entry["table_style_schema"]
        self.valid(entry["styled_example"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]), set(table_options(self.card)))
        self.assertEqual(schema["properties"]["header"]["properties"]["font_size"]["maximum"], 28)
        self.assertEqual(table_style_schema(replace(self.policy, max_items_per_card=7))["properties"]["cells"]["maxItems"], 7)
        self.assertEqual(set(capabilities["table_presets"]), {"clean", "project", "metrics", "risk"})
        for name, style in capabilities["table_presets"].items():
            with self.subTest(preset=name):
                self.valid({**self.card, "table_style": style})
        sample = component("table")
        self.assertEqual(len(sample["row_ids"]), len(sample["rows"]))
        self.assertTrue(sample["table_style"]["filterable"])
        _validate({"schema_version": 1, "id": "project", "title": "Project", "revision": 1, "cards": [sample]}, self.policy)
        overridden = component("table", columns=["Custom"], rows=[["Only one row"]])
        self.assertNotIn("row_ids", overridden)
        self.valid(overridden)
        presets = table_presets()
        presets["project"]["header"]["bold"] = False
        self.assertTrue(table_presets()["project"]["header"]["bold"])
