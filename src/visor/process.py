from __future__ import annotations

import os
import subprocess


def hidden_options() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def run(args: list[str], *, cwd: str | None = None, timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=False, **hidden_options())

