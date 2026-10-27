"""Prism appearance changes that preserve widgets, drafts and operator intent."""
from __future__ import annotations

import re

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QMdiArea

THEMES = {"calm-dark": "Calm dark", "soft-light": "Soft light"}

_DARK = {
    "Window": "#17232d", "WindowText": "#d4dee7", "Base": "#14212b",
    "AlternateBase": "#243743", "Text": "#d4dee7", "Button": "#223440",
    "ButtonText": "#d4dee7", "Highlight": "#31595c", "HighlightedText": "#e0e9ed",
    "PlaceholderText": "#a5b5c3", "ToolTipBase": "#263d4b", "ToolTipText": "#e1eaf0",
    "Link": "#8bd7ce", "LinkVisited": "#c4addf",
}
_LIGHT = {
    "Window": "#e1e8e4", "WindowText": "#21333e", "Base": "#eff2ee",
    "AlternateBase": "#dfe6e2", "Text": "#21333e", "Button": "#d7e3dc",
    "ButtonText": "#21333e", "Highlight": "#a9cdbf", "HighlightedText": "#143c32",
    "PlaceholderText": "#4c625b", "ToolTipBase": "#dce7df", "ToolTipText": "#21333e",
    "Link": "#20685e", "LinkVisited": "#685189",
}
_LIGHT_COLORS = {
    "#17232d": "#e1e8e4", "#d4dee7": "#21333e", "#111b24": "#e6ebe7",
    "#85c4c5": "#316b66", "#a5b5c3": "#4c625b", "#465b69": "#93a79e",
    "#2a414e": "#e4ece5", "#223440": "#d7e3dc", "#2b4350": "#cbded3",
    "#739a9e": "#638a7c", "#31595c": "#a9cdbf", "#e0e9ed": "#143c32",
    "#729b9e": "#5b8f7f", "#a0aebb": "#61736b", "#202d38": "#e1e6e1",
    "#14212b": "#eff2ee", "#0e1820": "#d8e0da", "#243743": "#dfe6e2",
    # VISOR's inherited stylesheet may also be supplied as the base.
    "#111c24": "#e1e8e4", "#e1eaf0": "#21333e", "#48616f": "#93a79e",
    "#192a36": "#d7e3dc", "#425d6d": "#93a79e", "#233b47": "#cbded3",
    "#00cdd2": "#317f72", "#70818d": "#61736b", "#2a3c46": "#a9b8ae",
    "#00bfc7": "#a9cdbf", "#07181d": "#143c32", "#354b59": "#93a79e",
    "#101b23": "#eff2ee", "#14232d": "#dfe6e2", "#293e4b": "#bacac0",
    "#214653": "#a9cdbf", "#182b38": "#d8e3dc", "#b9d0df": "#304e43",
    "#2b424f": "#bacac0", "#536d7c": "#8baba0", "#263d4b": "#dce7df",
    "#5c7988": "#829d91",
}


def apply_theme(widget, name="calm-dark", base_style=""):
    """Apply a palette and stylesheet in place. Returns the canonical theme name.

    The caller should retain its original dark base stylesheet when switching;
    this helper never reads rendered card HTML or replaces widget data.
    """
    if name not in THEMES:
        raise ValueError("Unknown Prism theme")
    colors = _LIGHT if name == "soft-light" else _DARK
    palette = QPalette(widget.palette())
    for role, value in colors.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(value))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
                     QColor("#61736b" if name == "soft-light" else "#a0aebb"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
                     QColor("#61736b" if name == "soft-light" else "#a0aebb"))
    widget.setPalette(palette)
    if name == "soft-light":
        style = re.sub(r"#[0-9a-fA-F]{6}\b", lambda match: _LIGHT_COLORS.get(match[0].lower(), match[0]), base_style)
    else:
        style = base_style
    style += ("\nQTableWidget { background:" + colors["Base"] + "; alternate-background-color:"
              + colors["AlternateBase"] + "; color:" + colors["Text"] + "; gridline-color:"
              + ("#bacac0" if name == "soft-light" else "#2d424e") + "; }"
              + "\nQHeaderView::section { background:" + colors["Button"] + "; color:"
              + colors["ButtonText"] + "; padding:4px; border:0; }"
              + "\nQToolTip { background:" + colors["ToolTipBase"] + "; color:"
              + colors["ToolTipText"] + "; }")
    widget.setStyleSheet(style)
    widget.setProperty("prismTheme", name)
    for area in widget.findChildren(QMdiArea):
        area.setBackground(QColor("#d8e0da" if name == "soft-light" else "#0e1820"))
    widget.update()
    return name
