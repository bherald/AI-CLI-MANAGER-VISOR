"""Portable, bounded table formatting and source-row helpers for Prism clients."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import Counter

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
_RGB = re.compile(r"#[0-9A-Fa-f]{6}\Z")
_CELL_KEYS = frozenset({"background", "text_color", "font_size", "bold", "align", "wrap",
                        "padding", "border", "badge", "tooltip"})
_BORDER_KEYS = frozenset({"color", "width", "style"})
_DEFAULT_OPTIONS = {"density": "comfortable", "striped": False, "grid": "all", "border": {},
                    "header": {}, "columns": [], "rows": [], "cells": [],
                    "sortable": False, "filterable": False, "copyable": False}


def _object(value, allowed, label):
    if not isinstance(value, dict) or value.keys() - allowed:
        raise ValueError(label + " must contain only its declared properties")


def _text(value, label, policy, *, nonempty=False, limit=None):
    maximum = min(policy.max_text_chars, limit) if limit is not None else policy.max_text_chars
    if (not isinstance(value, str) or len(value) > maximum or "\x00" in value
            or nonempty and not value.strip()):
        raise ValueError(label + " must be bounded usable text")


def _number(value, low, high, label):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and low <= value <= high
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(label + f" must be a finite number between {low} and {high}")


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(label + f" must be an integer between {low} and {high}")


def _color(value, label):
    if not isinstance(value, str) or not _RGB.fullmatch(value):
        raise ValueError(label + " must use #RRGGBB")


def _border(value):
    _object(value, _BORDER_KEYS, "Table border")
    if "color" in value:
        _color(value["color"], "Border color")
    if "width" in value:
        _number(value["width"], 0, 8, "Border width")
    if "style" in value and value["style"] not in ("solid", "dashed", "dotted", "none"):
        raise ValueError("Border style must be solid, dashed, dotted or none")


def _cell_style(value, policy, *, extra=frozenset()):
    from .activity_components import STATES
    _object(value, _CELL_KEYS | extra, "Table cell style")
    for key in ("background", "text_color"):
        if key in value:
            _color(value[key], key)
    if "font_size" in value:
        _integer(value["font_size"], 10, 28, "Table font size")
    if "padding" in value:
        _integer(value["padding"], 0, 16, "Table cell padding")
    for key in ("bold", "wrap"):
        if key in value and type(value[key]) is not bool:
            raise ValueError(key + " must be a boolean")
    if "align" in value and value["align"] not in ("left", "center", "right"):
        raise ValueError("Table alignment must be left, center or right")
    if "border" in value:
        _border(value["border"])
    if "badge" in value and value["badge"] not in STATES:
        raise ValueError("Table badge must be a declared status")
    if "tooltip" in value:
        _text(value["tooltip"], "Table tooltip", policy, limit=1000)


def validate_table(card, policy):
    """Validate data and styling; style coordinates always address source data."""
    columns, rows = card.get("columns"), card.get("rows")
    limit = policy.max_items_per_card
    if not isinstance(columns, list) or not 0 < len(columns) <= limit:
        raise ValueError("Table requires a bounded, nonempty list of columns")
    if not isinstance(rows, list) or len(rows) > limit:
        raise ValueError("Table rows must be a bounded list")
    for column in columns:
        _text(column, "Table column", policy, nonempty=True)
    items = len(columns)
    for row in rows:
        if not isinstance(row, list) or len(row) != len(columns):
            raise ValueError("Table rows must match the columns")
        items += len(row)
        if items > limit:
            raise ValueError("Table exceeds the configured item limit")
        for cell in row:
            if isinstance(cell, str):
                _text(cell, "Table cell", policy)
            elif cell is not None and type(cell) is not bool:
                _number(cell, -1e15, 1e15, "Table cell")
    if "row_ids" in card:
        identities = card["row_ids"]
        if not isinstance(identities, list) or len(identities) != len(rows):
            raise ValueError("Table row_ids must match its row count")
        if any(not isinstance(identity, str) or not _ID.fullmatch(identity) for identity in identities):
            raise ValueError("Table row_ids must use safe stable identifiers")
        if len(identities) != len(set(identities)):
            raise ValueError("Table row_ids must be unique")
    if "table_style" not in card:
        return
    style = card["table_style"]
    _object(style, _DEFAULT_OPTIONS.keys(), "Table style")
    if "density" in style and style["density"] not in ("compact", "comfortable"):
        raise ValueError("Table density must be compact or comfortable")
    if "grid" in style and style["grid"] not in ("all", "horizontal", "vertical", "none"):
        raise ValueError("Table grid must be all, horizontal, vertical or none")
    for key in ("striped", "sortable", "filterable", "copyable"):
        if key in style and type(style[key]) is not bool:
            raise ValueError("Table " + key + " must be a boolean")
    if "border" in style:
        _border(style["border"])
    if "header" in style:
        _cell_style(style["header"], policy)
    overrides = 0
    for key, coordinates in (("columns", ("column",)), ("rows", ("row",)), ("cells", ("row", "column"))):
        if key not in style:
            continue
        entries = style[key]
        if not isinstance(entries, list):
            raise ValueError("Table " + key + " styles must be a bounded list")  # noqa: TRY004 -- JSON schema validation uses ValueError
        overrides += len(entries)
        if overrides > limit:
            raise ValueError("Table style overrides exceed the configured item limit")
        seen = set()
        for entry in entries:
            extra = frozenset(coordinates) | ({"width"} if key == "columns" else set())
            _cell_style(entry, policy, extra=extra)
            for coordinate in coordinates:
                size = len(rows) if coordinate == "row" else len(columns)
                _integer(entry.get(coordinate), 0, size - 1, "Table source " + coordinate)
            identity = tuple(entry[coordinate] for coordinate in coordinates)
            if identity in seen:
                raise ValueError("Duplicate table " + key + " style coordinates")
            seen.add(identity)
            if "width" in entry:
                _integer(entry["width"], 80, 800, "Table column width")


def table_options(card):
    """Return independent defaults; unspecified styling continues to inherit theme."""
    declared = card.get("table_style", {})
    _object(declared, _DEFAULT_OPTIONS.keys(), "Table style")
    return {**copy.deepcopy(_DEFAULT_OPTIONS), **copy.deepcopy(declared)}


def cell_style(card, row, column, header=False):
    """Partial style, with source coordinates and nested border inheritance."""
    declared = card.get("table_style", {})
    _object(declared, _DEFAULT_OPTIONS.keys(), "Table style")
    merged = {}

    def overlay(value):
        for key, item in value.items():
            if key not in _CELL_KEYS:
                continue
            if key == "border":
                if item:
                    merged["border"] = {**merged.get("border", {}), **copy.deepcopy(item)}
            else:
                merged[key] = copy.deepcopy(item)

    overlay({"border": declared.get("border", {})})
    if header:
        overlay(declared.get("header", {}))
    for entry in declared.get("columns", []):
        if entry["column"] == column:
            overlay(entry)
    if not header:
        for entry in declared.get("rows", []):
            if entry["row"] == row:
                overlay(entry)
        for entry in declared.get("cells", []):
            if entry["row"] == row and entry["column"] == column:
                overlay(entry)
    return merged


def row_keys(card):
    """Use explicit IDs, or content fingerprints plus duplicate occurrences."""
    if "row_ids" in card:
        return list(card["row_ids"])
    seen, keys = Counter(), []
    for row in card["rows"]:
        raw = json.dumps(row, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        seen[fingerprint] += 1
        keys.append(f"row-{fingerprint}-{seen[fingerprint]}")
    return keys


def display_cell(value):
    if value is None:
        return ""
    if type(value) is bool:
        return "Yes" if value else "No"
    if type(value) is float and value.is_integer():
        return str(int(value))
    return str(value)


def copy_tsv(card, row_indices):
    """Copy distinct source rows in caller order, preserving literal cell content."""
    chosen, seen = [], set()
    for row in row_indices:
        _integer(row, 0, len(card["rows"]) - 1, "Copied source row")
        if row not in seen:
            chosen.append(row)
            seen.add(row)
    def quoted(value):
        text = display_cell(value)
        return '"' + text.replace('"', '""') + '"' if any(char in text for char in '\t\r\n"') else text
    return "\n".join("\t".join(quoted(value) for value in card["rows"][row]) for row in chosen)


def table_presets():
    """Optional complete style dictionaries; agents may compose their own."""
    overrides = {
        "clean": {"grid": "horizontal", "header": {"bold": True}, "copyable": True},
        "project": {"density": "compact", "striped": True, "grid": "horizontal", "header": {
            "background": "#263A46", "text_color": "#E7EFEA", "bold": True},
            "sortable": True, "filterable": True, "copyable": True},
        "metrics": {"density": "compact", "grid": "none", "header": {"bold": True, "align": "right"},
            "columns": [{"column": 0, "align": "left"}], "sortable": True, "copyable": True},
        "risk": {"striped": True, "grid": "horizontal", "header": {
            "background": "#503D29", "text_color": "#F4E8D6", "bold": True},
            "sortable": True, "filterable": True, "copyable": True},
    }
    return {name: table_options({"table_style": style}) for name, style in overrides.items()}


def table_style_schema(policy=None):
    """Inline JSON Schema, suitable for embedding in complete card schemas."""
    from .activity_components import STATES
    maximum = policy.max_items_per_card if policy is not None else 1000
    text_limit = min(1000, policy.max_text_chars) if policy is not None else 1000
    rgb = {"type": "string", "minLength": 7, "maxLength": 7, "pattern": "^#[0-9A-Fa-f]{6}$"}
    border = {"type": "object", "additionalProperties": False, "properties": {
        "color": rgb, "width": {"type": "number", "minimum": 0, "maximum": 8},
        "style": {"type": "string", "enum": ["solid", "dashed", "dotted", "none"]}}}
    properties = {"background": rgb, "text_color": rgb,
        "font_size": {"type": "integer", "minimum": 10, "maximum": 28},
        "bold": {"type": "boolean"}, "align": {"type": "string", "enum": ["left", "center", "right"]},
        "wrap": {"type": "boolean"}, "padding": {"type": "integer", "minimum": 0, "maximum": 16},
        "border": border, "badge": {"type": "string", "enum": list(STATES)},
        "tooltip": {"type": "string", "maxLength": text_limit, "pattern": "^[^\\u0000]*$",
                    "description": "Literal plain text, never HTML or executable content"}}
    cell = {"type": "object", "additionalProperties": False, "properties": properties}
    schema = {"type": "object", "additionalProperties": False, "properties": {
        "density": {"type": "string", "enum": ["compact", "comfortable"], "default": "comfortable"},
        "striped": {"type": "boolean", "default": False},
        "grid": {"type": "string", "enum": ["all", "horizontal", "vertical", "none"], "default": "all"},
        "border": border, "header": cell,
        **{key: {"type": "boolean", "default": False} for key in ("sortable", "filterable", "copyable")}},
        "description": "Optional portable styling. Unspecified properties inherit the active theme; "
                       "source-index overrides remain attached to data through local sorting/filtering. "
                       "The aggregate number of column, row and cell overrides is bounded by max_items_per_card."}
    for key, coordinates in (("columns", ("column",)), ("rows", ("row",)), ("cells", ("row", "column"))):
        item = {"type": "object", "additionalProperties": False, "required": list(coordinates),
                "properties": {**properties, **{coordinate: {"type": "integer", "minimum": 0,
                    "description": "Original source index; must be inside the declared data"} for coordinate in coordinates}}}
        if key == "columns":
            item["properties"]["width"] = {"type": "integer", "minimum": 80, "maximum": 800}
        schema["properties"][key] = {"type": "array", "maxItems": maximum, "items": item,
                                    "description": "Coordinates must be distinct within this override list"}
    return copy.deepcopy(schema)
