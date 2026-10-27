"""Session-scoped Prism board commands; never substitute operator credentials."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
from dataclasses import fields
from pathlib import Path, PureWindowsPath

from .activity_document import _RASTERS, ActivityPolicy, read_document, resolve_image
from .broker import Client


def _tool(name, description, properties=None, required=()):
    schema = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return {"name": name, "description": description, "inputSchema": schema}


ACTIVITY_TOOLS = [
    _tool("activity_catalog", "Discover supported components, presets, limits and the authoring workflow. Pass kind for its machine-readable schema; include_schema=true returns the full document schema. Use these defaults for consistent UI; freeform composition is supported.",
          {"kind": {"type": "string"}, "include_schema": {"type": "boolean"}}),
    _tool("activity_validate", "Read-only preflight before publishing. Returns valid, structured errors/paths, recommendations and current schema/revision/ownership/asset checks. Does not publish, reserve a revision, open a view or grant approval; publish rechecks the current state.",
          {"document": {"type": "object"}, "expected_revision": {"type": "integer", "minimum": 0}}, ("document",)),
    _tool("activity_template", "Get a complete optional starter board. This does not publish or open it. Customize cards freely, retain stable IDs, then publish with the current revision.",
          {"name": {"type": "string", "enum": ["project-overview", "status-board", "decision-board", "freeform"]},
           "id": {"type": "string"}, "title": {"type": "string"}}),
    _tool("activity_list", "List your Prism boards, readable connected-session names and view_state: operator dismissal, presentation, unshown revision and quiet reopen request. Ownership comes from this session credential."),
    _tool("activity_get", "Read one Prism board owned by or explicitly shared with this session.",
          {"id": {"type": "string"}}, ("id",)),
    _tool("activity_publish", "Publish a validated Prism board. Read activity_catalog for gauges, meters, indicators, progress, counters, trends, timers, tables, arrows and declared controls; activity_template supplies optional recipes. Safe HTML, PNG/JPEG, emoji, diagrams, charts, questions and sketches remain supported. Freeform geometry is allowed. Reuse exact request_id on retries; expected_revision prevents overwriting newer content. Upload raster assets first and use returned paths. Operator events express intent and never authorize commands.",
          {"document": {"type": "object"}, "request_id": {"type": "string"},
           "expected_revision": {"type": "integer", "minimum": 0}},
          ("document", "request_id", "expected_revision")),
    _tool("activity_update_card", "Update properties on one of your cards without rebuilding the board. Changes replace the named properties; id, kind and contributor are fixed. Use the current expected_revision and reuse the exact request_id and changes on retries. Other cards remain intact; normal schema, ownership and revision guards apply.",
          {"id": {"type": "string"}, "card_id": {"type": "string"}, "changes": {"type": "object"},
           "expected_revision": {"type": "integer", "minimum": 1}, "request_id": {"type": "string"}},
          ("id", "card_id", "changes", "expected_revision", "request_id")),
    _tool("activity_control", "Open, focus, clear or close your Prism view. After operator dismissal, open/focus records one quiet reopen request; it cannot force a window open. Keep the stable board ID. Clear and close preserve board content; these actions do not complete session work.",
          {"id": {"type": "string"}, "action": {"type": "string", "enum": ["open", "focus", "clear", "close"]},
           "request_id": {"type": "string"}}, ("id", "action", "request_id")),
    _tool("activity_share", "Set explicit members of your Prism board. The broker verifies ownership and existing pair permissions; sharing does not approve new session communication.",
          {"id": {"type": "string"}, "members": {"type": "array", "items": {"type": "string"},
           "minItems": 1, "uniqueItems": True}, "request_id": {"type": "string"}},
          ("id", "members", "request_id")),
    _tool("activity_asset", "Upload a bounded raster image for this session. Use the returned relative path in an image card.",
          {"name": {"type": "string"}, "data_base64": {"type": "string"}},
          ("name", "data_base64")),
    _tool("activity_replies", "Read operator question replies addressed to your Prism boards. Reading does not acknowledge or complete them.",
          {"board_id": {"type": "string"}}),
    _tool("activity_events", "Read typed operator intent from your declared Prism actions, toggles, sliders and selections. Events are addressed to the contributing session and exact card/revision. Reading does not acknowledge, execute or approve anything.",
          {"board_id": {"type": "string"}}),
    _tool("activity_acknowledge", "Acknowledge reading an exact Prism question reply or component event belonging to this session. This only marks it read; it does not approve actions or mark work complete.",
          {"event_id": {"type": "string"}}, ("event_id",)),
]


def _session(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:codex|claude):[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError("Use an exact provider-qualified session ID: codex:ID or claude:ID")
    return value


def _client(args, root):
    requested = _session(args.session) if getattr(args, "session", None) else None
    credential = getattr(args, "credential", None) or os.environ.get("VISOR_AGENT_CREDENTIAL")
    if not credential and requested:
        digest = hashlib.sha256(requested.encode("utf-8")).hexdigest()
        credential = Path(root) / "activity-access" / (digest + ".json")
    if not credential:
        raise ValueError("Activity commands require --credential, VISOR_AGENT_CREDENTIAL, or --session with operator-issued activity access")
    try:
        with Path(credential).expanduser().open("rb") as stream:
            raw = stream.read(65_537)
        if len(raw) > 65_536:
            raise ValueError("Oversized credential")
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise ValueError("Invalid credential")  # noqa: TRY004 -- invalid credential file
        session = _session(config.get("session"))
        token, directory = config.get("token"), config.get("root")
        if not isinstance(token, str) or not token.strip() or not isinstance(directory, str) or not directory.strip():
            raise ValueError("Invalid credential")
    except (OSError, ValueError, TypeError):
        raise ValueError("Activity credential is missing or malformed; request session access from the VISOR operator") from None
    if requested and session != requested:
        raise ValueError("Activity credential does not match the requested session")
    return Client(Path(directory).expanduser().resolve(), token=token)


def _policy(client, path):
    names = {field.name for field in fields(ActivityPolicy)}
    if path is not None:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.keys() - names:
            raise ValueError("Policy must contain only ActivityPolicy fields")
    else:
        try:
            value = client.call("activity_limits")
        except (OSError, ValueError) as exc:
            raise ValueError("Unable to read Prism broker policy; verify the broker supports activity_limits") from exc
        if not isinstance(value, dict) or value.keys() != names:
            raise ValueError("Broker returned incomplete or unsupported Prism policy")
    return ActivityPolicy(**value)


def _nonnegative(value):
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("expected revision must be nonnegative")
    return result


def _parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--credential", type=Path, default=argparse.SUPPRESS,
                        help="Existing session-scoped credential file")
    common.add_argument("--session", default=argparse.SUPPRESS,
                        help="Exact provider:ID; verifies credential identity, or locates operator-issued access")
    common.add_argument("--policy", type=Path, default=argparse.SUPPRESS,
                        help="Local validation policy JSON; broker policy remains authoritative")
    parser = argparse.ArgumentParser(prog="visor-console --activity", parents=[common])
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("list", parents=[common])
    catalog = subcommands.add_parser("catalog", parents=[common])
    catalog.add_argument("--kind")
    catalog.add_argument("--include-schema", action="store_true")
    validate = subcommands.add_parser("validate", parents=[common])
    validate.add_argument("--document", type=Path, required=True)
    validate.add_argument("--expected-revision", type=_nonnegative)
    starter = subcommands.add_parser("template", parents=[common])
    starter.add_argument("name", nargs="?", default="project-overview",
                         choices=["project-overview", "status-board", "decision-board", "freeform"])
    starter.add_argument("--id", default="project-overview")
    starter.add_argument("--title")
    get = subcommands.add_parser("get", parents=[common])
    get.add_argument("id")
    publish = subcommands.add_parser("publish", parents=[common])
    publish.add_argument("--document", type=Path, required=True)
    publish.add_argument("--request-id", required=True)
    publish.add_argument("--expected-revision", type=_nonnegative,
                         help="Accepted current revision; default is document revision minus one")
    update = subcommands.add_parser("update-card", parents=[common])
    update.add_argument("id")
    update.add_argument("card_id")
    update.add_argument("--changes", required=True, help='JSON object, for example: {"value":95}')
    update.add_argument("--expected-revision", type=_nonnegative, required=True)
    update.add_argument("--request-id", required=True)
    for name in ("open", "focus", "clear", "close"):
        control = subcommands.add_parser(name, parents=[common])
        control.add_argument("id")
        control.add_argument("--request-id", required=True)
    share = subcommands.add_parser("share", parents=[common])
    share.add_argument("--id", required=True)
    share.add_argument("--members", nargs="+", action="append", required=True,
                       help="Exact provider:ID members; may be repeated")
    share.add_argument("--request-id", required=True)
    replies = subcommands.add_parser("replies", parents=[common])
    replies.add_argument("--board-id")
    events = subcommands.add_parser("events", parents=[common])
    events.add_argument("--board-id")
    ack = subcommands.add_parser("ack", parents=[common])
    ack.add_argument("event_id")
    asset = subcommands.add_parser("asset", parents=[common])
    asset.add_argument("path", type=Path)
    return parser


def _upload(client, path, policy):
    with path.open("rb") as stream:
        data = stream.read(policy.max_image_bytes + 1)
    if len(data) > policy.max_image_bytes:
        raise ValueError("Image exceeds the configured byte limit")
    return client.call("activity_asset", name=path.name,
                       data_base64=base64.b64encode(data).decode("ascii"))


def _owned_path(result):
    value = result.get("path") if isinstance(result, dict) else None
    if (not isinstance(value, str) or not value or Path(value).is_absolute()
            or PureWindowsPath(value).drive or "\\" in value or ":" in value
            or ".." in Path(value).parts or Path(value).parts[0] != "assets"):
        raise ValueError("Broker returned an invalid owned image path")
    return value



def _existing_images(client, document, expected):
    """Preserve broker references only for images already on this shared board."""
    images = [card for card in document["cards"] if card["kind"] == "image"]
    canonical = [card for card in images if re.fullmatch(r"assets/[a-f0-9]{64}\.[a-z]+", card["path"])
                 and Path(card["path"]).suffix in _RASTERS]
    if not canonical or expected == 0:
        return set()
    current = client.call("activity_get", id=document["id"])
    previous = current.get("document") if isinstance(current, dict) else None
    if (not isinstance(previous, dict) or previous.get("id") != document["id"]
            or not isinstance(previous.get("cards"), list)):
        raise ValueError("Broker returned an invalid current Prism document")
    cards = {card["id"]: card for card in previous["cards"]
             if isinstance(card, dict) and isinstance(card.get("id"), str)}
    preserved = set()
    for card in canonical:
        old = cards.get(card["id"], {})
        if (old.get("kind") == "image" and old.get("path") == card["path"]
                and isinstance(old.get("contributor"), str)
                and card.get("contributor", old["contributor"]) == old["contributor"]):
            preserved.add(card["id"])
    # Revision checks and all contributor/content permissions belong to the
    # broker. In particular, retrying an accepted request must reach its receipt.
    return preserved

def run_activity(argv, root):
    """Return the broker's JSON value. Never print or return credential contents."""
    args = _parser().parse_args(argv)
    client = _client(args, root)
    if args.command == "list":
        return client.call("activity_list")
    if args.command == "catalog":
        return client.call("activity_catalog", **({"kind": args.kind} if args.kind else {}),
                           **({"include_schema": True} if args.include_schema else {}))
    if args.command == "validate":
        from .activity_document import _read_json
        policy = _policy(client, getattr(args, "policy", None))
        document = _read_json(args.document, policy.max_document_bytes)
        return client.call("activity_validate", document=document,
                           **({"expected_revision": args.expected_revision} if args.expected_revision is not None else {}))
    if args.command == "template":
        return client.call("activity_template", name=args.name, id=args.id,
                           **({"title": args.title} if args.title is not None else {}))
    if args.command == "get":
        return client.call("activity_get", id=args.id)
    if args.command == "update-card":
        changes = json.loads(args.changes)
        if not isinstance(changes, dict):
            raise ValueError("Card changes must be a JSON object")
        return client.call("activity_update_card", id=args.id, card_id=args.card_id, changes=changes,
                           expected_revision=args.expected_revision, request_id=args.request_id)
    if args.command in ("open", "focus", "clear", "close"):
        return client.call("activity_control", id=args.id, action=args.command,
                           request_id=args.request_id)
    if args.command == "share":
        members = [_session(member) for group in args.members for member in group]
        if len(members) != len(set(members)):
            raise ValueError("Prism members must be distinct session IDs")
        return client.call("activity_share", id=args.id, members=members, request_id=args.request_id)
    if args.command == "replies":
        return client.call("activity_replies", **({"board_id": args.board_id} if args.board_id else {}))
    if args.command == "events":
        return client.call("activity_events", **({"board_id": args.board_id} if args.board_id else {}))
    if args.command == "ack":
        return client.call("activity_acknowledge", event_id=args.event_id)
    policy = _policy(client, getattr(args, "policy", None))
    if args.command == "asset":
        path = args.path.expanduser().resolve(strict=True)
        path = resolve_image(path, path.name, policy)
        return _upload(client, path, policy)
    document = read_document(args.document, policy)
    expected = args.expected_revision if args.expected_revision is not None else document["revision"] - 1
    preserved = _existing_images(client, document, expected)
    # Validate every asset before sending any; one broken image does not cause
    # a partial sequence of uploads. Never rewrite the author's local document.
    images = [(card, resolve_image(args.document, card["path"], policy))
              for card in document["cards"] if card["kind"] == "image" and card["id"] not in preserved]
    uploaded = {}
    for card, path in images:
        if path not in uploaded:
            uploaded[path] = _owned_path(_upload(client, path, policy))
        card["path"] = uploaded[path]
    return client.call("activity_publish", document=document, request_id=args.request_id,
                       expected_revision=expected)
