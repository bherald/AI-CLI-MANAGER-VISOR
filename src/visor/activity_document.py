"""Validated local activity boards and private reply files; no session transport."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
import uuid
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath


@dataclass(frozen=True)
class ActivityPolicy:
    max_document_bytes: int = 2 * 1024 * 1024
    max_image_bytes: int = 10 * 1024 * 1024
    max_image_pixels: int = 40_000_000
    max_cards: int = 100
    max_text_chars: int = 50_000
    max_answer_chars: int = 10_000
    max_items_per_card: int = 1000
    max_drawing_points: int = 10_000

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{field.name} must be a positive integer")


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
_RASTERS = {".png": {b"png"}, ".jpg": {b"jpeg", b"jpg"}, ".jpeg": {b"jpeg", b"jpg"},
            ".webp": {b"webp"}, ".gif": {b"gif"}, ".bmp": {b"bmp"}}


def _identifier(value, label):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{label} must be a safe stable identifier")
    return value


def _text(value, label, policy, *, nonempty=False, limit=None):
    if not isinstance(value, str) or len(value) > (limit or policy.max_text_chars):
        raise ValueError(f"{label} must be text within the configured limit")
    if "\x00" in value or (nonempty and not value.strip()):
        raise ValueError(f"{label} must contain usable text")
    return value


def _number(value, label):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{label} must be a finite number")
    return value


def _items(value, label, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{label} must be a list within the configured limit")
    return value


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")  # noqa: TRY004 -- invalid JSON value
    return value


def _unique_objects(items, label):
    seen = set()
    for item in items:
        _object(item, label)
        identity = _identifier(item.get("id"), label + " id")
        if identity in seen:
            raise ValueError(f"Duplicate {label} id: {identity}")
        seen.add(identity)
    return seen


def _relative_image(value, policy):
    _text(value, "image path", policy, nonempty=True)
    path = Path(value)
    if (path.is_absolute() or PureWindowsPath(value).drive or "\\" in value
            or ":" in value or ".." in path.parts):
        raise ValueError("Image path must stay relative to the activity document")
    if path.suffix.lower() not in _RASTERS:
        raise ValueError("Image must use an allowed raster format")
    return path


def _validate(document, policy):
    _object(document, "document")
    if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise ValueError("Unsupported activity document schema_version")
    _identifier(document.get("id"), "board id")
    _text(document.get("title"), "board title", policy, nonempty=True)
    if type(document.get("revision")) is not int or document["revision"] <= 0:
        raise ValueError("Document revision must be a positive integer")
    if "session_link" in document:
        link = _object(document["session_link"], "session_link")
        _text(link.get("id"), "session display id", policy, nonempty=True)
        _text(link.get("label"), "session display label", policy, nonempty=True)
    if "parent_record" in document:
        _text(document["parent_record"], "parent_record", policy)
    cards = _items(document.get("cards"), "cards", policy.max_cards)
    _unique_objects(cards, "card")
    for card in cards:
        _text(card.get("title"), "card title", policy, nonempty=True)
        if "caption" in card:
            _text(card["caption"], "card caption", policy)
        if "geometry" in card:
            geometry = _items(card["geometry"], "geometry", 4)
            if len(geometry) != 4:
                raise ValueError("Geometry requires x, y, width, height")
            if any(type(value) is not int or abs(value) > 100_000 for value in geometry):
                raise ValueError("Card geometry must use integer coordinates within 100000")
            if geometry[2] < 100 or geometry[3] < 80:
                raise ValueError("Card geometry requires width at least 100 and height at least 80")
        kind = card.get("kind")
        if kind == "text":
            _text(card.get("html"), "card html", policy)
        elif kind == "sketch":
            canvas = _items(card.get("canvas"), "sketch canvas", 2)
            if len(canvas) != 2:
                raise ValueError("Sketch canvas requires width and height")
            for dimension in canvas:
                if not 1 <= _number(dimension, "canvas dimension") <= 100_000:
                    raise ValueError("Sketch canvas dimensions must be between 1 and 100000")
            strokes = _items(card.get("strokes"), "sketch strokes", policy.max_items_per_card)
            total = 0
            for stroke in strokes:
                _object(stroke, "sketch stroke")
                color = _text(stroke.get("color"), "stroke color", policy, nonempty=True)
                if not re.fullmatch(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?", color):
                    raise ValueError("Stroke color must be a hex RGB or ARGB value")
                if not 0 < _number(stroke.get("width"), "stroke width") <= min(canvas):
                    raise ValueError("Stroke width must be positive and fit the canvas")
                points = _items(stroke.get("points"), "stroke points", policy.max_drawing_points)
                if not points:
                    raise ValueError("A stroke requires at least one point")
                total += len(points)
                if total > policy.max_drawing_points:
                    raise ValueError("Sketch exceeds the configured drawing point limit")
                for point in points:
                    if len(_items(point, "stroke point", 2)) != 2:
                        raise ValueError("Stroke points require x and y")
                    for coordinate, dimension in zip(point, canvas, strict=True):
                        if not 0 <= _number(coordinate, "stroke coordinate") <= dimension:
                            raise ValueError("Stroke points must stay inside the canvas")
        elif kind == "image":
            _relative_image(card.get("path"), policy)
        elif kind == "diagram":
            nodes = _items(card.get("nodes"), "diagram nodes", policy.max_items_per_card)
            node_ids = _unique_objects(nodes, "diagram node")
            for node in nodes:
                _text(node.get("label"), "node label", policy)
                for key in ("x", "y", "w", "h"):
                    _number(node.get(key), "node " + key)
                    if abs(node[key]) > 100_000:
                        raise ValueError("Diagram coordinates must be within 100000")
                if node["w"] <= 0 or node["h"] <= 0:
                    raise ValueError("Node width and height must be positive")
                if "color" in node:
                    color = _text(node["color"], "node color", policy, nonempty=True)
                    if not re.fullmatch(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?", color):
                        raise ValueError("Node color must be a hex RGB or ARGB value")
            for edge in _items(card.get("edges"), "diagram edges", policy.max_items_per_card):
                _object(edge, "edge")
                source = _identifier(edge.get("from"), "edge source")
                target = _identifier(edge.get("to"), "edge target")
                if source not in node_ids or target not in node_ids:
                    raise ValueError("Diagram edge must reference existing nodes")
                if "label" in edge:
                    _text(edge["label"], "edge label", policy)
        elif kind == "chart":
            labels = _items(card.get("labels"), "chart labels", policy.max_items_per_card)
            values = _items(card.get("values"), "chart values", policy.max_items_per_card)
            if len(labels) != len(values):
                raise ValueError("Chart labels and values must have the same length")
            for label in labels:
                _text(label, "chart label", policy)
            for value in values:
                _number(value, "chart value")
            if "unit" in card:
                _text(card["unit"], "chart unit", policy)
        elif kind == "question":
            _text(card.get("prompt"), "question prompt", policy, nonempty=True)
            if "choices" in card:
                choices = _items(card["choices"], "choices", policy.max_items_per_card)
                for choice in choices:
                    _text(choice, "choice", policy, nonempty=True, limit=policy.max_answer_chars)
                if not choices or len(set(choices)) != len(choices):
                    raise ValueError("Question choices must be nonempty and distinct")
        else:
            raise ValueError("Unknown activity card kind")
    return document


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path, limit):
    if not path.is_file():
        raise ValueError("Activity input must be a regular file")
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Activity input must be a regular file")
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Activity input exceeds the configured byte limit")
    def invalid_constant(value):
        raise ValueError(f"Nonfinite JSON number: {value}")
    try:
        return json.loads(content.decode("utf-8"), object_pairs_hook=_json_object,
                          parse_constant=invalid_constant)
    except RecursionError as exc:
        raise ValueError("Activity input nesting is excessive") from exc


def read_document(path, policy=None):
    """Read a bounded document. Session labels are hints, never authenticated peers."""
    policy = policy or ActivityPolicy()
    return _validate(_read_json(Path(path), policy.max_document_bytes), policy)


def resolve_image(document_path, relative_path, policy=None):
    """Resolve a local raster within the document directory, including symlink checks."""
    policy = policy or ActivityPolicy()
    relative = _relative_image(relative_path, policy)
    directory = Path(document_path).resolve(strict=True).parent
    image = (directory / relative).resolve(strict=True)
    if not image.is_relative_to(directory) or not image.is_file():
        raise ValueError("Image must be a regular file inside the activity document directory")
    if image.stat().st_size > policy.max_image_bytes:
        raise ValueError("Image exceeds the configured byte limit")
    from PySide6.QtGui import QImageReader
    reader = QImageReader(str(image))
    size = reader.size()
    if (not reader.canRead() or bytes(reader.format()).lower() not in _RASTERS[relative.suffix.lower()]
            or size.width() <= 0 or size.height() <= 0):
        raise ValueError("Image header is invalid or does not match its raster suffix")
    if size.width() * size.height() > policy.max_image_pixels:
        raise ValueError("Image exceeds the configured pixel limit")
    return image


def _reply_directory(state_dir, *, create):
    base = Path(state_dir)
    if base.is_symlink():
        raise ValueError("Activity reply state directory must not be a symlink")
    replies = base / "replies"
    if replies.is_symlink():
        raise ValueError("Activity replies directory must not be a symlink")
    if create:
        created = not replies.exists()
        replies.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "nt":
            from .data_security import ensure_windows_private
            ensure_windows_private(replies, created=created)
        elif stat.S_IMODE(replies.stat().st_mode) & 0o077:
            raise PermissionError("Activity replies directory must be private to its owner")
    return replies


def save_reply(state_dir, document, card, answer, policy=None):
    """Persist an operator's local preview answer; this never sends to a session."""
    policy = policy or ActivityPolicy()
    _validate(document, policy)
    _object(card, "question card")
    matching = next((item for item in document["cards"] if item["id"] == card.get("id")), None)
    if matching != card or card.get("kind") != "question":
        raise ValueError("Reply card must be the exact question in this document revision")
    _text(answer, "answer", policy, nonempty=True, limit=policy.max_answer_chars)
    event = {"schema_version": 1, "id": str(uuid.uuid4()), "board_id": document["id"],
             "card_id": card["id"], "document_revision": document["revision"],
             "prompt_sha256": hashlib.sha256(card["prompt"].encode("utf-8")).hexdigest(),
             "answer": answer, "created_at": datetime.now(UTC).isoformat(),
             "origin": "operator_local_preview", "recipient_hint": document.get("session_link")}
    payload = (json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(payload) > policy.max_document_bytes:
        raise ValueError("Reply exceeds the configured byte limit")
    replies = _reply_directory(state_dir, create=True)
    fd, temporary = tempfile.mkstemp(prefix=".reply-", suffix=".tmp", dir=replies)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, replies / (event["id"] + ".json"))
    finally:
        Path(temporary).unlink(missing_ok=True)
    return event


