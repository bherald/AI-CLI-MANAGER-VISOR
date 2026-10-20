from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from pathlib import Path

from .broker import Client

log = logging.getLogger(__name__)


def run_terminal(ticket: Path):
    config = json.loads(ticket.read_text(encoding="utf-8"))
    ticket.unlink()  # One-shot launch ticket; credentials remain for this session's MCP process.
    client = Client(Path(config["root"]), config["helper_token"])
    stopped = threading.Event()

    def heartbeat():
        while not stopped.is_set():
            try:
                client.call("heartbeat", pid=os.getpid())
            except (OSError, ValueError):
                log.debug("Broker heartbeat unavailable", exc_info=True)
            stopped.wait(5)

    worker = threading.Thread(target=heartbeat, daemon=True)
    worker.start()
    try:
        try:
            process = subprocess.Popen(config["command"], cwd=config["cwd"],
                                       env={**os.environ, **config.get("env", {})})
            process.wait()
        except (OSError, KeyboardInterrupt) as exc:
            print(f"\nProvider stopped or could not start: {exc}")
        try:
            client.call("provider_exit")
        except (OSError, ValueError):
            log.debug("Provider exit notification unavailable", exc_info=True)
        print("\nProvider exited. This terminal remains open for inspection.")
        try:
            input("Press Enter to close this terminal. ")
        except (EOFError, KeyboardInterrupt):
            pass
    finally:
        stopped.set()
        worker.join(timeout=1)
        try:
            client.call("terminal_exit")
        except (OSError, ValueError):
            log.debug("Terminal exit notification unavailable", exc_info=True)

