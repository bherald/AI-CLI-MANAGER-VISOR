"""Read-only, point-in-time authoring feedback for scoped Prism agents."""
from __future__ import annotations

import json
from dataclasses import asdict

from .activity_document import _validate
from .store import PolicyError


def preflight(service, actor, document, expected_revision=None):
    from .activity_contract import CONTRACT_REVISION
    from .activity_store import _hash, _json, _stamp
    session = service._actor(actor)
    if session is None:
        raise PolicyError("Preflight requires an authenticated session owner")
    policy = service.policy()
    result = {"valid": False, "read_only": True, "contract_revision": CONTRACT_REVISION,
              "errors": [], "recommendations": [], "limits": asdict(policy),
              "checks": {name: "not_checked" for name in ("schema", "revision", "card_ownership", "assets")},
              "publication": "Publish rechecks current policy, permissions, assets and revision; preflight does not reserve or publish a board."}

    def error(code, path, message, retryable=False):
        result["errors"].append({"code": code, "path": path, "message": str(message), "retryable": retryable})

    try:
        raw = _json(document).encode("utf-8")
        candidate = json.loads(raw)
        if not isinstance(candidate, dict):
            raise TypeError("Activity document must be an object")
        result["document_sha256"] = _hash(raw)
        result["document_bytes"] = len(raw)
        if len(raw) > policy.max_document_bytes:
            error("document_limit", "/", "Document exceeds the current byte limit")
            return result
        candidate.pop("contributors", None)
        candidate.pop("contributor_labels", None)
        candidate.pop("last_editor", None)
        for card in candidate.get("cards", []) if isinstance(candidate.get("cards"), list) else []:
            if isinstance(card, dict):
                card.pop("contributor", None)
        candidate["session_link"] = {"id": actor, "label": session["name"]}
        _validate(candidate, policy)
    except (ValueError, TypeError, PolicyError, OverflowError) as exc:
        path = "/"
        if isinstance(locals().get("candidate"), dict) and isinstance(candidate.get("cards"), list):
            for index, card in enumerate(candidate["cards"]):
                try:
                    _validate({**candidate, "cards": [card]}, policy)
                except (ValueError, TypeError):
                    path = f"/cards/{index}"
                    break
        error("invalid_schema", path, exc)
        return result
    result["checks"]["schema"] = "passed"
    if expected_revision is None:
        expected_revision = candidate["revision"] - 1
    if type(expected_revision) is not int or expected_revision < 0:
        error("invalid_revision", "/revision", "Expected revision must be a nonnegative integer")
        return result
    result["expected_revision"] = expected_revision
    try:
        with service.store.connect() as db:
            db.execute("BEGIN")
            existing = service._publish_target(db, actor, candidate["id"])
            revision = existing["revision"] if existing else 0
            result["checked_revision"] = revision
            if expected_revision != revision or candidate["revision"] != revision + 1:
                error("revision_conflict", "/revision", "Read the current board and merge changes before a new publish", True)
                return result
            result["checks"]["revision"] = "passed"
            old = json.loads(db.execute("SELECT document FROM activity_revisions WHERE board_id=? AND revision=?",
                (candidate["id"], revision)).fetchone()[0]) if existing else None
            previous = {card["id"]: card for card in old["cards"]} if old else {}
            incoming = {card["id"]: card for card in candidate["cards"]}
            for identity, card in previous.items():
                if card["contributor"] != actor and incoming.get(identity) != {key: value for key, value in card.items() if key != "contributor"}:
                    error("card_ownership", "/cards", "Only a card's contributor may change or remove it")
                    return result
            for index, card in enumerate(candidate["cards"]):
                if card["id"] in previous and card["kind"] != previous[card["id"]]["kind"]:
                    error("card_kind_change", f"/cards/{index}/kind", "Use a new card ID when changing kind")
                    return result
                card["contributor"] = previous.get(card["id"], {}).get("contributor", actor)
                if card["kind"] == "table" and card.get("rows") and "row_ids" not in card:
                    result["recommendations"].append({"path": f"/cards/{index}/row_ids",
                        "message": "Provide stable row_ids to retain table selection when values or row order change."})
            result["checks"]["card_ownership"] = "passed"
            if old:
                candidate["session_link"] = old["session_link"]
            candidate["contributors"] = {card["id"]: card["contributor"] for card in candidate["cards"]}
            candidate["last_editor"] = actor
            candidate["updated_at"] = _stamp()
            _validate(candidate, policy)
            if len(_json(candidate).encode("utf-8")) > policy.max_document_bytes:
                error("document_limit", "/", "Document with broker ownership metadata exceeds the byte limit")
                return result
            service._assets(db, candidate, policy)
            result["checks"]["assets"] = "passed"
    except (PolicyError, ValueError, OSError) as exc:
        stage = "assets" if result["checks"]["card_ownership"] == "passed" else "access"
        error(stage + "_unavailable", "/cards" if stage == "assets" else "/id", exc)
        return result
    result["valid"] = True
    return result
