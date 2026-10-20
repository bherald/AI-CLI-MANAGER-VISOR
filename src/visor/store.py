from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class PolicyError(ValueError):
    pass


class Store:
    """One connection per operation. Write transactions serialize policy and delivery claims."""

    def __init__(self, root: Path):
        self.path = root / "visor.sqlite3"
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise PolicyError("This database requires a newer VISOR version")
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(
                    id TEXT PRIMARY KEY, provider TEXT NOT NULL, native_id TEXT NOT NULL,
                    name TEXT NOT NULL, cwd TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unknown',
                    latest TEXT NOT NULL DEFAULT '', updated REAL NOT NULL,
                    managed INTEGER NOT NULL DEFAULT 0, participates INTEGER NOT NULL DEFAULT 0,
                    window_token TEXT, heartbeat REAL, process_id INTEGER,
                    shutdown TEXT NOT NULL DEFAULT '', UNIQUE(provider,native_id));
                CREATE TABLE IF NOT EXISTS pairs(a TEXT NOT NULL REFERENCES sessions(id),
                    b TEXT NOT NULL REFERENCES sessions(id), PRIMARY KEY(a,b), CHECK(a<b));
                CREATE TABLE IF NOT EXISTS credentials(hash TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id));
                CREATE TABLE IF NOT EXISTS deleted_sessions(
                    session_id TEXT PRIMARY KEY, deleted REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(
                    id TEXT PRIMARY KEY, sender TEXT NOT NULL, recipient TEXT NOT NULL REFERENCES sessions(id),
                    body TEXT NOT NULL, origin TEXT NOT NULL, state TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, detail TEXT NOT NULL DEFAULT '',
                    dedupe TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'message',
                    UNIQUE(sender,dedupe));
            """)
            columns = {r[1] for r in db.execute("PRAGMA table_info(credentials)")}
            if "role" not in columns:
                db.execute("ALTER TABLE credentials ADD COLUMN role TEXT NOT NULL DEFAULT 'agent'")
            columns = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
            for column in ("helper_identity", "window_identity"):
                if column not in columns:
                    db.execute(f"ALTER TABLE sessions ADD COLUMN {column} TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS messages_pending ON messages(state,recipient,created)")
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def setting(db, key, default=None):
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def get_setting(self, key, default=None):
        with self.connect() as db:
            return self.setting(db, key, default)

    def set_setting(self, key, value):
        with self.connect(True) as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value)))

    def save_receipt_scan(self, session, value):
        with self.connect(True) as db:
            if not db.execute("SELECT 1 FROM sessions WHERE id=?", (session,)).fetchone():
                return  # A concurrent deletion must not leave an orphan cursor/history record.
            key = "receipt_scan:" + session
            if value:
                db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value)))
            else:
                db.execute("DELETE FROM settings WHERE key=?", (key,))

    def external_focus_links(self):
        with self.connect() as db:
            return {row["key"].removeprefix("external_focus:"): json.loads(row["value"])
                    for row in db.execute("SELECT key,value FROM settings WHERE key LIKE 'external_focus:%'")}

    def set_external_focus(self, key, value):
        with self.connect(True) as db:
            session = db.execute("SELECT * FROM sessions WHERE id=?", (key,)).fetchone()
            if not session:
                raise PolicyError("Unknown session")
            if value:
                if session["window_token"] or (session["heartbeat"] and time.time() - session["heartbeat"] < 20):
                    raise PolicyError("This session already has a managed terminal association")
                for row in db.execute("SELECT key,value FROM settings WHERE key LIKE 'external_focus:%'"):
                    if row["key"] != "external_focus:" + key and json.loads(row["value"])["identity"] == value["identity"]:
                        raise PolicyError("This window is already linked to another session; unlink it there first")
                db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", ("external_focus:" + key, json.dumps(value)))
            else:
                db.execute("DELETE FROM settings WHERE key=?", ("external_focus:" + key,))

    def upsert_session(self, provider, native_id, name, cwd, **fields):
        if provider not in ("codex", "claude") or not native_id or len(native_id) > 128:
            raise PolicyError("Invalid provider or session identity")
        key = f"{provider}:{native_id}"
        with self.connect(True) as db:
            if db.execute("SELECT 1 FROM deleted_sessions WHERE session_id=?", (key,)).fetchone():
                return key  # A stale discovery cannot resurrect a deleted conversation.
            db.execute("""INSERT INTO sessions(id,provider,native_id,name,cwd,updated)
                VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated""",
                       (key, provider, native_id, name, cwd, time.time()))
            self._update(db, key, fields)
        return key

    @staticmethod
    def _update(db, key, fields):
        allowed = {"name", "cwd", "status", "latest", "updated", "managed", "participates",
                   "window_token", "heartbeat", "process_id", "shutdown", "helper_identity", "window_identity"}
        if fields.keys() - allowed:
            raise PolicyError("Invalid session fields")
        for field, value in fields.items():
            db.execute(f"UPDATE sessions SET {field}=? WHERE id=?", (value, key))

    def update_session(self, key, **fields):
        with self.connect(True) as db:
            self._update(db, key, fields)

    def sessions(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM sessions ORDER BY updated DESC")]

    def delete_session(self, key):
        """Only after provider deletion succeeds; retain an identity-only rediscovery guard."""
        with self.connect(True) as db:
            db.execute("INSERT OR REPLACE INTO deleted_sessions VALUES(?,?)", (key, time.time()))
            db.execute("DELETE FROM pairs WHERE a=? OR b=?", (key, key))
            db.execute("DELETE FROM credentials WHERE session_id=?", (key,))
            db.execute("DELETE FROM messages WHERE sender=? OR recipient=?", (key, key))
            db.execute("DELETE FROM settings WHERE key=?", ("claude_runtime:" + key,))
            db.execute("DELETE FROM settings WHERE key=?", ("owned_work:" + key,))
            db.execute("DELETE FROM settings WHERE key=?", ("receipt_scan:" + key,))
            db.execute("DELETE FROM settings WHERE key=?", ("external_focus:" + key,))
            db.execute("DELETE FROM settings WHERE key IN (?,?)",
                       ("wrap_up_timeout:" + key, "wrap_up_request:" + key))
            targets = [target for target in self.setting(db, "shutdown_targets", []) if target != key]
            db.execute("INSERT OR REPLACE INTO settings VALUES('shutdown_targets',?)", (json.dumps(targets),))
            layout = [item for item in self.setting(db, "layout", []) if item.get("session") != key]
            db.execute("INSERT OR REPLACE INTO settings VALUES('layout',?)", (json.dumps(layout),))
            db.execute("DELETE FROM sessions WHERE id=?", (key,))
        return True

    def session(self, key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (key,)).fetchone()
        if not row:
            raise PolicyError("Unknown session")
        return dict(row)

    def issue_credential(self, key, role="agent"):
        if role not in ("agent", "helper"):
            raise PolicyError("Invalid credential role")
        self.session(key)
        token = secrets.token_urlsafe(32)
        with self.connect(True) as db:
            db.execute("DELETE FROM credentials WHERE session_id=? AND role=?", (key, role))
            db.execute("INSERT INTO credentials(hash,session_id,role) VALUES(?,?,?)", (self.digest(token), key, role))
        return token

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def authenticate(self, token):
        principal = self.authenticate_principal(token)
        return principal[0] if principal else None

    def authenticate_principal(self, token):
        with self.connect() as db:
            row = db.execute("SELECT session_id,role FROM credentials WHERE hash=?",
                             (self.digest(token),)).fetchone()
        return tuple(row) if row else None

    def pair(self, a, b, enabled=True):
        if a == b:
            raise PolicyError("Choose two different sessions")
        a, b = sorted((a, b))
        with self.connect(True) as db:
            if enabled:
                db.execute("INSERT OR IGNORE INTO pairs VALUES(?,?)", (a, b))
            else:
                db.execute("DELETE FROM pairs WHERE a=? AND b=?", (a, b))
                db.execute("""UPDATE messages SET state='held',updated=? WHERE origin='agent'
                    AND state='queued' AND ((sender=? AND recipient=?) OR (sender=? AND recipient=?))""",
                           (time.time(), a, b, b, a))

    def pairs(self):
        with self.connect() as db:
            return [list(r) for r in db.execute("SELECT a,b FROM pairs ORDER BY a,b")]

    def toggle(self, enabled):
        with self.connect(True) as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES('agent_enabled',?)", (json.dumps(bool(enabled)),))
            if not enabled:
                db.execute("UPDATE messages SET state='held',updated=? WHERE origin='agent' AND state='queued'", (time.time(),))

    def participate(self, key, enabled):
        with self.connect(True) as db:
            self._update(db, key, {"participates": bool(enabled)})
            if not enabled:
                db.execute("""UPDATE messages SET state='held',updated=? WHERE origin='agent'
                    AND state='queued' AND (sender=? OR recipient=?)""", (time.time(), key, key))

    def permitted(self, db, sender, recipient):
        a, b = sorted((sender, recipient))
        return (self.setting(db, "agent_enabled", False)
                and db.execute("SELECT 1 FROM pairs WHERE a=? AND b=?", (a, b)).fetchone()
                and db.execute("SELECT COUNT(*) FROM sessions WHERE id IN (?,?) AND participates=1",
                               (a, b)).fetchone()[0] == 2)

    def enqueue(self, sender, recipient, body, dedupe, *, operator=False, kind="message"):
        body = body.strip()
        if not body or len(body) > 16000 or not dedupe or len(dedupe) > 128:
            raise PolicyError("Message must contain 1–16000 characters and a request ID")
        if kind not in ("message", "wrap_up") or (kind == "wrap_up" and not operator):
            raise PolicyError("Wrap-up is an operator action")
        origin = "operator" if operator else "agent"
        sender = "operator" if operator else sender
        now = time.time()
        with self.connect(True) as db:
            if db.execute("SELECT 1 FROM deleted_sessions WHERE session_id IN (?,?)", (sender, recipient)).fetchone():
                raise PolicyError("Session was permanently deleted")
            old = db.execute("SELECT * FROM messages WHERE sender=? AND dedupe=?", (sender, dedupe)).fetchone()
            if old:
                if old["state"] == "deleted":
                    raise PolicyError("Message was deleted; use a new request ID")
                if (old["recipient"], old["body"], old["kind"]) != (recipient, body, kind):
                    raise PolicyError("Request ID already used for a different message")
                return dict(old)
            if not operator:
                a, b = sorted((sender, recipient))
                if not db.execute("SELECT 1 FROM pairs WHERE a=? AND b=?", (a, b)).fetchone():
                    raise PolicyError("No approved direct connection")
                count = db.execute("SELECT COUNT(*) FROM messages WHERE sender=? AND created>?", (sender, now - 60)).fetchone()[0]
                if count >= 12:
                    raise PolicyError("Agent rate limit: 12 messages per minute")
            pending = db.execute("SELECT COUNT(*) FROM messages WHERE recipient=? AND state IN ('queued','held','sending')",
                                 (recipient,)).fetchone()[0]
            if pending >= 500:
                raise PolicyError("Recipient inbox is full (500 pending messages); review queued/held messages first")
            queued = operator or self.permitted(db, sender, recipient)
            detail = ""
            if queued and not operator:
                repeated = db.execute("""SELECT COUNT(*) FROM messages WHERE origin='agent'
                    AND sender=? AND recipient=? AND body=? AND created>?""",
                                      (sender, recipient, body, now - 300)).fetchone()[0]
                pair_count = db.execute("""SELECT COUNT(*) FROM messages WHERE origin='agent'
                    AND created>? AND ((sender=? AND recipient=?) OR (sender=? AND recipient=?))""",
                                        (now - 300, sender, recipient, recipient, sender)).fetchone()[0]
                if repeated >= 3 or pair_count >= 24:
                    queued = False
                    detail = "Automatic loop hold: repeated content or pair traffic limit (24 per 5 minutes). Review and release manually."
            mid = str(uuid.uuid4())
            db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (mid, sender, recipient, body, origin, "queued" if queued else "held", now, now, detail, dedupe, kind))
            if kind == "wrap_up":
                self._update(db, recipient, {"shutdown": "requested"})
            return dict(db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone())

    def messages(self, limit=200, *, visible=False):
        with self.connect() as db:
            return [dict(r) for r in db.execute("""SELECT * FROM messages
                WHERE NOT ? OR (state != 'deleted' AND NOT
                    (state IN ('completed','failed') AND body='[cleared]'))
                ORDER BY created DESC LIMIT ?""", (visible, limit))]

    def message(self, mid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        return dict(row) if row else None

    def receipt_messages(self, recipient):
        """Background reconciliation must not use the bounded UI history window."""
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM messages WHERE recipient=?
                AND state IN ('delivered','delivery_unknown','acknowledged') ORDER BY created""",
                                                    (recipient,))]

    def claim(self, recipient=None):
        """Claim under current policy; an in-flight claim cannot be retracted by a toggle."""
        with self.connect(True) as db:
            rows = db.execute("SELECT * FROM messages WHERE state='queued' AND (? IS NULL OR recipient=?) ORDER BY created",
                              (recipient, recipient)).fetchall()
            for row in rows:
                if recipient and row["recipient"] != recipient:
                    continue
                if row["origin"] == "agent" and not self.permitted(db, row["sender"], row["recipient"]):
                    db.execute("UPDATE messages SET state='held' WHERE id=?", (row["id"],))
                    continue
                db.execute("UPDATE messages SET state='sending',updated=? WHERE id=?", (time.time(), row["id"]))
                return dict(row)
        return None

    def delivery_result(self, mid, state, detail=""):
        if state not in ("delivered", "delivery_unknown", "failed", "held"):
            raise PolicyError("Invalid delivery result")
        with self.connect(True) as db:
            db.execute("UPDATE messages SET state=?,detail=?,updated=? WHERE id=? AND state='sending'",
                       (state, detail, time.time(), mid))

    def acknowledge(self, recipient, mid, completed=False):
        with self.connect(True) as db:
            row = db.execute("SELECT * FROM messages WHERE id=? AND recipient=?", (mid, recipient)).fetchone()
            if not row or row["state"] not in ("sending", "delivered", "delivery_unknown", "acknowledged", "completed"):
                raise PolicyError("Message is not available for this acknowledgment")
            state = "completed" if completed or row["state"] == "completed" else "acknowledged"
            detail = "Agent explicitly confirmed completion; background work remains unverified" if state == "completed" else "Agent explicitly acknowledged reading"
            db.execute("UPDATE messages SET state=?,detail=?,updated=? WHERE id=?", (state, detail, time.time(), mid))
            if completed and row["kind"] == "wrap_up":
                self._update(db, recipient, {"shutdown": "confirmed; background work unverified"})

    def transport_received(self, recipient, mid):
        with self.connect(True) as db:
            row = db.execute("SELECT * FROM messages WHERE id=? AND recipient=?", (mid, recipient)).fetchone()
            if not row:
                raise PolicyError("Message does not belong to this recipient")
            db.execute("""UPDATE messages SET state='delivered',updated=?,
                detail='Recipient MCP transport wrote the response; agent acknowledgment pending'
                WHERE id=? AND state IN ('sending','delivery_unknown')""", (time.time(), mid))

    def expire_sending(self, age=120):
        with self.connect(True) as db:
            db.execute("""UPDATE messages SET state='delivery_unknown',updated=?,
                detail='Delivery attempt ended without transport confirmation'
                WHERE state='sending' AND updated<?""", (time.time(), time.time() - age))

    def release(self, mid):
        with self.connect(True) as db:
            row = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
            if not row or row["state"] != "held":
                raise PolicyError("Only held messages can be released; uncertain delivery cannot be retried")
            if row["origin"] == "agent" and not self.permitted(db, row["sender"], row["recipient"]):
                raise PolicyError("Connection policy still blocks delivery")
            db.execute("UPDATE messages SET state='queued',updated=? WHERE id=?", (time.time(), mid))

    def recover(self):
        with self.connect(True) as db:
            db.execute("""UPDATE messages SET state='delivery_unknown',detail='Broker stopped during delivery',
                updated=? WHERE state='sending'""", (time.time(),))
            db.execute("UPDATE sessions SET status='unknown',heartbeat=NULL,process_id=NULL")

    def delete_messages(self, ids):
        """Remove operator-selected content; retain request IDs to prevent replay."""
        if not isinstance(ids, list) or not ids or len(ids) > 200 or any(
                not isinstance(mid, str) or not mid for mid in ids):
            raise PolicyError("Select 1–200 message IDs")
        with self.connect(True) as db:
            count = 0
            for mid in set(ids):
                count += db.execute("""UPDATE messages SET body='[deleted]',detail='',
                    state='deleted',updated=? WHERE id=? AND state!='deleted'""",
                                    (time.time(), mid)).rowcount
            return count

    def clear_history(self, days=0):
        """Keep idempotency tombstones; remove content, never pending or ambiguous deliveries."""
        if isinstance(days, bool) or not isinstance(days, int) or days < 0:
            raise PolicyError("Retention days must be a nonnegative integer")
        with self.connect(True) as db:
            return db.execute("""UPDATE messages SET body='[cleared]',detail='' WHERE
                state IN ('completed','failed') AND created<? AND body!='[cleared]'""",
                              (time.time() - days * 86400,)).rowcount

    def maintain(self, days=30, *, compact=False):
        """Explicit offline maintenance; caller holds broker lock. No messages are deleted."""
        cleared = self.clear_history(days)
        with self.connect() as db:
            checkpoint = tuple(db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
            if checkpoint[0]:
                raise PolicyError("Database checkpoint is busy; retry when VISOR is closed")
            if compact:
                db.execute("VACUUM")
        return {"cleared_messages": cleared, "compacted": compact,
                "deduplication_preserved": True,
                "note": "Pending and ambiguous messages retained; clearing is not secure erasure."}
