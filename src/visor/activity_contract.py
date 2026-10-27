"""Machine-readable Prism authoring guidance using the runtime policy bounds."""
from __future__ import annotations

import copy
from dataclasses import asdict

from .activity_document import ActivityPolicy

CONTRACT_REVISION = 2
LEGACY_KINDS = ("text", "image", "chart", "diagram", "question", "sketch")
IDENTIFIER = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"}


def card_schema(kind, policy=None):
    """Authoring schema; preflight remains authoritative for cross-field rules."""
    from .activity_components import KINDS, STATES
    policy = policy or ActivityPolicy()
    if kind not in KINDS and kind not in LEGACY_KINDS:
        raise ValueError("Unknown Prism component kind")
    text = {"type": "string", "maxLength": policy.max_text_chars, "pattern": "^[^\\u0000]*$"}
    number = {"type": "number", "minimum": -1e15, "maximum": 1e15}
    scalar = {"anyOf": [text, number, {"type": "boolean"}, {"type": "null"}]}
    properties = {
        "id": IDENTIFIER, "kind": {"const": kind}, "title": {**text, "minLength": 1},
        "caption": text, "help": {"type": "string", "maxLength": min(1000, policy.max_text_chars)},
        "geometry": {"type": "array", "minItems": 4, "maxItems": 4, "prefixItems": [
            {"type": "integer", "minimum": -100000, "maximum": 100000},
            {"type": "integer", "minimum": -100000, "maximum": 100000},
            {"type": "integer", "minimum": 100, "maximum": 100000},
            {"type": "integer", "minimum": 80, "maximum": 100000}]},
        "actions": {"type": "array", "maxItems": min(8, policy.max_items_per_card), "items": {
            "type": "object", "required": ["id", "label"], "additionalProperties": False,
            "properties": {"id": IDENTIFIER, "label": {"type": "string", "minLength": 1,
                            "maxLength": min(160, policy.max_text_chars)}}}},
        "data_status": {"type": "object", "required": ["state"], "additionalProperties": False,
            "properties": {"state": {"enum": ["ready", "loading", "empty", "error", "stale"]},
                "message": {"type": "string", "maxLength": min(400, policy.max_text_chars)},
                "source": {"type": "string", "maxLength": min(160, policy.max_text_chars)},
                "observed_at": {"type": "string", "format": "date-time", "maxLength": min(64, policy.max_text_chars)}}},
    }
    required = ["id", "kind", "title"]
    array = lambda items: {"type": "array", "maxItems": policy.max_items_per_card, "items": items}
    if kind not in LEGACY_KINDS:
        properties.update({key: text for key in ("label", "text", "detail", "unit")})
        properties.update(color={"type": "string", "pattern": "^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"},
                          depth={"type": "boolean"}, symbol={"type": "string", "maxLength": min(32, policy.max_text_chars)})
    if kind == "text":
        properties["html"] = text
        required.append("html")
    elif kind == "image":
        properties["path"] = {**text, "description": "Contained relative raster path returned by activity_asset"}
        required.append("path")
    elif kind == "chart":
        properties.update(labels=array(text), values=array(number), unit=text)
        required += ["labels", "values"]
    elif kind == "diagram":
        properties.update(nodes=array({"type": "object", "required": ["id", "label", "x", "y", "w", "h"],
            "properties": {"id": IDENTIFIER, "label": text,
                           **{key: {"type": "number", "minimum": -100000, "maximum": 100000} for key in ("x", "y")},
                           **{key: {"type": "number", "exclusiveMinimum": 0, "maximum": 100000} for key in ("w", "h")},
                           "color": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"}}}),
            edges=array({"type": "object", "required": ["from", "to"],
                "properties": {"from": IDENTIFIER, "to": IDENTIFIER, "label": text}}))
        required += ["nodes", "edges"]
    elif kind == "question":
        properties.update(prompt={**text, "minLength": 1}, choices={**array({"type": "string", "minLength": 1,
            "maxLength": min(policy.max_text_chars, policy.max_answer_chars)}), "minItems": 1, "uniqueItems": True})
        required.append("prompt")
    elif kind == "sketch":
        point = {"type": "array", "minItems": 2, "maxItems": 2,
                 "items": {"type": "number", "minimum": 0, "maximum": 100000}}
        properties.update(canvas={"type": "array", "minItems": 2, "maxItems": 2,
            "items": {"type": "number", "minimum": 1, "maximum": 100000}},
            strokes=array({"type": "object", "required": ["points", "color", "width"], "properties": {
                "points": {"type": "array", "minItems": 1, "maxItems": policy.max_drawing_points, "items": point},
                "color": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"},
                "width": {"type": "number", "exclusiveMinimum": 0, "maximum": 100000}}}))
        required += ["canvas", "strokes"]
    elif kind in {"gauge", "meter", "progress", "slider"}:
        properties.update(value=number, min=number, max=number, orientation={"enum": ["horizontal", "vertical"]})
        if kind == "slider":
            properties.update(step={"type": "number", "exclusiveMinimum": 0, "maximum": 1e15})
    elif kind in {"counter", "stat"}:
        properties["value"] = {"anyOf": [number, text]}
    elif kind in {"indicator", "status"}:
        properties["state"] = {"enum": list(STATES)}
    elif kind == "sparkline":
        properties["values"] = array(number)
        required.append("values")
    elif kind == "timer":
        properties.update(seconds={"type": "number", "minimum": 0, "maximum": 3155760000},
                          running={"type": "boolean"}, mode={"enum": ["elapsed", "countdown"]})
    elif kind == "table":
        from .activity_table import table_style_schema
        properties.update(columns={**array({**text, "minLength": 1}), "minItems": 1},
                          rows=array({"type": "array", "maxItems": policy.max_items_per_card, "items": scalar}),
                          row_ids={**array(IDENTIFIER), "uniqueItems": True}, table_style=table_style_schema(policy))
        required += ["columns", "rows"]
    elif kind == "arrow":
        properties["direction"] = {"enum": ["left", "right", "up", "down"]}
    elif kind == "toggle":
        properties["value"] = {"type": "boolean"}
    elif kind == "select":
        properties.update(choices={**array({**text, "minLength": 1}), "minItems": 1, "uniqueItems": True}, value=text)
        required.append("choices")
    if kind in {"action", "toggle", "slider", "select"}:
        properties["action_id"] = IDENTIFIER
        required.append("action_id")
    constraints = {"table": ["Row lengths equal the column count", "Total cells and style overrides obey the item limit",
                             "row_ids match row count and are unique", "Style coordinates reference original data positions"],
                   "chart": ["labels and values have equal lengths"],
                   "diagram": ["Node IDs are unique and edges reference existing nodes"],
                   "sketch": ["Points stay inside canvas dimensions", "Stroke width fits the canvas", "Total points obey the drawing limit"],
                   "slider": ["min < max and value is inside the range", "Range and value align to step; at most 10000 steps"],
                   "select": ["value belongs to choices"],
                   "gauge": ["min < max and value is inside the range"],
                   "meter": ["min < max and value is inside the range"],
                   "progress": ["min < max and value is inside the range"]}
    return {"type": "object", "required": required, "properties": copy.deepcopy(properties),
            "x-runtime-constraints": constraints.get(kind, []) + ["Titles and required text contain non-whitespace characters", "Action IDs are distinct within the card"],
            "description": "Supported authoring fields. Use activity_validate for cross-field and current publication checks."}


def describe_catalog(catalog, policy=None, *, kind=None, include_schema=False):
    from .activity_components import KINDS
    policy = policy or ActivityPolicy()
    result = copy.deepcopy(catalog)
    all_kinds = sorted(KINDS | set(LEGACY_KINDS))
    if kind is not None and kind not in all_kinds:
        raise ValueError("Unknown Prism component kind")
    entries = {item["kind"]: item for item in result["components"]}
    result["components"] = [entries.get(name, {"kind": name}) for name in ([kind] if kind else all_kinds)]
    if kind is not None or include_schema:
        for item in result["components"]:
            item["json_schema"] = card_schema(item["kind"], policy)
    else:
        for item in result["components"]:
            item.pop("table_style_schema", None)
    result.update(contract_revision=CONTRACT_REVISION, limits=asdict(policy),
        cache_hint="Cache the catalog by contract_revision and effective limits; request kind for detailed schemas.",
        platforms={"native": ["Windows", "Linux", "macOS"], "browser": ["desktop", "tablet", "phone"],
                   "styling_contract": "The same table_style and stable row_ids on native and web"},
        authoring={
            "workflow": ["activity_list: reuse an existing board when appropriate", "activity_catalog(kind): discover component schema and presets",
                         "activity_template: optional starting board; freeform is supported", "activity_validate: read-only preflight before publication",
                         "activity_publish: exact expected_revision and request_id", "activity_update_card: update only changing card properties",
                         "activity_events/activity_replies: read recipient-scoped operator input", "activity_acknowledge: exact input ID after reading"],
            "defaults": ["Lead with outcome, state and next action", "Prefer a small number of useful cards",
                         "Use named presets and theme colors; reserve strong borders and badges for emphasis",
                         "Supply stable row_ids for changing tables", "Use help and tooltips for detail",
                         "Use data_status for loading/empty/error/stale cues and label source/observation time honestly",
                         "Keep board/card IDs stable and respect operator dismissal",
                         "Input intent, reading, completion and approval are distinct"],
            "existing_sessions": "Already-running MCP clients may cache tools. Use the installed visor-console --activity CLI after Enable Prism access; do not restart sessions for discovery.",
            "schema_discovery": "Pass kind for one bounded schema, or include_schema=true for the complete document schema."})
    if include_schema:
        result["document_schema"] = {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object", "required": ["schema_version", "id", "title", "revision", "cards"],
            "properties": {"schema_version": {"const": 1}, "id": IDENTIFIER,
                "title": {"type": "string", "minLength": 1, "maxLength": policy.max_text_chars},
                "revision": {"type": "integer", "minimum": 1},
                "cards": {"type": "array", "maxItems": policy.max_cards,
                          "items": {"oneOf": [card_schema(name, policy) for name in all_kinds]}}}}
    return result
