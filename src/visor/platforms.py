from __future__ import annotations

import ctypes
import os
import shlex
import shutil
import subprocess
import sys
import time
from ctypes import wintypes

from .layout import Rect
from .process import hidden_options, run


class WindowUnavailable(RuntimeError):
    pass


class WindowNotFound(WindowUnavailable):
    pass


def linux_terminal(preference="auto"):
    """Supported independent terminals; auto inherits Konsole's desktop profile."""
    if preference not in ("auto", "konsole", "xterm"):
        raise WindowUnavailable("Unsupported Linux terminal")
    for name in (("konsole", "xterm") if preference == "auto" else (preference,)):
        executable = shutil.which(name)
        if executable:
            return name, executable
    raise WindowUnavailable("Install Konsole or xterm, or choose an installed Linux terminal in Terminal appearance")


def _desktop_terminal_environment():
    # PyInstaller prepends its own Qt libraries/plugins. Native Konsole must use
    # the system Qt installation, while retaining DISPLAY, PATH and provider env.
    environment = os.environ.copy()
    if getattr(sys, "frozen", False):
        original = environment.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            environment.pop("LD_LIBRARY_PATH", None)
        else:
            environment["LD_LIBRARY_PATH"] = original
        for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
            environment.pop(key, None)
    return environment


def terminal_launch(command: list[str], cwd: str, token: str, appearance=None):
    """Only the intentional managed terminal is visible; no shell-interpolated user messages."""
    from .appearance import validate
    settings = validate(appearance or {})
    if sys.platform == "win32":
        wt = shutil.which("wt")
        if not wt:
            raise WindowUnavailable("Windows Terminal (wt.exe) is required")
        profile = ["--profile", settings["windows_profile"]] if settings["windows_profile"] else []
        subprocess.Popen([wt, "-w", "new", "new-tab", *profile, "--title", token,
                          "--suppressApplicationTitle", "-d", cwd, *command], **hidden_options())
    elif sys.platform == "darwin":
        # Terminal.app may already be running with an older desktop environment.
        # Pass the resolved search paths explicitly, including source-mode imports.
        environment = [f"{key}={os.environ[key]}" for key in ("PATH", "PYTHONPATH")
                       if key in os.environ]
        shell_command = "cd " + shlex.quote(cwd) + " && exec " + shlex.join(
            ["/usr/bin/env", *environment, *command])
        # AppleScript receives values as argv, not source text.
        script = '''on run argv
tell application "Terminal"
set t to do script (item 1 of argv)
if (count of argv) > 2 then
set current settings of t to settings set (item 3 of argv)
end if
set custom title of t to item 2 of argv
activate
end tell
end run'''
        # Check profile existence before launching any provider process.
        if settings["mac_profile"]:
            check = 'on run argv\ntell application "Terminal" to get name of settings set (item 1 of argv)\nend run'
            checked = run(["osascript", "-e", check, settings["mac_profile"]])
            if checked.returncode:
                raise WindowUnavailable("Terminal profile is unavailable; check its name and Automation permission")
        profile_arg = [settings["mac_profile"]] if settings["mac_profile"] else []
        result = run(["osascript", "-e", script, shell_command, token, *profile_arg])
        if result.returncode:
            raise WindowUnavailable("Terminal automation failed; check macOS Automation permission")
    else:
        name, executable = linux_terminal(settings["linux_terminal"])
        if name == "konsole":
            # Separate process/window, default font/colors, no existing tabs reused.
            # Literal local/remote formats omit %w, so OSC title changes cannot
            # replace the ownership token. Konsole adds its standard app suffix.
            return subprocess.Popen([executable, "--separate", "--workdir", cwd,
                                     "-p", f"LocalTabTitleFormat={token}",
                                     "-p", f"RemoteTabTitleFormat={token}",
                                     "-e", *command], cwd=cwd, env=_desktop_terminal_environment(),
                                    start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Providers/shells emit OSC title changes. Keep the launch token stable.
        font = ["-fa", settings["linux_font"], "-fs", str(settings["linux_size"])] if settings["linux_font"] else []
        return subprocess.Popen([executable, *font, "-T", token, "-n", token,
                          "-xrm", "XTerm.vt100.allowTitleOps: false", "-e", *command], cwd=cwd,
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Windows:
    def __init__(self):
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.api.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.api.IsWindowVisible.argtypes = [wintypes.HWND]
        self.api.IsWindow.argtypes = [wintypes.HWND]
        self.api.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.api.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.api.GetForegroundWindow.restype = wintypes.HWND
        self.api.IsIconic.argtypes = [wintypes.HWND]
        self.api.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL]

    def find(self, token):
        found = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.api.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]

        @callback_type
        def callback(hwnd, param):
            if self.api.IsWindowVisible(hwnd):
                size = self.api.GetWindowTextLengthW(hwnd)
                text = ctypes.create_unicode_buffer(size + 1)
                self.api.GetWindowTextW(hwnd, text, size + 1)
                if text.value == token:
                    found.append(hwnd)
            return True
        self.api.EnumWindows(callback, 0)
        if not found:
            raise WindowNotFound("Managed terminal window no longer exists")
        if len(found) != 1:
            raise WindowUnavailable("Managed window association is missing or ambiguous")
        return found[0]

    def focus(self, token, expected_identity=None):
        hwnd = self.find(token)
        if expected_identity is not None:
            from .win_identity import window_identity
            if window_identity(hwnd) != expected_identity:
                raise WindowUnavailable("Terminal identity changed before focus; refresh and try again")
        if self.api.IsIconic(hwnd):
            self.api.ShowWindow(hwnd, 9)
        if not self.api.SetForegroundWindow(hwnd) and self.api.GetForegroundWindow() != hwnd:
            raise WindowUnavailable("Focus was interrupted by another window. Click Focus again.")

    def geometry(self, token, expected_identity=None):
        hwnd = self.find(token)
        if expected_identity is not None:
            from .win_identity import window_identity
            if window_identity(hwnd) != expected_identity:
                raise WindowUnavailable("Terminal identity changed before reading geometry")
        rect = wintypes.RECT()
        if not self.api.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise WindowUnavailable("Cannot read window geometry")
        return Rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)

    def close(self, token, expected_identity=None):
        from .win_identity import window_identity
        hwnd = self.find(token)
        if not expected_identity or window_identity(hwnd) != expected_identity:
            raise WindowUnavailable("Terminal identity changed before close")
        self.api.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        if not self.api.PostMessageW(hwnd, 0x10, 0, 0):  # WM_CLOSE: exact window, never shared WT process.
            raise WindowUnavailable("Windows declined terminal close")
        wait_window_closed(lambda: self.api.IsWindow(hwnd))

    def place(self, token, rect, expected_identity=None):
        hwnd = self.find(token)
        if expected_identity is not None:
            from .win_identity import window_identity
            if window_identity(hwnd) != expected_identity:
                raise WindowUnavailable("Terminal identity changed before arrangement")
        self.api.ShowWindow(hwnd, 9)
        if not self.api.MoveWindow(hwnd, rect.x, rect.y, rect.width, rect.height, True):
            raise WindowUnavailable("Windows declined arrangement")


