# Private VISOR state backups and recovery

The supervisor menu provides **State backup settings**, **Back up VISOR state now**, and recovery instructions. Automatic backups before broker upgrades are opt-in. Retention is configurable from 1 to 20 snapshots (default 5); it is applied when creating a normal backup. Recovery also saves a pre-restore snapshot without pruning existing backups at that point.

Backups contain VISOR sessions, settings, message drafts, message history, deduplication/tombstone records and scoped credentials. They are stored under the private VISOR data directory in `backups/`. They can retain messages later deleted from the live database. Keep backups private and delete unneeded copies according to your retention needs. Provider authentication and provider-owned conversation history are not backed up or restored. This is not secure erasure or an external disaster-recovery service.

The SQLite backup API creates a consistent snapshot while the broker is running. The backup is checked and atomically finalized; POSIX files are owner-only, and Windows inherits the protected data-directory permissions. A normal backup can also be created with the console helper:

```text
visor-console --backup-state --data-dir "<VISOR data directory>"
```

On Windows, use `visor-console.exe`. With Python source, use `.venv/bin/python -m visor` (Windows: `.venv\Scripts\python.exe -m visor`) instead of the console helper.

## Offline recovery

1. Save work in managed consoles. Close their helpers/console windows normally.
2. Use **Stop broker** in the supervisor menu, then exit VISOR. Recovery refuses a locked broker/supervisor or a recorded helper PID still running. It never kills a process to proceed.
3. Choose an existing backup and run:

```text
visor-console --restore-state "<backup.sqlite3>" --data-dir "<VISOR data directory>"
```

Recovery checks database integrity and schema, saves the existing state before replacement, and restores the snapshot atomically. It disables agent communication, holds queued messages, marks interrupted sends as delivery-unknown, invalidates old scoped credentials and clears terminal/window ownership links. Review held messages before releasing them. Old helper/MCP credentials need new association; independently running provider processes are not adopted automatically. Restored messages can include content deleted since the snapshot.

Reopen VISOR and inspect session state before resuming or relinking consoles. Provider history and project files are unchanged. Backups made by a newer schema are refused; retain the original backup and use the matching/newer application.
