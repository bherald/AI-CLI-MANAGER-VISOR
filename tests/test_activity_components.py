import json
import unittest
from dataclasses import replace

from visor.activity_components import (
    KINDS,
    catalog,
    component,
    template,
    validate_actions,
    validate_component,
    validate_event,
)
from visor.activity_document import ActivityPolicy, _validate


class ActivityComponentTests(unittest.TestCase):
    def setUp(self):
        self.policy = ActivityPolicy()

    def test_catalog_examples_and_complete_templates_use_the_real_validator(self):
        capabilities = json.loads(json.dumps(catalog()))
        self.assertTrue(capabilities["freeform"])
        self.assertEqual({item["kind"] for item in capabilities["components"]}, KINDS)
        for item in capabilities["components"]:
            with self.subTest(kind=item["kind"]):
                self.assertTrue(validate_component(item["example"], self.policy))
                self.assertTrue(item["fields"])
        for name in capabilities["templates"]:
            with self.subTest(template=name):
                document = template(name, "example", "Example project")
                self.assertEqual(document["id"], "example")
                self.assertEqual(document["title"], "Example project")
                self.assertNotIn("contributor", document)
                _validate(document, self.policy)

    def test_recipes_are_independent_and_freeform_is_empty(self):
        card = component("sparkline")
        card["values"].append(900)
        self.assertNotEqual(card["values"], component("sparkline")["values"])
        self.assertEqual(template("freeform")["cards"], [])
        with self.assertRaises(ValueError):
            template("missing")
        with self.assertRaises(ValueError):
            component("missing")
        self.assertFalse(validate_component({"kind": "text"}, self.policy))
        self.assertFalse(validate_component({"kind": []}, self.policy))

    def test_malformed_component_data_is_rejected(self):
        cases = [
            ("gauge", {"value": float("nan")}),
            ("gauge", {"min": 100, "max": 0}),
            ("meter", {"value": -1}),
            ("progress", {"value": True}),
            ("meter", {"orientation": []}),
            ("indicator", {"state": "approved-automatically"}),
            ("status", {"symbol": "x" * 33}),
            ("counter", {"value": 10**1000}),
            ("stat", {"detail": "bad\x00text"}),
            ("sparkline", {"values": [0, float("inf")]}),
            ("timer", {"seconds": -1}),
            ("timer", {"running": "yes"}),
            ("timer", {"mode": {}}),
            ("table", {"rows": [["missing cells"]]}),
            ("table", {"rows": [[{}, "You", "Today"]]}),
            ("arrow", {"direction": {}}),
            ("toggle", {"value": 1}),
            ("slider", {"min": 0, "max": 1, "step": .3, "value": 0}),
            ("slider", {"step": 0}),
            ("slider", {"step": 1e-300}),
            ("select", {"choices": ["same", "same"]}),
            ("select", {"value": "undeclared"}),
            ("action", {"action_id": "not safe"}),
            ("gauge", {"color": "red"}),
            ("gauge", {"depth": "yes"}),
            ("gauge", {"script": "alert(1)"}),
            ("action", {"command": "delete files"}),
        ]
        for kind, properties in cases:
            with self.subTest(kind=kind, properties=properties), self.assertRaises(ValueError):
                validate_component(component(kind, **properties), self.policy)

    def test_component_lists_and_text_honor_operator_limits(self):
        small = replace(self.policy, max_items_per_card=3, max_text_chars=10)
        for card in [component("sparkline"), component("table"),
                     component("counter", value="x" * 11, detail="")]:
            with self.subTest(kind=card["kind"]), self.assertRaises(ValueError):
                validate_component(card, small)
        self.assertTrue(validate_component(component("sparkline", values=[1, 2, 3], label=""), small))
        self.assertTrue(validate_component(component("sparkline", values=[]), self.policy))

    def test_common_actions_work_on_legacy_cards_with_strict_declarations(self):
        card = {"kind": "text", "actions": [{"id": "inspect", "label": "Inspect"}]}
        self.assertEqual(validate_actions(card, self.policy), card["actions"])
        self.assertEqual(validate_event(card, {"action_id": "inspect", "value": None}, self.policy),
                         {"action_id": "inspect", "value": None})
        for actions in [
            [{"id": "inspect", "label": "Inspect", "command": "something"}],
            [{"id": "inspect", "label": "Inspect"}] * 2,
            [{"id": str(i), "label": "Inspect"} for i in range(9)],
            [{"id": "unsafe id", "label": "Inspect"}],
        ]:
            with self.subTest(actions=actions), self.assertRaises(ValueError):
                validate_actions({"kind": "text", "actions": actions}, self.policy)
        with self.assertRaises(ValueError):
            validate_component(component("action", actions=[{"id": "view-checks", "label": "Duplicate"}]), self.policy)

    def test_events_match_the_exact_declared_type_and_range(self):
        good = [("action", None), ("toggle", True), ("slider", 42), ("select", "Pilot")]
        for kind, value in good:
            card = component(kind)
            event = {"action_id": card["action_id"], "value": value}
            with self.subTest(kind=kind):
                self.assertEqual(validate_event(card, event, self.policy), event)
        bad = [("action", True), ("toggle", 1), ("slider", 42.5), ("slider", float("nan")),
               ("slider", 101), ("select", "undeclared"), ("select", None)]
        for kind, value in bad:
            card = component(kind)
            with self.subTest(kind=kind, value=value), self.assertRaises(ValueError):
                validate_event(card, {"action_id": card["action_id"], "value": value}, self.policy)
        for event in [{"action_id": "undeclared", "value": None},
                      {"action_id": "view-checks", "value": None, "command": "run"}]:
            with self.subTest(event=event), self.assertRaises(ValueError):
                validate_event(component("action"), event, self.policy)


if __name__ == "__main__":
    unittest.main()