class Linux:
    def _check(self):
        if os.environ.get("XDG_SESSION_TYPE") == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
            raise WindowUnavailable("Window control is unavailable on Wayland; arrange terminals manually")
        if not shutil.which("wmctrl"):
            raise WindowUnavailable("Install wmctrl for X11 window control")

    def find(self, token):
        self._check()
        result = run(["wmctrl", "-lG"])
        if result.returncode:
            raise WindowUnavailable("Cannot enumerate X11 windows; check DISPLAY and window manager support")
        matches = []
        for line in result.stdout.splitlines():
            columns = line.split(None, 7)
            if len(columns) == 8 and columns[7] in (token, token + " — Konsole"):
                matches.append(columns)
        if not matches:
            raise WindowNotFound("Managed terminal window no longer exists")
        if len(matches) != 1:
            raise WindowUnavailable("Managed window association is missing or ambiguous")
        return matches[0]

    def _verified_id(self, token, expected_identity=None):
        wid = self.find(token)[0]
        if expected_identity is not None and expected_identity != {"handle": wid}:
            raise WindowUnavailable("Terminal window identity changed; refresh and try again")
        return wid

    def focus(self, token, expected_identity=None):
        result = run(["wmctrl", "-ia", self._verified_id(token, expected_identity)])
        if result.returncode:
            raise WindowUnavailable("Window manager declined focus")

    def close(self, token, expected_identity=None):
        if not expected_identity:
            raise WindowUnavailable("Terminal identity has not been verified")
        wid = self._verified_id(token, expected_identity)
        if run(["wmctrl", "-ic", wid]).returncode:
            raise WindowUnavailable("Window manager declined terminal close")
        def exists():
            result = run(["wmctrl", "-l"])
            if result.returncode:
                # A window can disappear between wmctrl reading _NET_CLIENT_LIST
                # and its properties. Retry that race; it does not prove closure.
                if "BadWindow (invalid Window parameter)" in result.stderr:
                    return True
                raise WindowUnavailable("Cannot verify terminal closure")
            return any(line.split()[0] == wid for line in result.stdout.splitlines() if line.split())
        wait_window_closed(exists)

    def geometry(self, token, expected_identity=None):
        row = self.find(token)
        if expected_identity is not None and expected_identity != {"handle": row[0]}:
            raise WindowUnavailable("Terminal identity changed before reading geometry")
        return Rect(*map(int, row[2:6]))

    def place(self, token, rect, expected_identity=None):
        wid = self._verified_id(token, expected_identity)
        run(["wmctrl", "-ir", wid, "-b", "remove,maximized_vert,maximized_horz"])
        result = run(["wmctrl", "-ir", wid, "-e", f"0,{rect.x},{rect.y},{rect.width},{rect.height}"])
        if result.returncode:
            raise WindowUnavailable("Window manager declined arrangement")


