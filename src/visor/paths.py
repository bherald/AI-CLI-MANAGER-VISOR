from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def prepare_runtime_path():
    """Use the operator's shell PATH even when started from a desktop launcher."""
    if (sys.platform != "win32" and os.environ.get("VISOR_SHELL_PATH") != "0"
            and os.environ.get("_VISOR_PATH_READY") != "1"):
        shell = os.environ.get("SHELL", "/bin/zsh" if sys.platform == "darwin" else "/bin/bash")
        try:
            # Interactive startup loads version managers such as NVM. Read only PATH;
            # startup chatter must not become part of an executable search directory.
            result = subprocess.run(
                [shell, "-ilc", "printf '\\000VISOR_PATH\\000%s\\000' \"$PATH\""],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=5, check=False,
            )
            marker = b"\x00VISOR_PATH\x00"
            if result.returncode == 0 and marker in result.stdout:
                value, separator, _ = result.stdout.rpartition(marker)[2].partition(b"\x00")
                if separator and value:
                    # Exclude implicit current-directory lookup from a GUI process.
                    entries = [entry for entry in os.fsdecode(value).split(os.pathsep)
                               if os.path.isabs(entry)]
                    if entries:
                        os.environ["PATH"] = os.pathsep.join(entries)
        except (OSError, subprocess.TimeoutExpired):
            pass  # Keep the inherited PATH when shell startup is unavailable.
        os.environ["_VISOR_PATH_READY"] = "1"
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        entries = os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin").split(os.pathsep)
        for directory in (Path.home() / ".local/bin", Path.home() / ".cargo/bin",
                          Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
            if directory.is_dir() and str(directory) not in entries:
                entries.append(str(directory))
        os.environ["PATH"] = os.pathsep.join(entries)


def data_dir(explicit: str | None = None) -> Path:
    if explicit or os.environ.get("VISOR_DATA_DIR"):
        root = Path(explicit or os.environ["VISOR_DATA_DIR"]).expanduser().resolve()
    elif sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Visor"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / "Visor"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "visor"
    created = not root.exists()
    root.mkdir(mode=0o777 if os.name == "nt" else 0o700, parents=True, exist_ok=True)
    if os.name == "nt":
        from .data_security import ensure_windows_private
        ensure_windows_private(root, created=created)
    else:
        root.chmod(0o700)
    return root


def write_private(path: Path, value: dict) -> None:
    """Atomic replacement; temporary files never contain broadly readable credentials."""
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def own_command(console: bool = False) -> list[str]:
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        if console:
            if sys.platform == "darwin" and exe.parent.name == "MacOS":
                exe = exe.parent.parent / "Helpers" / "visor-console" / "visor-console"
            else:
                exe = exe.with_name("visor-console.exe" if os.name == "nt" else "visor-console")
        return [str(exe)]
    exe = Path(sys.executable)
    if console and exe.name.lower() == "pythonw.exe":
        exe = exe.with_name("python.exe")
    return [str(exe), "-m", "visor"]

