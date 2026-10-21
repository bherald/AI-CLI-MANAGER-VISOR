from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def clamped(self, screen: Rect) -> Rect:
        w = min(self.width, screen.width)
        h = min(self.height, screen.height)
        return Rect(max(screen.x, min(self.x, screen.x + screen.width - w)),
                    max(screen.y, min(self.y, screen.y + screen.height - h)), w, h)


def arrange(count: int, area: Rect, mode="Grid") -> list[Rect]:
    if count <= 0:
        return []
    if mode not in ("Grid", "Columns", "Rows", "Stacked", "Cascade"):
        raise ValueError("Unknown arrangement")
    if mode == "Cascade":
        # Classic cascade: leave a useful portion of earlier windows visible,
        # keep each title bar exposed, and wrap when the monitor is filled.
        offset = min(28, max(0, min(area.width - 1, area.height - 1)))
        w, h = max(1, area.width * 3 // 4), max(1, area.height * 3 // 4)
        slots = max(1, min((area.width - w) // max(1, offset),
                           (area.height - h) // max(1, offset)) + 1)
        return [Rect(area.x + (i % slots) * offset, area.y + (i % slots) * offset, w, h)
                for i in range(count)]
    cols = count if mode == "Columns" else 1 if mode in ("Rows", "Stacked") else math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)
    return [Rect(area.x + (i % cols) * area.width // cols,
                 area.y + (i // cols) * area.height // rows,
                 (i % cols + 1) * area.width // cols - (i % cols) * area.width // cols,
                 (i // cols + 1) * area.height // rows - (i // cols) * area.height // rows) for i in range(count)]

