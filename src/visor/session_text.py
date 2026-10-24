"""Bounded labels from existing provider metadata; no model-generated summaries."""
from pathlib import Path


def summary(value, limit=240):
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if text.startswith(("[VISOR message ", "<environment_context>", "<send_user_message", "/")):
        return ""
    text = "".join(c for c in text if c.isprintable())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def label(name, preview, cwd, native_id):
    generic = {native_id, native_id[:12], f"{Path(cwd).name} · {native_id[:8]}"}
    title = summary(name, 160)
    if title and title not in generic:
        return title
    return summary(preview, 96) or f"{Path(cwd).name or 'Saved session'} · {native_id[:8]}"
