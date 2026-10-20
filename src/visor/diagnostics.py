"""Read-only prerequisites and explicit platform capabilities; never authenticates providers."""
from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys

from .process import run
from .providers import ProviderUnavailable, provider_command


def doctor():
    wayland = sys.platform.startswith("linux") and bool(
        os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland")
    terminal = "wt" if sys.platform == "win32" else "osascript" if sys.platform == "darwin" else "xterm"
    warnings = []
    providers = {}
    for provider in ("codex", "claude"):
        exe = shutil.which(provider)
        info = {"executable": exe, "version": None}
        if exe:
            try:
                command = provider_command(provider)
                info["executable"] = command[0]
                info["command"] = command
                if command != [exe]:
                    info["launcher"] = exe
                result = run([*command, "--version"], timeout=5)
                info["version"] = result.stdout.strip()[:200] if not result.returncode else "unavailable"
            except ProviderUnavailable as exc:
                warnings.append(f"{provider}: {exc}")
            except (OSError, subprocess.TimeoutExpired):
                info["version"] = "unavailable"
        providers[provider] = info
    prerequisites = {terminal: shutil.which(terminal)}
    if sys.platform.startswith("linux"):
        prerequisites["wmctrl"] = shutil.which("wmctrl")
        if not wayland and not os.environ.get("DISPLAY"):
            warnings.append("DISPLAY is missing; use a graphical desktop for native acceptance")
        if wayland:
            warnings.append("Wayland: Focus, arrangement and managed-window closure are unavailable")
        elif not prerequisites["wmctrl"]:
            warnings.append("Install wmctrl for X11 window control")
    if not prerequisites[terminal]:
        warnings.append(f"Install {terminal} before launching managed terminals")
    if sys.platform == "darwin":
        warnings.append("Terminal control requires macOS Automation permission; this diagnostic does not request it")
    return {"python": sys.version.split()[0], "platform": sys.platform,
            "architecture": platform.machine(),
            "pyside6": importlib.util.find_spec("PySide6") is not None,
            "psutil": importlib.util.find_spec("psutil") is not None,
            "providers": providers, "prerequisites": prerequisites, "wayland": wayland,
            "window_control": "unavailable" if wayland else "native acceptance required",
            "warnings": warnings}