def list_replies(state_dir, board_id=None, policy=None):
    """List local answer events; their presence proves no transport or acknowledgment."""
    policy = policy or ActivityPolicy()
    if board_id is not None:
        _identifier(board_id, "board id")
    replies = _reply_directory(state_dir, create=False)
    if not replies.exists():
        return []
    result = []
    for path in replies.glob("*.json"):
        if path.is_symlink():
            raise ValueError("Reply files must not be symlinks")
        event = _object(_read_json(path, policy.max_document_bytes), "reply")
        if (event.get("origin") != "operator_local_preview" or type(event.get("schema_version")) is not int
                or event["schema_version"] != 1 or event.get("id") != path.stem):
            raise ValueError("Invalid local reply identity or origin")
        if str(uuid.UUID(event["id"])) != event["id"]:
            raise ValueError("Invalid reply event UUID")
        _identifier(event.get("board_id"), "reply board id")
        _identifier(event.get("card_id"), "reply card id")
        _text(event.get("answer"), "reply answer", policy, nonempty=True, limit=policy.max_answer_chars)
        if type(event.get("document_revision")) is not int or event["document_revision"] <= 0:
            raise ValueError("Invalid reply document revision")
        if not isinstance(event.get("prompt_sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", event["prompt_sha256"]):
            raise ValueError("Invalid reply prompt hash")
        stamp = _text(event.get("created_at"), "reply timestamp", policy, nonempty=True)
        if datetime.fromisoformat(stamp).tzinfo is None:
            raise ValueError("Reply timestamp must include its time zone")
        if event.get("recipient_hint") is not None:
            hint = _object(event["recipient_hint"], "recipient_hint")
            _text(hint.get("id"), "recipient display id", policy, nonempty=True)
            _text(hint.get("label"), "recipient display label", policy, nonempty=True)
        if board_id is None or event["board_id"] == board_id:
            result.append(event)
    return sorted(result, key=lambda event: (event["created_at"], event["id"]))
