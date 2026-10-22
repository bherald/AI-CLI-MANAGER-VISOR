from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .paths import data_dir, prepare_runtime_path


def main():
    parser = argparse.ArgumentParser(description="VISOR — independent AI terminal supervisor")
    parser.add_argument("--data-dir", help="Explicit portable data directory (never commit it)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--broker", action="store_true")
    mode.add_argument("--mcp", action="store_true")
    mode.add_argument("--claude-hook", action="store_true")
    mode.add_argument("--terminal", type=Path)
    mode.add_argument("--doctor", action="store_true")
    mode.add_argument("--maintain-storage", action="store_true",
                      help="Offline: clear old completed/failed message text and checkpoint storage")
    mode.add_argument("--backup-state", action="store_true", help="Create a private SQLite state backup")
    mode.add_argument("--restore-state", type=Path, help="Offline recovery from a VISOR state backup")
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--compact", action="store_true", help="Also compact storage during offline maintenance")
    mode.add_argument("--smoke-test", action="store_true", help="Render UI offscreen and exit; no provider calls")
    mode.add_argument("--diagnose-loading", action="store_true", help="Load session metadata in the offscreen UI, report counts, and exit")
    parser.add_argument("--credential", type=Path)
    args = parser.parse_args()
    if not (args.mcp or args.claude_hook or args.terminal or args.maintain_storage or args.smoke_test):
        prepare_runtime_path()
    if args.retention_days < 0 or (args.compact and not args.maintain_storage):
        parser.error("Retention must be nonnegative; --compact requires --maintain-storage")
    if not getattr(sys, "frozen", False):
        source = str(Path(__file__).resolve().parent.parent)
        os.environ["PYTHONPATH"] = source + os.pathsep + os.environ.get("PYTHONPATH", "")
    if args.doctor:
        from .diagnostics import doctor
        print(json.dumps(doctor(), indent=2))
        return
    if args.mcp:
        if not args.credential:
            parser.error("--mcp requires --credential")
        from .mcp import serve
        serve(args.credential)
        return
    if args.claude_hook:
        if not args.credential:
            parser.error("--claude-hook requires --credential")
        from .claude import serve_hook
        serve_hook(args.credential)
        return
    if args.terminal:
        from .terminal import run_terminal
        run_terminal(args.terminal)
        return
    root = data_dir(args.data_dir)
    if args.backup_state:
        from .state_backup import snapshot
        print(snapshot(root))
        return
    if args.restore_state:
        from contextlib import ExitStack

        from .broker import BrokerLock
        from .state_backup import restore
        locks = []
        with ExitStack() as stack:
            for name in ("supervisor.lock", "broker.lock"):
                lock = BrokerLock(root / name)
                locks.append(lock)
                stack.callback(lock.close)
            print(restore(root, args.restore_state))
        return
    if args.maintain_storage:
        from .broker import BrokerLock
        from .store import Store
        lock = BrokerLock(root / "broker.lock")
        try:
            print(json.dumps(Store(root).maintain(args.retention_days, compact=args.compact), indent=2))
        finally:
            lock.close()
        return
    if args.broker:
        from .broker import serve
        serve(root)
        return
    try:
        from .ui import launch
    except ModuleNotFoundError as exc:
        if exc.name == "PySide6":
            raise SystemExit("PySide6 is missing. Run scripts/bootstrap.py with network access first.") from exc
        raise
    launch(root, smoke=args.smoke_test or args.diagnose_loading, discover=args.diagnose_loading)


if __name__ == "__main__":
    main()
