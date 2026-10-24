"""Per-platform terminal appearance; preferences affect new VISOR windows only."""
from __future__ import annotations

DEFAULTS = {"linux_terminal": "auto", "linux_font": "", "linux_size": 12, "windows_profile": "", "mac_profile": ""}


def validate(value):
    if not isinstance(value, dict) or value.keys() - DEFAULTS.keys():
        raise ValueError("Invalid terminal appearance settings")
    result = {**DEFAULTS, **value}
    if result["linux_terminal"] not in ("auto", "konsole", "xterm"):
        raise ValueError("Linux terminal must be auto, konsole or xterm")
    for key in ("linux_font", "windows_profile", "mac_profile"):
        text = result[key]
        if not isinstance(text, str) or len(text) > 160 or any(ord(c) < 32 or ord(c) == 127 for c in text):
            raise ValueError("Font/profile names must contain at most 160 printable characters")
        result[key] = text.strip()
    if type(result["linux_size"]) is not int or not 6 <= result["linux_size"] <= 48:
        raise ValueError("Terminal font size must be between 6 and 48 points")
    return result
