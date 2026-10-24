"""Operator-selected window links for Focus only; these do not establish session ownership."""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

import psutil

from .platforms import Linux, Windows, WindowUnavailable
from .process import run
from .process_identity import process_identity


def _windows_candidates():
    from .win_identity import window_identity
    adapter = Windows()
    candidates = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    adapter.api.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]

    @callback_type
    def callback(hwnd, param):
        if adapter.api.IsWindowVisible(hwnd):
            try:
                identity = {"platform": "win32", **window_identity(hwnd)}
            except (OSError, ValueError):
                return True
            size = adapter.api.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(size + 1)
            adapter.api.GetWindowTextW(hwnd, title, size + 1)
            candidates.append({"title": title.value or "Windows Terminal", "identity": identity})
        return True
    if not adapter.api.EnumWindows(callback, 0):
        raise WindowUnavailable("Cannot enumerate terminal windows")
    return candidates


def _linux_candidates():
    Linux()._check()
    result = run(["wmctrl", "-lp"])
    if result.returncode:
        raise WindowUnavailable("Cannot enumerate X11 terminal windows")
    candidates = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) != 5:
            continue
        try:
            identity = process_identity(int(parts[2]))
        except (OSError, ValueError):
            continue
        if Path(identity["image"]).name not in ("xterm", "uxterm", "konsole"):
            continue
        candidates.append({"title": parts[4], "identity": {"platform": "linux", "handle": parts[0], **identity}})
    return candidates


def _mac_process():
    matches = []
    for process in psutil.process_iter(["pid", "name", "uids"]):
        if process.info["name"] != "Terminal" or not process.info["uids"] or process.info["uids"].real != os.getuid():
            continue
        try:
            identity = process_identity(process.pid)
        except (OSError, ValueError):
            continue
        if identity["image"].endswith("/Terminal.app/Contents/MacOS/Terminal"):
            matches.append(identity)
    if len(matches) != 1:
        raise WindowUnavailable("Terminal.app process identity is missing or ambiguous")
    return matches[0]


def _mac_call(handle, operation):
    if not isinstance(handle, str) or not handle.isdecimal() or len(handle) > 16:
        raise WindowUnavailable("Invalid Terminal window identity")
    body = "return name of w as text" if operation == "title" else "set miniaturized of w to false\nset index of w to 1\nactivate"
    script = '''on run argv
if application "Terminal" is not running then error "VISOR_WINDOW_NOT_FOUND"
tell application "Terminal"
set wid to item 1 of argv as integer
set w to first window whose id is wid
''' + body + "\nend tell\nend run"
    result = run(["osascript", "-e", script, handle])
    if result.returncode:
        raise WindowUnavailable("Cannot access the linked Terminal window; check Automation permission")
    return result.stdout.strip()


def _mac_candidates():
    identity = _mac_process()
    result = run(["osascript", "-e", 'tell application "Terminal" to get id of every window'])
    if result.returncode:
        raise WindowUnavailable("Cannot enumerate Terminal windows; check Automation permission")
    handles = result.stdout.replace(",", " ").split()
    return [{"title": _mac_call(handle, "title"),
             "identity": {"platform": "darwin", "handle": handle, **identity}} for handle in handles]


def candidates():
    if sys.platform == "win32":
        return _windows_candidates()
    if sys.platform == "darwin":
        return _mac_candidates()
    return _linux_candidates()


def validate(expected):
    if not isinstance(expected, dict) or expected.get("platform") != sys.platform:
        raise WindowUnavailable("External terminal link belongs to a different platform")
    matches = [item for item in candidates() if item["identity"] == expected]
    if len(matches) != 1:
        raise WindowUnavailable("External terminal identity changed or is unavailable; select its window again")
    return matches[0]


def focus(expected):
    validate(expected)
    if sys.platform == "win32":
        from .win_identity import window_identity
        hwnd = expected["hwnd"]
        current = {"platform": "win32", **window_identity(hwnd)}
        if current != expected:
            raise WindowUnavailable("External terminal identity changed before focus")
        adapter = Windows()
        if adapter.api.IsIconic(hwnd):
            adapter.api.ShowWindow(hwnd, 9)
        if not adapter.api.SetForegroundWindow(hwnd) and adapter.api.GetForegroundWindow() != hwnd:
            raise WindowUnavailable("Focus was interrupted. Click Focus again.")
    elif sys.platform == "darwin":
        if {"platform": "darwin", "handle": expected["handle"], **_mac_process()} != expected:
            raise WindowUnavailable("Terminal.app identity changed before focus")
        _mac_call(expected["handle"], "focus")
    else:
        current = {"platform": "linux", "handle": expected["handle"], **process_identity(expected["pid"])}
        if current != expected:
            raise WindowUnavailable("External terminal process identity changed before focus")
        if run(["wmctrl", "-ia", expected["handle"]]).returncode:
            raise WindowUnavailable("Window manager declined focus")
