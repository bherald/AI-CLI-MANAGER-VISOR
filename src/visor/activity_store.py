"""Authenticated activity persistence. Broker identity is supplied by the caller."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path

from .activity_document import _RASTERS, ActivityPolicy, _identifier, _text, _validate
from .store import PolicyError

_ANY_CACHE = object()

ACTIVITY_TABLES = {"activity_boards", "activity_revisions", "activity_assets",
                   "activity_requests", "activity_replies", "activity_members", "activity_deleted", "activity_lifecycle"}

def _stamp():
    return datetime.now(UTC).isoformat()


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise PolicyError("Activity request must contain finite JSON values") from exc


def _hash(content):
    return hashlib.sha256(content).hexdigest()


class ActivityService:
    def __init__(self, store):
        # Broker construction runs on the main thread before transient HTTP
        # workers. Keep Qt image plugin factories attached to that stable thread.
        from PySide6.QtGui import QImageReader
        QImageReader.supportedImageFormats()
        self.store = store
        self.root = store.path.parent.resolve()
        self._projection_cache = {}
        self._cache_lock = threading.Lock()
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS activity_boards(
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                    revision INTEGER NOT NULL, updated TEXT NOT NULL,
                    view_action TEXT NOT NULL, view_seq INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS activity_revisions(
                    board_id TEXT NOT NULL REFERENCES activity_boards(id), revision INTEGER NOT NULL,
                    document TEXT NOT NULL, sha256 TEXT NOT NULL, created TEXT NOT NULL,
                    PRIMARY KEY(board_id,revision));
                CREATE TABLE IF NOT EXISTS activity_assets(
                    owner TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
                    bytes INTEGER NOT NULL, created TEXT NOT NULL, content BLOB NOT NULL, PRIMARY KEY(owner,path));
                CREATE TABLE IF NOT EXISTS activity_requests(
                    actor TEXT NOT NULL, request_id TEXT NOT NULL, digest TEXT NOT NULL,
                    result TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(actor,request_id));
                CREATE TABLE IF NOT EXISTS activity_replies(
                    id TEXT PRIMARY KEY, board_id TEXT NOT NULL REFERENCES activity_boards(id),
                    card_id TEXT NOT NULL, revision INTEGER NOT NULL, answer TEXT NOT NULL,
                    prompt_sha256 TEXT NOT NULL, created TEXT NOT NULL, state TEXT NOT NULL, recipient TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS activity_members(
                    board_id TEXT NOT NULL REFERENCES activity_boards(id), session_id TEXT NOT NULL,
                    PRIMARY KEY(board_id,session_id));
                CREATE TABLE IF NOT EXISTS activity_deleted(
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, revision INTEGER NOT NULL, deleted_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS activity_lifecycle(
                    board_id TEXT PRIMARY KEY REFERENCES activity_boards(id),
                    operator_dismissed INTEGER NOT NULL DEFAULT 0,
                    presentation TEXT NOT NULL DEFAULT 'unknown',
                    presented_revision INTEGER NOT NULL DEFAULT 0,
                    reopen_requested INTEGER NOT NULL DEFAULT 0,
                    requested_by TEXT, requested_revision INTEGER NOT NULL DEFAULT 0,
                    observed_at TEXT, last_operator_open_seq INTEGER NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO activity_lifecycle(board_id) SELECT id FROM activity_boards;
                CREATE INDEX IF NOT EXISTS activity_board_owner ON activity_boards(owner,updated);
                CREATE INDEX IF NOT EXISTS activity_reply_board ON activity_replies(board_id,created);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(activity_lifecycle)")}
            if "last_operator_open_seq" not in columns:
                db.execute("ALTER TABLE activity_lifecycle ADD COLUMN last_operator_open_seq INTEGER NOT NULL DEFAULT 0")
                # Old control receipts retain the authenticated requester and sequence.
                # Recover explicit operator reopen ordering; never infer it from an agent action.
                for row in db.execute("SELECT result FROM activity_requests WHERE actor='operator'").fetchall():
                    result = json.loads(row["result"])
                    if (isinstance(result, dict) and result.get("view_action") in ("open", "focus")
                            and type(result.get("view_seq")) is int and result["view_seq"] > 0):
                        db.execute("UPDATE activity_lifecycle SET last_operator_open_seq=MAX(last_operator_open_seq,?) WHERE board_id=?",
                                   (result["view_seq"], result.get("id")))

    def policy(self):
        return self._policy_value(self.store.get_setting("activity_policy", {}))

    @staticmethod
    def _policy_value(settings):
        if not isinstance(settings, dict):
            raise PolicyError("Activity policy must be an object")
        try:
            return ActivityPolicy(**settings)
        except (TypeError, ValueError) as exc:
            raise PolicyError("Activity policy contains invalid or unsupported limits") from exc

    def _actor(self, actor):
        if actor is None:
            return None
        if not isinstance(actor, str) or not actor:
            raise PolicyError("An authenticated session identity is required")
        return self.store.session(actor)

    def reset_presentations(self):
        """A new broker cannot attest that its previous GUI is still displaying a board."""
        with self.store.connect(True) as db:
            db.execute("UPDATE activity_lifecycle SET presentation='unknown' WHERE presentation IN ('visible','hidden')")

    @staticmethod
    def _view_state(db, board):
        row = db.execute("SELECT * FROM activity_lifecycle WHERE board_id=?", (board["id"],)).fetchone()
        if row is None:
            raise PolicyError("Activity presentation state is missing")
        return {"operator_dismissed": bool(row["operator_dismissed"]), "presentation": row["presentation"],
                "presented_revision": row["presented_revision"],
                "unshown_revision": board["revision"] if board["revision"] > row["presented_revision"] else None,
                "reopen_requested": bool(row["reopen_requested"]), "requested_by": row["requested_by"],
                "requested_revision": row["requested_revision"], "observed_at": row["observed_at"],
                "last_operator_open_seq": row["last_operator_open_seq"]}

    def _members(self, db, identity):
        return [r[0] for r in db.execute("SELECT session_id FROM activity_members WHERE board_id=? ORDER BY session_id", (identity,))]

    def _allowed(self, db, actor, board):
        if actor is None:
            return True
        if not db.execute("SELECT 1 FROM sessions WHERE id=?", (actor,)).fetchone():
            return False
        members = self._members(db, board["id"])
        return actor in members and all(self.store.permitted(db, a, b) for a, b in combinations(members, 2))

    def _board(self, db, actor, identity):
        _identifier(identity, "board id")
        row = db.execute("SELECT * FROM activity_boards WHERE id=?", (identity,)).fetchone()
        if row is None or not self._allowed(db, actor, row):
            raise PolicyError("Activity board is unavailable for this session")
        board = dict(row)
        board["view_state"] = self._view_state(db, board)
        return board

    @staticmethod
    def _request(db, actor, request_id, payload, digest=None):
        _identifier(request_id, "request id")
        digest = digest or _hash(_json(payload).encode("utf-8"))
        row = db.execute("SELECT digest,result FROM activity_requests WHERE actor=? AND request_id=?",
                         (actor, request_id)).fetchone()
        if row:
            if row["digest"] != digest:
                raise PolicyError("Activity request ID was already used with different content")
            return digest, json.loads(row["result"])
        return digest, None

    @staticmethod
    def _record_request(db, actor, request_id, digest, result):
        db.execute("INSERT INTO activity_requests VALUES(?,?,?,?,?)",
                   (actor, request_id, digest, _json(result), _stamp()))

    def _owner_dir(self, actor):
        current = self.root
        for component in ("activities", _hash(actor.encode("utf-8"))):
            current = current / component
            if current.is_symlink():
                raise PolicyError("Activity directories must not be symlinks")
            current.mkdir(mode=0o700, exist_ok=True)
            if not current.is_dir():
                raise PolicyError("Activity storage must be a directory")
            if os.name != "nt" and stat.S_IMODE(current.stat().st_mode) & 0o077:
                raise PolicyError("Activity storage must be private to its owner")
        return current

    @staticmethod
    def _stage(path, content):
        descriptor, temporary = tempfile.mkstemp(prefix=".activity-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            return Path(temporary)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    @classmethod
    def _atomic(cls, path, content):
        if path.is_symlink():
            raise PolicyError("Activity output must not be a symlink")
        temporary = cls._stage(path, content)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _same_content(path, content):
        if not path.is_file() or path.stat().st_size != len(content):
            return False
        with path.open("rb") as stream:
            return stream.read(len(content) + 1) == content

    def _remember_projection(self, identity, revision, policy, status, path=None, *, expected=_ANY_CACHE):
        entry = {"revision": revision, "policy": policy, "projection": status, "path": str(path) if path else None}
        with self._cache_lock:
            previous = self._projection_cache.get(identity)
            if expected is not _ANY_CACHE and previous is not expected:
                return  # A different attempt has completed since this one began.
            if previous is None or previous["revision"] <= revision:
                self._projection_cache[identity] = entry

    def _cached_projection(self, identity):
        with self._cache_lock:
            return self._projection_cache.get(identity)

    def _projection(self, identity, actor=None):
        policy = self.policy()
        # Snapshot only immutable canonical input. Neither hashing, Qt inspection,
        # asset writes nor fsync holds the SQLite writer lock.
        with self.store.connect() as db:
            db.execute("BEGIN")
            board = self._board(db, actor, identity)
            row = db.execute("SELECT document,sha256 FROM activity_revisions WHERE board_id=? AND revision=?",
                             (identity, board["revision"])).fetchone()
            content = row["document"].encode("utf-8")
            expected_hash = row["sha256"]
        temporary = None
        cache_at_start = self._cached_projection(identity)
        try:
            if _hash(content) != expected_hash:
                raise PolicyError("Stored activity revision failed its integrity check")
            document = json.loads(content)
            directory = self._owner_dir(board["owner"])
            with self.store.connect() as db:
                self._assets(db, document, policy, directory)
            path = directory / (identity + ".json")
            if path.is_symlink():
                raise PolicyError("Activity projection must not be a symlink")
            if not self._same_content(path, content):
                temporary = self._stage(path, content)
            # The final short transaction orders the rename with publish/delete.
            # Obsolete staged work cannot overwrite a newer revision or resurrect a tombstone.
            with self.store.connect(True) as db:
                current = self._board(db, actor, identity)
                if current["revision"] != board["revision"]:
                    raise PolicyError("Activity changed while preparing its projection; read the current revision")
                if self._policy_value(self.store.setting(db, "activity_policy", {})) != policy:
                    raise PolicyError("Activity policy changed while preparing its projection; retry the read")
                if path.is_symlink():
                    raise PolicyError("Activity projection must not be a symlink")
                if temporary is not None:
                    os.replace(temporary, path)
                current["members"] = self._members(db, identity)
            self._remember_projection(identity, board["revision"], policy,
                                      {"state": "ready", "revision": board["revision"]}, path)
            return path, current, document
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            self._remember_projection(identity, board["revision"], policy,
                                      {"state": "pending", "revision": board["revision"], "error": str(exc)},
                                      expected=cache_at_start)
            raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def projection_status(self, identity):
        try:
            _, board, _ = self._projection(identity)
            projection = {"state": "ready", "revision": board["revision"]}
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            projection = {"state": "pending", "revision": None, "error": str(exc)}
        return projection

    def _accepted(self, result, request_id):
        # Canonical acceptance and request identity were committed together. A
        # projection problem never turns that durable result into an HTTP failure.
        return {**result, "request_id": request_id, "projection": self.projection_status(result["id"])}

    def _assets(self, db, document, policy, projection=None):
        identities = {}
        for card in document["cards"]:
            if card["kind"] != "image":
                continue
            relative = card["path"]
            owner = card["contributor"]
            if (owner, relative) in identities:
                continue
            row = db.execute("SELECT sha256,bytes,content FROM activity_assets WHERE owner=? AND path=?", (owner, relative)).fetchone()
            if row is None:
                raise PolicyError("Image must reference an asset uploaded by its contributor")
            content = bytes(row["content"])
            if len(content) != row["bytes"] or _hash(content) != row["sha256"]:
                raise PolicyError("Activity asset failed its stored integrity check")
            suffix = Path(relative).suffix
            if relative != f"assets/{row['sha256']}{suffix}" or suffix not in _RASTERS:
                raise PolicyError("Stored activity asset path is invalid")
            self._check_image(content, suffix, policy)
            identities[(owner, relative)] = (row["sha256"], row["bytes"])
            if projection is not None:
                directory = projection / "assets"
                if directory.is_symlink():
                    raise PolicyError("Asset projection must not be a symlink")
                directory.mkdir(mode=0o700, exist_ok=True)
                if os.name != "nt" and stat.S_IMODE(directory.stat().st_mode) & 0o077:
                    raise PolicyError("Asset projection must be private")
                path = projection / relative
                if path.is_symlink():
                    raise PolicyError("Asset projection must not be a symlink")
                if not self._same_content(path, content):
                    self._atomic(path, content)
        return identities

    @staticmethod
    def _check_asset_identities(db, identities):
        for (owner, path), identity in identities.items():
            row = db.execute("SELECT sha256,bytes FROM activity_assets WHERE owner=? AND path=?", (owner, path)).fetchone()
            if row is None or tuple(row) != identity:
                raise PolicyError("Activity assets changed during validation; retry the request")

    @staticmethod
    def _check_image(content, suffix, policy):
        if len(content) > policy.max_image_bytes:
            raise PolicyError("Activity image exceeds the byte limit")
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        from PySide6.QtGui import QImageReader
        buffer = QBuffer()
        buffer.setData(QByteArray(content))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)
        size = reader.size()
        if (not reader.canRead() or bytes(reader.format()).lower() not in _RASTERS.get(suffix, set())
                or size.width() <= 0 or size.height() <= 0):
            raise PolicyError("Activity asset is not a supported raster image")
        if size.width() * size.height() > policy.max_image_pixels:
            raise PolicyError("Activity image exceeds the pixel limit")

    def asset(self, actor, name, data_base64):
        if self._actor(actor) is None:
            raise PolicyError("Asset upload requires a session owner")
        policy = self.policy()
        if (not isinstance(name, str) or not name or len(name) > 160
                or Path(name).name != name or any(c in name for c in ("/", "\\", ":", "\x00"))):
            raise PolicyError("Asset name must be a local raster filename")
        suffix = Path(name).suffix.lower()
        if suffix not in _RASTERS:
            raise PolicyError("Asset format is not supported")
        if (not isinstance(data_base64, str)
                or len(data_base64) > 4 * ((policy.max_image_bytes + 2) // 3)):
            raise PolicyError("Activity image exceeds the encoded byte limit")
        try:
            content = base64.b64decode(data_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise PolicyError("Asset data must be valid base64") from exc
        self._check_image(content, suffix, policy)
        digest = _hash(content)
        suffix = ".jpg" if suffix == ".jpeg" else suffix
        relative = f"assets/{digest}{suffix}"
        directory = self._owner_dir(actor) / "assets"
        if directory.is_symlink():
            raise PolicyError("Asset storage must not be a symlink")
        directory.mkdir(mode=0o700, exist_ok=True)
        if os.name != "nt" and stat.S_IMODE(directory.stat().st_mode) & 0o077:
            raise PolicyError("Asset storage must be private to its owner")
        path = directory / (digest + suffix)
        if path.is_symlink():
            raise PolicyError("Asset file must not be a symlink")
        if not self._same_content(path, content):
            self._atomic(path, content)
        with self.store.connect(True) as db:
            if not db.execute("SELECT 1 FROM sessions WHERE id=?", (actor,)).fetchone():
                raise PolicyError("Unknown session")
            if self._policy_value(self.store.setting(db, "activity_policy", {})) != policy:
                raise PolicyError("Activity policy changed during asset validation; retry the upload")
            db.execute("INSERT OR IGNORE INTO activity_assets VALUES(?,?,?,?,?,?)",
                       (actor, relative, digest, len(content), _stamp(), content))
        return {"path": relative, "sha256": digest, "bytes": len(content)}

    def publish(self, actor, document, request_id, expected_revision=0):
        session = self._actor(actor)
        if session is None:
            raise PolicyError("Publish requires an authenticated session owner")
        if type(expected_revision) is not int or expected_revision < 0:
            raise PolicyError("Expected revision must be a nonnegative integer")
        raw = _json(document).encode("utf-8")
        candidate = json.loads(raw)
        if not isinstance(candidate, dict):
            raise PolicyError("Activity document must be an object")
        identity = _identifier(candidate.get("id"), "board id")
        payload = {"action": "publish", "document": document, "expected_revision": expected_revision}
        digest = _hash(_json(payload).encode("utf-8"))
        with self.store.connect() as db:
            db.execute("BEGIN")
            existing = self._publish_target(db, actor, identity)
            _, previous = self._request(db, actor, request_id, payload, digest)
            old = json.loads(db.execute("SELECT document FROM activity_revisions WHERE board_id=? AND revision=?",
                                       (identity, existing["revision"])).fetchone()[0]) if existing and previous is None else None
        if previous is not None:
            return self._accepted(previous, request_id)
        policy = self.policy()
        if len(raw) > policy.max_document_bytes:
            raise PolicyError("Activity document exceeds the byte limit")
        candidate.pop("contributors", None)
        candidate.pop("last_editor", None)
        for card in candidate.get("cards", []):
            if isinstance(card, dict):
                card.pop("contributor", None)
        candidate["session_link"] = {"id": actor, "label": session["name"]}
        _validate(candidate, policy)
        revision = existing["revision"] if existing else 0
        if expected_revision != revision or candidate["revision"] != revision + 1:
            raise PolicyError("Activity revision conflict; read the current document before updating")
        previous_cards = {card["id"]: card for card in old["cards"]} if old else {}
        incoming = {card["id"]: card for card in candidate["cards"]}
        for key, card in previous_cards.items():
            if card["contributor"] != actor:
                comparable = {k: v for k, v in card.items() if k != "contributor"}
                if incoming.get(key) != comparable:
                    raise PolicyError("Only a card's contributor may change or remove it")
        if old:
            candidate["session_link"] = old["session_link"]
        for card in candidate["cards"]:
            if card["id"] in previous_cards and card["kind"] != previous_cards[card["id"]]["kind"]:
                raise PolicyError("A card ID cannot change kind; use a new ID")
            card["contributor"] = previous_cards.get(card["id"], {}).get("contributor", actor)
        candidate["contributors"] = {card["id"]: card["contributor"] for card in candidate["cards"]}
        candidate["last_editor"] = actor
        _validate(candidate, policy)
        encoded = _json(candidate)
        if len(encoded.encode("utf-8")) > policy.max_document_bytes:
            raise PolicyError("Activity document exceeds the byte limit")
        content_hash = _hash(encoded.encode("utf-8"))
        with self.store.connect() as db:
            identities = self._assets(db, candidate, policy)
        with self.store.connect(True) as db:
            current = self._publish_target(db, actor, identity)
            _, previous = self._request(db, actor, request_id, payload, digest)
            if previous is not None:
                result = previous
            else:
                if (current["revision"] if current else 0) != expected_revision:
                    raise PolicyError("Activity revision conflict; read the current document before updating")
                if self._policy_value(self.store.setting(db, "activity_policy", {})) != policy:
                    raise PolicyError("Activity policy changed during validation; retry the publish")
                self._check_asset_identities(db, identities)
                created = _stamp()
                if current:
                    db.execute("UPDATE activity_boards SET title=?,revision=?,updated=? WHERE id=?",
                               (candidate["title"], candidate["revision"], created, identity))
                else:
                    db.execute("INSERT INTO activity_boards VALUES(?,?,?,?,?,?,?)",
                               (identity, actor, candidate["title"], 1, created, "open", 1))
                    db.execute("INSERT INTO activity_members VALUES(?,?)", (identity, actor))
                    db.execute("INSERT INTO activity_lifecycle(board_id,presentation) VALUES(?,'requested')", (identity,))
                db.execute("INSERT INTO activity_revisions VALUES(?,?,?,?,?)",
                           (identity, candidate["revision"], encoded, content_hash, created))
                result = {"id": identity, "revision": candidate["revision"], "owner": current["owner"] if current else actor,
                          "accepted": True, "request_id": request_id}
                self._record_request(db, actor, request_id, digest, result)
        return self._accepted(result, request_id)

    def _publish_target(self, db, actor, identity):
        if not db.execute("SELECT 1 FROM sessions WHERE id=?", (actor,)).fetchone():
            raise PolicyError("Unknown session")
        if db.execute("SELECT 1 FROM activity_deleted WHERE id=?", (identity,)).fetchone():
            raise PolicyError("Activity board was deleted; use a new board ID")
        existing = db.execute("SELECT * FROM activity_boards WHERE id=?", (identity,)).fetchone()
        if existing and not self._allowed(db, actor, existing):
            raise PolicyError("Activity board is unavailable for this session")
        return existing

    def list(self, actor=None):
        self._actor(actor)
        policy = self.policy()
        with self.store.connect() as db:
            db.execute("BEGIN")
            result = [dict(row) for row in db.execute("SELECT * FROM activity_boards ORDER BY updated,id") if self._allowed(db, actor, row)]
            for board in result:
                board["members"] = self._members(db, board["id"])
                board["view_state"] = self._view_state(db, board)
        if actor is None:
            identities = {board["id"] for board in result}
            with self._cache_lock:
                self._projection_cache = {key: value for key, value in self._projection_cache.items() if key in identities}
            for board in result:
                cached = self._cached_projection(board["id"])
                if cached is None or cached["revision"] != board["revision"] or cached["policy"] != policy:
                    try:
                        _, current, _ = self._projection(board["id"])
                        board.update(current)
                    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
                        board["projection_error"] = str(exc)
                        self._remember_projection(board["id"], board["revision"], policy,
                                                  {"state": "pending", "revision": board["revision"], "error": str(exc)}, expected=cached)
                    cached = self._cached_projection(board["id"])
                if cached and cached["revision"] == board["revision"] and cached["policy"] == policy:
                    board["projection"] = cached["projection"]
                    if cached["path"]:
                        board.pop("projection_error", None)
                        board["path"] = cached["path"]
                    else:
                        board["projection_error"] = cached["projection"].get("error", "Projection pending")
        return result

    def get(self, actor, id):
        self._actor(actor)
        path, result, document = self._projection(id, actor)
        result["document"] = document
        result["projection"] = {"state": "ready", "revision": result["revision"]}
        if actor is None:
            result["path"] = str(path)
        return result

    def control(self, actor, id, action, request_id):
        self._actor(actor)
        if action not in ("open", "focus", "clear", "close"):
            raise PolicyError("Unsupported activity view action")
        requester = actor if actor is not None else "operator"
        with self.store.connect(True) as db:
            board = self._board(db, actor, id)
            digest, previous = self._request(db, requester, request_id, {"action": "control", "id": id, "view_action": action})
            if previous is not None:
                return previous
            state = board["view_state"]
            quiet = actor is not None and state["operator_dismissed"] and action in ("open", "focus")
            if quiet:
                db.execute("UPDATE activity_lifecycle SET reopen_requested=1,requested_by=?,requested_revision=? WHERE board_id=?",
                           (actor, board["revision"], id))
            else:
                board["view_action"] = action
                board["view_seq"] += 1
                db.execute("UPDATE activity_boards SET view_action=?,view_seq=?,updated=? WHERE id=?",
                           (action, board["view_seq"], _stamp(), id))
                if actor is None and action in ("open", "focus"):
                    db.execute("""UPDATE activity_lifecycle SET operator_dismissed=0,reopen_requested=0,
                                  requested_by=NULL,requested_revision=0,presentation='requested',last_operator_open_seq=?
                                  WHERE board_id=?""", (board["view_seq"], id))
                elif actor is None and action == "close":
                    db.execute("UPDATE activity_lifecycle SET operator_dismissed=1,presentation='requested' WHERE board_id=?", (id,))
                elif not state["operator_dismissed"]:
                    db.execute("UPDATE activity_lifecycle SET presentation='requested' WHERE board_id=?", (id,))
            result = {"id": id, "owner": board["owner"], "view_action": board["view_action"], "view_seq": board["view_seq"],
                      "view_state": self._view_state(db, board)}
            self._record_request(db, requester, request_id, digest, result)
        return result

    def view(self, id, state, revision, view_seq, request_id):
        """Record operator GUI presentation, never a claim that a human read it."""
        if state not in ("visible", "hidden", "closed"):
            raise PolicyError("Unsupported activity presentation state")
        if type(revision) is not int or revision < 1 or type(view_seq) is not int or view_seq < 1:
            raise PolicyError("Presentation requires positive revision and view sequence")
        with self.store.connect(True) as db:
            board = self._board(db, None, id)
            digest, previous = self._request(db, "operator", request_id,
                                             {"action": "view", "id": id, "state": state, "revision": revision, "view_seq": view_seq})
            if previous is not None:
                return previous
            if state == "closed":
                # A disconnected GUI close is still operator intent after agent
                # controls. Only a newer explicit operator reopen supersedes it.
                accepted = board["view_state"]["last_operator_open_seq"] <= view_seq <= board["view_seq"]
            else:
                accepted = view_seq == board["view_seq"] and not board["view_state"]["operator_dismissed"]
            if accepted and revision > board["revision"]:
                raise PolicyError("Presentation revision is newer than the current document")
            if accepted:
                if state == "closed":
                    board["view_seq"] += 1
                    db.execute("UPDATE activity_boards SET view_action='close',view_seq=?,updated=? WHERE id=?", (board["view_seq"], _stamp(), id))
                    db.execute("UPDATE activity_lifecycle SET operator_dismissed=1,presentation='closed',observed_at=? WHERE board_id=?", (_stamp(), id))
                elif state == "visible":
                    db.execute("""UPDATE activity_lifecycle SET presentation='visible',
                                  presented_revision=MAX(presented_revision,?),observed_at=? WHERE board_id=?""", (revision, _stamp(), id))
                else:
                    db.execute("UPDATE activity_lifecycle SET presentation='hidden',observed_at=? WHERE board_id=?", (_stamp(), id))
            result = {"id": id, "view_seq": board["view_seq"], "accepted": accepted, "view_state": self._view_state(db, board)}
            self._record_request(db, "operator", request_id, digest, result)
        return result

    def reply(self, id, card_id, revision, answer, request_id):
        policy = self.policy()
        _text(answer, "answer", policy, nonempty=True, limit=policy.max_answer_chars)
        if type(revision) is not int or revision <= 0:
            raise PolicyError("Reply revision must be a positive integer")
        with self.store.connect(True) as db:
            board = self._board(db, None, id)
            payload = {"action": "reply", "id": id, "card_id": card_id, "revision": revision, "answer": answer}
            digest, previous = self._request(db, "operator", request_id, payload)
            if previous is not None:
                return previous
            if revision != board["revision"]:
                raise PolicyError("Activity changed; review the current question before answering")
            row = db.execute("SELECT document FROM activity_revisions WHERE board_id=? AND revision=?", (id, revision)).fetchone()
            document = json.loads(row["document"])
            card = next((card for card in document["cards"] if card["id"] == card_id), None)
            if card is None or card["kind"] != "question":
                raise PolicyError("Reply must target a current question card")
            result = {"id": str(uuid.uuid4()), "board_id": id, "card_id": card_id, "revision": revision,
                      "answer": answer, "prompt_sha256": _hash(card["prompt"].encode("utf-8")),
                      "created": _stamp(), "state": "pending", "recipient": card["contributor"]}
            db.execute("INSERT INTO activity_replies VALUES(?,?,?,?,?,?,?,?,?)",
                       tuple(result[key] for key in ("id", "board_id", "card_id", "revision", "answer", "prompt_sha256", "created", "state", "recipient")))
            self._record_request(db, "operator", request_id, digest, result)
        return result

    def replies(self, actor, board_id=None):
        self._actor(actor)
        with self.store.connect(True) as db:
            if board_id is not None:
                self._board(db, actor, board_id)
            query = "SELECT r.* FROM activity_replies r JOIN activity_boards b ON b.id=r.board_id"
            conditions, values = [], []
            if actor is not None:
                conditions.append("r.recipient=?")
                values.append(actor)
            if board_id is not None:
                conditions.append("r.board_id=?")
                values.append(board_id)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY r.created,r.id"
            return [dict(row) for row in db.execute(query, values)
                    if self._allowed(db, actor, {"id": row["board_id"]})]

    def acknowledge(self, actor, event_id):
        if self._actor(actor) is None:
            raise PolicyError("Reply acknowledgment requires its session owner")
        with self.store.connect(True) as db:
            row = db.execute("SELECT r.* FROM activity_replies r JOIN activity_boards b ON b.id=r.board_id WHERE r.id=? AND r.recipient=?",
                             (event_id, actor)).fetchone()
            if row is None:
                raise PolicyError("Activity reply is unavailable for this session")
            self._board(db, actor, row["board_id"])
            result = dict(row)
            if result["state"] == "pending":
                db.execute("UPDATE activity_replies SET state='read' WHERE id=? AND state='pending'", (event_id,))
                result["state"] = "read"
            return result

    def share(self, actor, id, members, request_id):
        self._actor(actor)
        if not isinstance(members, list) or any(not isinstance(m, str) or not m for m in members):
            raise PolicyError("Members must be exact session IDs")
        requester = actor if actor is not None else "operator"
        with self.store.connect(True) as db:
            board = self._board(db, None, id)
            if actor is not None and actor != board["owner"]:
                raise PolicyError("Only the board owner can change sharing")
            members = sorted(set(members) | {board["owner"]})
            digest, previous = self._request(db, requester, request_id, {"action": "share", "id": id, "members": members})
            if previous is not None:
                return previous
            for member in members:
                if not db.execute("SELECT 1 FROM sessions WHERE id=?", (member,)).fetchone():
                    raise PolicyError("Sharing requires existing sessions")
            if not all(self.store.permitted(db, a, b) for a, b in combinations(members, 2)):
                raise PolicyError("Sharing requires enabled participation and an approved direct connection between every member")
            db.execute("DELETE FROM activity_members WHERE board_id=?", (id,))
            db.executemany("INSERT INTO activity_members VALUES(?,?)", [(id, m) for m in members])
            result = {"id": id, "owner": board["owner"], "members": members}
            self._record_request(db, requester, request_id, digest, result)
            return result

    def edit(self, id, card_id, revision, content, request_id):
        if type(revision) is not int or revision < 1:
            raise PolicyError("Edit revision must be a positive integer")
        payload = {"action": "edit", "id": id, "card_id": card_id, "revision": revision, "content": content}
        digest = _hash(_json(payload).encode("utf-8"))
        with self.store.connect() as db:
            db.execute("BEGIN")
            board = self._board(db, None, id)
            _, previous = self._request(db, "operator", request_id, payload, digest)
            document = json.loads(db.execute("SELECT document FROM activity_revisions WHERE board_id=? AND revision=?",
                                            (id, board["revision"])).fetchone()[0]) if previous is None else None
        if previous is not None:
            return self._accepted(previous, request_id)
        if board["revision"] != revision:
            raise PolicyError("Activity changed; review the current card before editing")
        policy = self.policy()
        card = next((card for card in document["cards"] if card["id"] == card_id), None)
        if card is None or card["kind"] not in ("text", "sketch"):
            raise PolicyError("Operator editing supports text and sketch cards")
        card["html" if card["kind"] == "text" else "strokes"] = content
        document["revision"] += 1
        document["last_editor"] = "operator"
        _validate(document, policy)
        encoded = _json(document)
        if len(encoded.encode("utf-8")) > policy.max_document_bytes:
            raise PolicyError("Activity document exceeds the byte limit")
        content_hash = _hash(encoded.encode("utf-8"))
        with self.store.connect(True) as db:
            board = self._board(db, None, id)
            _, previous = self._request(db, "operator", request_id, payload, digest)
            if previous is not None:
                result = previous
            else:
                if board["revision"] != revision:
                    raise PolicyError("Activity changed; review the current card before editing")
                if self._policy_value(self.store.setting(db, "activity_policy", {})) != policy:
                    raise PolicyError("Activity policy changed during validation; retry the edit")
                created = _stamp()
                db.execute("UPDATE activity_boards SET revision=?,updated=? WHERE id=?", (document["revision"], created, id))
                db.execute("INSERT INTO activity_revisions VALUES(?,?,?,?,?)", (id, document["revision"], encoded, content_hash, created))
                result = {"id": id, "revision": document["revision"], "owner": board["owner"], "accepted": True, "request_id": request_id}
                self._record_request(db, "operator", request_id, digest, result)
        return self._accepted(result, request_id)

    def _delete_projection(self, identity, owner):
        directory = self.root
        for component in ("activities", _hash(owner.encode("utf-8"))):
            directory = directory / component
            if directory.is_symlink():
                raise PolicyError("Activity projection directory must not be a symlink")
            if not directory.exists():
                return
            if not directory.is_dir():
                raise PolicyError("Activity projection directory is invalid")
        path = directory / (identity + ".json")
        if path.is_symlink():
            raise PolicyError("Activity projection must not be a symlink")
        if path.exists() and not path.is_file():
            raise PolicyError("Activity projection must be a regular file")
        path.unlink(missing_ok=True)

    def delete(self, id, expected_revision, request_id, *, confirmed=False):
        if confirmed is not True:
            raise PolicyError("Confirm deleting this saved Prism board")
        _identifier(id, "board id")
        if type(expected_revision) is not int or expected_revision < 1:
            raise PolicyError("Expected revision must be a positive integer")
        payload = {"action": "delete", "id": id, "expected_revision": expected_revision}
        with self.store.connect(True) as db:
            digest, previous = self._request(db, "operator", request_id, payload)
            if previous is not None:
                return previous
            board = self._board(db, None, id)
            if board["revision"] != expected_revision:
                raise PolicyError(f"Activity delete revision conflict; current revision is {board['revision']}; review and confirm again")
            db.execute("INSERT INTO activity_deleted VALUES(?,?,?,?)", (id, board["owner"], board["revision"], _stamp()))
            for table in ("activity_replies", "activity_revisions", "activity_members", "activity_lifecycle"):
                db.execute(f"DELETE FROM {table} WHERE board_id=?", (id,))
            db.execute("DELETE FROM activity_boards WHERE id=?", (id,))
            # Retain request identities/digests, but remove reply bodies and other
            # deleted-board content retained in cached accepted responses.
            for row in db.execute("SELECT actor,request_id,result FROM activity_requests").fetchall():
                result = json.loads(row["result"])
                if result.get("board_id", result.get("id")) == id:
                    tombstone = {"id": result.get("id"), "board_id": id, "deleted": True}
                    db.execute("UPDATE activity_requests SET result=? WHERE actor=? AND request_id=?",
                               (_json(tombstone), row["actor"], row["request_id"]))
            result = {"id": id, "revision": board["revision"], "deleted": True,
                      "retained_assets": True, "retained_backups": True, "projection_cleanup": "complete"}
            # This is one small cache unlink under the same writer transaction:
            # a concurrent projection cannot recreate it after canonical deletion.
            try:
                self._delete_projection(id, board["owner"])
            except (OSError, ValueError) as exc:
                result["projection_cleanup"] = "pending"
                result["projection_error"] = str(exc)
            self._record_request(db, "operator", request_id, digest, result)
        return result
