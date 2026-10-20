"""Read-only process ownership evidence; inaccessible processes are never presumed dead."""
from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

import psutil


def process_identity(pid: int) -> dict:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("Invalid process ID")
    if sys.platform == "win32":
        from .win_identity import process_identity as native_identity
        return native_identity(pid)
    try:
        process = psutil.Process(pid)
        created = process.create_time()
        image = str(Path(process.exe()).resolve())
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            raise ProcessLookupError(errno.ESRCH, "Process has exited")
        return {"pid": pid, "created": created, "image": image}
    except (psutil.NoSuchProcess, psutil.ZombieProcess) as exc:
        raise ProcessLookupError(errno.ESRCH, "Process has exited") from exc
    except psutil.AccessDenied as exc:
        raise PermissionError(errno.EACCES, "Cannot inspect process identity") from exc


def retired(expected: dict | None) -> bool:
    if not expected or not isinstance(expected.get("pid"), int) or "created" not in expected:
        return False
    try:
        current = process_identity(expected["pid"])
        return current["created"] != expected["created"]
    except OSError as exc:
        return exc.errno == (87 if sys.platform == "win32" else errno.ESRCH)


def expected_helper_image() -> str:
    from .paths import own_command
    exe = Path(own_command(console=True)[0]).resolve()
    if not getattr(sys, "frozen", False):
        exe = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        if exe.name.casefold() == "pythonw.exe":
            exe = exe.with_name("python.exe")
    return os.path.normcase(str(exe))


def descendants(pid: int) -> list[dict]:
    """Snapshot only: detached jobs between observations cannot be proven absent."""
    try:
        children = psutil.Process(pid).children(recursive=True)
    except psutil.NoSuchProcess:
        return []
    except psutil.AccessDenied as exc:
        raise PermissionError(errno.EACCES, "Cannot inspect owned workers") from exc
    identities = []
    for child in children:
        try:
            identities.append(process_identity(child.pid))
        except OSError as exc:
            if exc.errno not in (errno.ESRCH, 87):
                raise
    return identities
