"""Owner-only SQLite snapshots and conservative offline recovery; no provider history."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import psutil

OPTIONS = {"before_upgrade": False, "keep": 5}


@contextmanager
def connection(*args, **kwargs):
    db = sqlite3.connect(*args, **kwargs)
    try:
        with db:
            yield db
    finally:
        db.close()


def validate_options(value):
    if not isinstance(value, dict) or value.keys() - OPTIONS.keys():
        raise ValueError("Invalid backup settings")
    value = {**OPTIONS, **value}
    if type(value["before_upgrade"]) is not bool or type(value["keep"]) is not int or not 1 <= value["keep"] <= 20:
        raise ValueError("Keep between 1 and 20 backups")
    return value


def read_options(root):
    path = Path(root) / "visor.sqlite3"
    if not path.is_file():
        return dict(OPTIONS)
    with connection(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT value FROM settings WHERE key='backup_options'").fetchone()
    return validate_options(json.loads(row[0]) if row else {})


def snapshot(root, *, prune=True):
    root = Path(root).resolve()
    folder = root / "backups"
    if folder.is_symlink() or folder.exists() and folder.resolve() != folder:
        raise ValueError("Backup directory must not be a symbolic link")
    created = not folder.exists()
    folder.mkdir(mode=0o700, exist_ok=True)
    if os.name == "nt":
        from .data_security import ensure_windows_private
        ensure_windows_private(folder, created=created)
    else:
        folder.chmod(0o700)
    fd, name = tempfile.mkstemp(prefix=".snapshot-", dir=folder)
    os.close(fd)
    temp = Path(name)
    target = folder / (time.strftime("visor-%Y%m%d-%H%M%S-") + uuid.uuid4().hex + ".sqlite3")
    try:
        with connection((root / "visor.sqlite3").as_uri() + "?mode=ro", uri=True) as source, connection(temp) as destination:
            source.backup(destination)
            if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Backup failed SQLite integrity check")
        os.replace(temp, target)
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(str(temp) + suffix).unlink(missing_ok=True)
    if prune:
        keep = read_options(root)["keep"]
        for old in sorted(folder.glob("visor-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)[keep:]:
            if old.is_file() and not old.is_symlink():
                old.unlink()
    return str(target)


def restore(root, backup):
    """Caller must own both broker and supervisor locks throughout this operation."""
    root, backup = Path(root).resolve(), Path(backup).expanduser().resolve()
    if not backup.is_file() or backup == root / "visor.sqlite3":
        raise ValueError("Choose a separate existing VISOR backup file")
    with connection(backup.as_uri() + "?mode=ro", uri=True) as source:
        if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Backup failed SQLite integrity check")
        if source.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Unsupported backup schema version")
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != {"settings", "sessions", "pairs", "credentials", "deleted_sessions", "messages"}:
            raise ValueError("Not a supported VISOR state backup")
        if source.execute("SELECT COUNT(*) FROM sqlite_master WHERE type IN ('trigger','view')").fetchone()[0]:
            raise ValueError("Backup contains unsupported database objects")
        current = root / "visor.sqlite3"
        if current.exists():
            with connection(current) as db:
                for (pid,) in db.execute("SELECT process_id FROM sessions WHERE process_id IS NOT NULL"):
                    if psutil.pid_exists(int(pid)):
                        raise ValueError("Close managed console helpers before restoring VISOR state")
                if db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                    raise ValueError("State database is still busy")
            snapshot(root, prune=False)  # Retain the pre-restore snapshot regardless of normal retention.
        fd, name = tempfile.mkstemp(prefix=".restore-", dir=root)
        os.close(fd)
        temp = Path(name)
        try:
            with connection(temp) as destination:
                source.backup(destination)
                destination.execute("DELETE FROM credentials")
                destination.execute("UPDATE sessions SET managed=0,heartbeat=NULL,process_id=NULL,window_token=NULL,helper_identity=NULL,window_identity=NULL,status='unknown',shutdown=''")
                destination.execute("UPDATE messages SET state='held',detail='Held after backup recovery; review before release' WHERE state='queued'")
                destination.execute("UPDATE messages SET state='delivery_unknown',detail='Interrupted delivery restored from backup' WHERE state='sending'")
                destination.execute("INSERT OR REPLACE INTO settings VALUES('agent_enabled','false')")
                destination.execute("DELETE FROM settings WHERE key LIKE 'external_focus%' OR key LIKE 'claude_runtime:%' OR key IN ('shutdown_targets')")
            # Never reuse stale WAL state with the replacement database.
            for suffix in ("-wal", "-shm"):
                Path(str(current) + suffix).unlink(missing_ok=True)
            os.replace(temp, current)
            (root / "broker.json").unlink(missing_ok=True)
        finally:
            for suffix in ("", "-wal", "-shm"):
                Path(str(temp) + suffix).unlink(missing_ok=True)
    return "State restored. Pending messages held, old credentials invalidated, terminal links cleared."
