"""Bounded, portable Prism components and optional agent authoring recipes.

The data describes displays and operator intent. It never contains executable
commands, scripts, URLs or tool approvals. The broker supplies event ownership.
"""
from __future__ import annotations

import copy
import math
import re

from .activity_table import table_presets, table_style_schema, validate_table

COMPONENT_VERSION = 1
KINDS = frozenset({"gauge", "meter", "progress", "indicator", "status", "counter", "stat",
                   "sparkline", "timer", "table", "arrow", "action", "toggle", "slider", "select"})
STATES = ("neutral", "info", "success", "warning", "error", "working", "waiting", "offline")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?\Z")
_FORBIDDEN = {"script", "javascript", "command", "exec", "url", "href", "onclick"}


def _text(value, label, policy, *, nonempty=False, limit=None):
    if (not isinstance(value, str) or len(value) > min(limit or policy.max_text_chars, policy.max_text_chars)
            or "\x00" in value or nonempty and not value.strip()):
        raise ValueError(label + " must be bounded usable text")
    return value


def _identifier(value, label):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(label + " must be a safe stable identifier")
    return value


def _number(value, label):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e15
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(label + " must be a finite number within 1000000000000000")
    return value


def _list(value, label, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(label + " must be a bounded list")
    return value


def _range(card):
    low = _number(card.get("min", 0), "minimum")
    high = _number(card.get("max", 100), "maximum")
    value = _number(card.get("value", low), "value")
    if high <= low or not low <= value <= high:
        raise ValueError("Component requires min < max and a value inside that range")
    return low, high, value


def _slider(card):
    low, high, value = _range(card)
    step = _number(card.get("step", 1), "slider step")
    if step <= 0:
        raise ValueError("Slider step must be positive")
    steps, position = (high - low) / step, (value - low) / step
    if (not 1 <= steps <= 10000 or not math.isclose(steps, round(steps), abs_tol=1e-7)
            or not math.isclose(position, round(position), abs_tol=1e-7)):
        raise ValueError("Slider range/value must align to its step, with at most 10000 steps")
    return low, high, value, step


def validate_actions(card, policy):
    """Validate common direct-action declarations on either new or legacy cards."""
    actions = _list(card.get("actions", []), "actions", min(8, policy.max_items_per_card))
    seen = set()
    if card.get("kind") in {"action", "toggle", "slider", "select"}:
        seen.add(_identifier(card.get("action_id"), "action_id"))
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"id", "label"}:
            raise ValueError("An action contains only id and label; commands and scripts are unsupported")
        identity = _identifier(action["id"], "action id")
        if identity in seen:
            raise ValueError("Action IDs must be distinct within a card")
        seen.add(identity)
        _text(action["label"], "action label", policy, nonempty=True, limit=160)
    return actions


def validate_component(card, policy):
    """True for validated new components, False for kinds handled by the caller."""
    if not isinstance(card, dict) or not isinstance(card.get("kind"), str) or card["kind"] not in KINDS:
        return False
    kind = card["kind"]
    if _FORBIDDEN & card.keys():
        raise ValueError("Components describe data and intent, not commands, scripts or URLs")
    for name in ("unit", "detail", "label", "text", "symbol"):
        if name in card:
            _text(card[name], name, policy, limit=32 if name == "symbol" else None)
    if "color" in card and (not isinstance(card["color"], str) or not _COLOR.fullmatch(card["color"])):
        raise ValueError("Component color must be a hex RGB or ARGB value")
    if "depth" in card and type(card["depth"]) is not bool:
        raise ValueError("depth must be a boolean")
    if kind in {"gauge", "meter", "progress"}:
        _range(card)
        if card.get("orientation", "horizontal") not in ("horizontal", "vertical"):
            raise ValueError("Meter orientation must be horizontal or vertical")
    elif kind in {"indicator", "status"}:
        if card.get("state", "neutral") not in STATES:
            raise ValueError("Unknown component state")
    elif kind in {"counter", "stat"}:
        value = card.get("value", 0)
        _text(value, "value", policy) if isinstance(value, str) else _number(value, "value")
    elif kind == "sparkline":
        for value in _list(card.get("values"), "sparkline values", policy.max_items_per_card):
            _number(value, "sparkline value")
    elif kind == "timer":
        if not 0 <= _number(card.get("seconds", 0), "timer seconds") <= 3155760000:
            raise ValueError("Timer seconds must be between zero and 100 years")
        if type(card.get("running", False)) is not bool:
            raise ValueError("Timer running must be a boolean")
        if card.get("mode", "elapsed") not in ("elapsed", "countdown"):
            raise ValueError("Timer mode must be elapsed or countdown")
    elif kind == "table":
        validate_table(card, policy)
    elif kind == "arrow":
        if card.get("direction", "right") not in ("left", "right", "up", "down"):
            raise ValueError("Arrow direction must be left, right, up or down")
    elif kind == "toggle":
        if type(card.get("value", False)) is not bool:
            raise ValueError("Toggle value must be a boolean")
    elif kind == "slider":
        _slider(card)
    elif kind == "select":
        choices = _list(card.get("choices"), "select choices", policy.max_items_per_card)
        if not choices or len(choices) != len({_text(x, "choice", policy, nonempty=True) for x in choices}):
            raise ValueError("Select choices must be nonempty and distinct")
        if card.get("value", choices[0]) not in choices:
            raise ValueError("Select value must be one of the choices")
    validate_actions(card, policy)
    return True


