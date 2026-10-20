"""Visible X11 acceptance with disposable terminals; no providers or model calls.

Run with the project Python from an X11 desktop. Only this probe's terminals
are moved/focused/closed. Results remain in the repository's ignored cache.
"""
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))

from visor.external_windows import candidates, focus, validate
from visor.layout import Rect
from visor.platforms import Linux, WindowNotFound, WindowUnavailable, terminal_launch
from visor.process import run
from visor.process_identity import process_identity, retired


def wait_for(predicate, message, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise RuntimeError(message)


def main():
    if not sys.platform.startswith("linux") or not os.environ.get("DISPLAY"):
        raise SystemExit("Run this visible acceptance check on a Linux X11 desktop")
    missing = [name for name in ("xterm", "wmctrl", "xprop") if not shutil.which(name)]
    if missing:
        raise SystemExit("Install acceptance prerequisites: " + ", ".join(missing))
    adapter = Linux()
    adapter._check()  # Wayland must fail explicitly, even with XWayland available.
    work = root / ".cache" / "linux-window-acceptance" / uuid.uuid4().hex / "project space's"
    work.mkdir(parents=True)
    fixture = work / "terminal fixture.py"
    fixture.write_text('''import json, os, sys, time
from pathlib import Path
# Exercise the title changes emitted by interactive providers and shells.
print("\\033]0;provider title override\\007", end="", flush=True)
Path(sys.argv[1]).write_text(json.dumps({"cwd": os.getcwd(), "args": sys.argv[2:], "pid": os.getpid()}))
time.sleep(120)
''', encoding="utf-8")
    processes, terminals, workers = [], [], []
    argument = "space's; $(literal)"
    try:
        for index in range(2):
            token = "VISOR-X11-acceptance-" + uuid.uuid4().hex
            record = work / f"worker-{index}.json"
            processes.append(terminal_launch(
                [sys.executable, str(fixture), str(record), argument], str(work), token))

            def ready(record=record, token=token):
                try:
                    return record.is_file() and bool(adapter.find(token))
                except WindowNotFound:
                    return False

            wait_for(ready, "Managed terminal missing (including after provider title change)")
            data = json.loads(record.read_text())
            assert data["cwd"] == str(work) and data["args"] == [argument], data
            workers.append(process_identity(data["pid"]))
            terminals.append((token, {"handle": adapter.find(token)[0]}))
        token, identity = terminals[0]
        other_token, other_identity = terminals[1]
        original_other = adapter.geometry(other_token, other_identity)
        try:
            adapter.close(token, other_identity)
        except WindowUnavailable:
            pass
        else:
            raise AssertionError("A mismatched window identity was accepted")
        # Exercise geometry without assuming window-manager decoration offsets.
        adapter.place(token, Rect(40, 50, 640, 400), identity)
        wait_for(lambda: adapter.geometry(token, identity).width == 640,
                 "Window manager did not apply terminal dimensions")
        assert adapter.geometry(other_token, other_identity) == original_other
        adapter.focus(token, identity)

        def active():
            result = run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
            return result.returncode == 0 and int(result.stdout.split()[-1], 16) == int(identity["handle"], 16)

        wait_for(active, "Focus did not activate the exact test window")
        matches = [item for item in candidates() if item["identity"]["handle"] == identity["handle"]]
        assert len(matches) == 1, matches
        external = matches[0]["identity"]
        validate(external)
        focus(external)
        wait_for(active, "External Focus did not activate the exact test window")
        adapter.close(token, identity)
        wait_for(lambda: retired(workers[0]), "Closed terminal's worker did not stop")
        try:
            validate(external)
        except WindowUnavailable:
            pass
        else:
            raise AssertionError("Closed external window identity was accepted")
        assert adapter.find(other_token)[0] == other_identity["handle"]
        assert not retired(workers[1]), "Adjacent worker stopped unexpectedly"
        adapter.close(other_token, other_identity)
        wait_for(lambda: retired(workers[1]), "Adjacent test worker did not stop on close")
        result = {"platform": "Linux/X11", "passed": ["launch/quoted cwd and arguments",
                  "title override protection", "identity mismatch rejection", "placement/geometry",
                  "exact focus", "external focus/stale rejection", "close/worker exit",
                  "adjacent window and worker preservation"], "provider_calls": False}
        (work / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        print(f"Acceptance evidence: {work}")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()  # Only the exact xterm processes created above.
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
