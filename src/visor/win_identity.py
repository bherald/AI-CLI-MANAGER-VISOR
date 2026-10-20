"""Read-only Windows identity probes. PID alone is never treated as stable identity."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path


def process_identity(pid: int) -> dict:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("Invalid process ID")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        raise OSError(ctypes.get_last_error(), "Cannot verify process identity")
    try:
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
            raise OSError(ctypes.get_last_error(), "Cannot read process creation time")
        length = wintypes.DWORD(32768)
        path = ctypes.create_unicode_buffer(length.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
            raise OSError(ctypes.get_last_error(), "Cannot read process image")
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise OSError(ctypes.get_last_error(), "Cannot read process exit status")
        if code.value != 259:
            raise OSError(87, "Process has exited")
        return {"pid": pid, "created": (created.dwHighDateTime << 32) | created.dwLowDateTime,
                "image": str(Path(path.value)).casefold()}
    finally:
        kernel.CloseHandle(handle)


def window_identity(hwnd: int) -> dict:
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    pid = wintypes.DWORD()
    if not api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
        raise OSError(ctypes.get_last_error(), "Window no longer exists")
    identity = process_identity(pid.value)
    if Path(identity["image"]).name.casefold() not in ("windowsterminal.exe", "windowsterminalpreview.exe"):
        raise ValueError("The selected window is not Windows Terminal")
    return {"hwnd": int(hwnd), **identity}