def validate_event(card, event, policy):
    """Validate operator intent against the exact declared card, never executable code."""
    validate_component(card, policy)
    actions = validate_actions(card, policy)
    if not isinstance(event, dict) or set(event) != {"action_id", "value"}:
        raise ValueError("Component event contains action_id and value only")
    action = _identifier(event["action_id"], "event action_id")
    value = event["value"]
    if any(item["id"] == action for item in actions):
        if value is not None:
            raise ValueError("A direct action does not accept a value")
    elif card.get("kind") in {"action", "toggle", "slider", "select"} and action == card["action_id"]:
        kind = card["kind"]
        if kind == "action" and value is not None:
            raise ValueError("An action does not accept a value")
        if kind == "toggle" and type(value) is not bool:
            raise ValueError("Toggle events require a boolean")
        if kind == "slider":
            _slider({**card, "value": value})
        if kind == "select" and value not in card["choices"]:
            raise ValueError("Select event requires one of its declared choices")
    else:
        raise ValueError("The action is not declared on this card")
    return {"action_id": action, "value": value}


_DEFAULTS = {
    "gauge": {"value": 95, "min": 0, "max": 100, "unit": "%", "detail": "19 of 20 checks passed"},
    "meter": {"value": 42, "min": 0, "max": 100, "unit": "%"},
    "progress": {"value": 19, "min": 0, "max": 20, "detail": "Checks passed"},
    "indicator": {"state": "success", "text": "On track", "symbol": "✓"},
    "status": {"state": "warning", "text": "Review required"},
    "counter": {"value": 19, "unit": "checks", "detail": "Verified"},
    "stat": {"value": 240, "unit": "ms", "detail": "Response time · stable"},
    "sparkline": {"values": [245, 231, 251, 239, 244, 228, 240], "unit": "ms", "label": "Response time"},
    "timer": {"seconds": 300, "running": False, "mode": "countdown", "detail": "Next check"},
    "table": {"columns": ["Action", "Owner", "When"],
              "rows": [["Retest permissions", "Quality review", "Today"], ["Approve pilot", "You", "Friday"]],
              "row_ids": ["retest-permissions", "approve-pilot"], "table_style": table_presets()["project"]},
    "arrow": {"direction": "right", "text": "Next step"},
    "action": {"action_id": "view-checks", "label": "View checks"},
    "toggle": {"action_id": "change-preview", "label": "Preview enabled", "value": False},
    "slider": {"action_id": "change-target", "label": "Preview target", "min": 0, "max": 100, "step": 1, "value": 50},
    "select": {"action_id": "choose-stage", "label": "Next stage", "choices": ["Plan", "Verify", "Pilot"], "value": "Verify"},
}


def component(kind, id=None, title=None, **properties):
    """Create an editable single-card recipe; validation is applied on publication."""
    if kind not in _DEFAULTS:
        raise ValueError("Unknown component recipe")
    result = {"id": id or kind, "kind": kind, "title": title or kind.replace("-", " ").capitalize(),
              **copy.deepcopy(_DEFAULTS[kind]), **copy.deepcopy(properties)}
    if kind == "table" and "rows" in properties and "row_ids" not in properties:
        result.pop("row_ids", None)
    return result