class Mac:
    def _call(self, token, operation, values=(), expected_identity=None):
        body = {
            "find": "return id of w as text",
            "focus": "set miniaturized of w to false\nset index of w to 1\nactivate\nreturn id of w as text",
            "close": 'if count of tabs of w is not 1 then error "VISOR_EXTRA_TABS"\nset wid to id of w\nclose w saving no\nreturn wid as text',
            "geometry": 'set b to bounds of w\nreturn (item 1 of b as text) & "," & (item 2 of b as text) & "," & (item 3 of b as text) & "," & (item 4 of b as text)',
            "place": "set bounds of w to {(item 3 of argv as integer), (item 4 of argv as integer), (item 5 of argv as integer), (item 6 of argv as integer)}\nreturn id of w as text"
        }[operation]
        script = '''on run argv
if application "Terminal" is not running then error "VISOR_WINDOW_NOT_FOUND"
tell application "Terminal"
set matches to {}
repeat with w in windows
if custom title of selected tab of w is item 1 of argv then set end of matches to w
end repeat
if count of matches is 0 then error "VISOR_WINDOW_NOT_FOUND"
if count of matches is not 1 then error "VISOR_WINDOW_AMBIGUOUS"
set w to item 1 of matches
if item 2 of argv is not "" and (id of w as text) is not item 2 of argv then error "VISOR_IDENTITY_CHANGED"
''' + body + "\nend tell\nend run"
        expected = (expected_identity or {}).get("handle", "")
        result = run(["osascript", "-e", script, token, str(expected), *map(str, values)])
        if result.returncode:
            if "VISOR_WINDOW_NOT_FOUND" in result.stderr:
                raise WindowNotFound("Managed terminal window no longer exists")
            errors = {
                "VISOR_WINDOW_AMBIGUOUS": "Managed Terminal window is ambiguous; preserve all windows and reconcile the association",
                "VISOR_IDENTITY_CHANGED": "Terminal identity changed; window control cancelled",
                "VISOR_EXTRA_TABS": "Close other tabs or move the managed tab to its own window before deleting",
            }
            for marker, message in errors.items():
                if marker in result.stderr:
                    raise WindowUnavailable(message)
            raise WindowUnavailable("Cannot control Terminal; check System Settings > Privacy & Security > Automation and allow this VISOR application to control Terminal")
        return result.stdout.strip()

    def find(self, token):
        return self._call(token, "find")

    def focus(self, token, expected_identity=None):
        self._call(token, "focus", expected_identity=expected_identity)

    def close(self, token, expected_identity=None):
        if not expected_identity:
            raise WindowUnavailable("Terminal identity has not been verified")
        wid = self._call(token, "close", expected_identity=expected_identity)
        def exists():
            result = run(["osascript", "-e", 'if application "Terminal" is not running then return ""\ntell application "Terminal" to get id of every window'])
            if result.returncode:
                raise WindowUnavailable("Cannot verify terminal closure")
            return wid in result.stdout.replace(",", " ").split()
        wait_window_closed(exists)

    def geometry(self, token, expected_identity=None):
        x, y, right, bottom = map(int, self._call(token, "geometry", expected_identity=expected_identity).split(","))
        return Rect(x, y, right - x, bottom - y)

    def place(self, token, rect, expected_identity=None):
        self._call(token, "place", (rect.x, rect.y, rect.x + rect.width, rect.y + rect.height),
                   expected_identity=expected_identity)


def windows():
    return Windows() if sys.platform == "win32" else Mac() if sys.platform == "darwin" else Linux()


def wait_window_closed(exists, timeout=5):
    deadline = time.monotonic() + timeout
    while exists():
        if time.monotonic() >= deadline:
            raise WindowUnavailable("Terminal did not close; session history was preserved. Resolve its close prompt and retry.")
        time.sleep(0.05)


def focus_verified(target):
    """Run in the foreground UI process after the broker verifies ownership."""
    if target.get("external"):
        from .external_windows import focus
        focus(target["identity"])
        return
    adapter = windows()
    if not target.get("identity"):
        raise WindowUnavailable("Terminal identity has not been verified")
    adapter.focus(target["token"], expected_identity=target["identity"])


def allow_foreground(pid):
    """Let a user-launched second instance activate the existing supervisor."""
    if sys.platform != "win32":
        return True
    if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid < 0xFFFFFFFF:
        raise ValueError("Invalid foreground owner PID")
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
    api.AllowSetForegroundWindow.restype = wintypes.BOOL
    return bool(api.AllowSetForegroundWindow(pid))


def enable_window_resize(hwnd):
    """Frameless Qt windows need WS_THICKFRAME for Windows border sizing."""
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    api.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    api.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    api.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    api.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
    style = api.GetWindowLongPtrW(hwnd, -16)
    if style & 0x40000:
        return
    ctypes.set_last_error(0)
    previous = api.SetWindowLongPtrW(hwnd, -16, style | 0x40000)
    if not previous and ctypes.get_last_error():
        raise OSError(ctypes.get_last_error(), "Cannot enable supervisor edge resizing")
    if not api.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x37):
        raise OSError(ctypes.get_last_error(), "Cannot refresh supervisor resizing frame")