def template(name="project-overview", id="project-overview", title=None):
    """Optional complete board recipes. Agents remain free to compose any valid board."""
    presets = {
        "project-overview": [("gauge", "Readiness"), ("table", "Next actions"), ("sparkline", "Service health"), ("indicator", "Status"), ("meter", "Capacity"), ("arrow", "Next milestone")],
        "status-board": [("gauge", "Readiness"), ("stat", "Response time"), ("meter", "Capacity"), ("sparkline", "Trend"), ("timer", "Next check"), ("indicator", "Status")],
        "decision-board": [("status", "Decision required"), ("table", "Next actions"), ("select", "Choose the next stage"), ("action", "View checks")],
        "freeform": [],
    }
    if name not in presets:
        raise ValueError("Unknown board template")
    cards = []
    for index, (kind, heading) in enumerate(presets[name]):
        card = component(kind, id=f"{kind}-{index + 1}", title=heading)
        card["geometry"] = [20 + index % 3 * 360, 20 + index // 3 * 300, 340, 280]
        cards.append(card)
    return {"schema_version": 1, "id": id, "title": title or name.replace("-", " ").capitalize(), "revision": 1, "cards": cards}


def catalog():
    """Serializable capability discovery; schemas use the same vocabulary on every UI."""
    shapes = {
        "gauge": "value:number, min?:number=0, max?:number=100, unit?:text, detail?:text",
        "meter": "value:number, min?:number=0, max?:number=100, unit?:text, orientation?:horizontal|vertical",
        "progress": "value:number, min?:number=0, max?:number=100, detail?:text",
        "indicator": "state?:neutral|info|success|warning|error|working|waiting|offline, text?:text, symbol?:text",
        "status": "state?:neutral|info|success|warning|error|working|waiting|offline, text?:text",
        "counter": "value:number|text, unit?:text, detail?:text",
        "stat": "value:number|text, unit?:text, detail?:text",
        "sparkline": "values:number[], unit?:text, label?:text",
        "timer": "seconds?:number=0, running?:boolean=false, mode?:elapsed|countdown",
        "table": "columns:text[], rows:(text|number|boolean|null)[][], row_ids?:distinct-identifier[] (one per row), table_style?:portable style object; see table_style_schema and table_presets",
        "arrow": "direction?:left|right|up|down, text?:text",
        "action": "action_id:identifier, label?:text",
        "toggle": "action_id:identifier, label?:text, value?:boolean=false",
        "slider": "action_id:identifier, label?:text, min?:number=0, max?:number=100, step?:number=1, value?:number",
        "select": "action_id:identifier, label?:text, choices:distinct-text[], value?:text",
    }
    entries = [{"kind": kind, "fields": shapes[kind], "example": component(kind)} for kind in sorted(KINDS)]
    table_entry = next(entry for entry in entries if entry["kind"] == "table")
    table_entry["table_style_schema"] = table_style_schema()
    table_entry["styled_example"] = component("table", id="risk-review", title="Release review",
        columns=["Check", "Owner", "Status"],
        rows=[["Permissions review", "Quality lead", "Needs review"], ["Backup rehearsal", "Release lead", "Verified"]],
        row_ids=["permissions-review", "backup-rehearsal"],
        table_style={**table_presets()["risk"],
            "columns": [{"column": 0, "width": 240, "wrap": True}, {"column": 2, "width": 140, "align": "center"}],
            "cells": [{"row": 0, "column": 2, "badge": "warning", "bold": True,
                       "tooltip": "One issue still needs a decision"},
                      {"row": 1, "column": 2, "badge": "success", "tooltip": "Rehearsal verified"}]})
    return {"schema_version": 1, "component_version": COMPONENT_VERSION,
            "components": entries,
            "table_presets": table_presets(),
            "table_rules": {"coordinates": "Styles address original source rows/columns, independent of local sort/filter",
                "row_identity": "Prefer stable row_ids for changing values; otherwise content fingerprints plus occurrence identify rows",
                "cascade": "Body: global border, column, row, cell. Header: global border, header, column. Border properties merge",
                "capacity": "Column headers plus data cells, and aggregate column/row/cell style overrides, each honor max_items_per_card",
                "authoring": "Copy a table_presets style into table_style, then add bounded source-index overrides. Presets are optional; arbitrary CSS and commands are unsupported"},
            "common": {"actions": [{"id": "view-checks", "label": "View checks"}],
                       "color": "optional hex RGB/ARGB", "depth": "optional boolean; restrained visual depth",
                       "geometry": "optional [x,y,width,height]; templates are not required"},
            "events": {"shape": {"action_id": "declared action ID", "value": "null|boolean|number|text"},
                       "semantics": "Operator intent only; does not acknowledge, approve or execute commands"},
            "templates": ["project-overview", "status-board", "decision-board", "freeform"],
            "legacy_kinds": ["text", "image", "chart", "diagram", "question", "sketch"],
            "freeform": True}
