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
    if mode not in ("Grid", "Columns", "Rows", "Cascade"):
        raise ValueError("Unknown arrangement")
    if mode == "Cascade":
        offset = min(28, max(0, min(area.width - 320, area.height - 200) // max(1, count)))
        w, h = area.width - offset * (count - 1), area.height - offset * (count - 1)
        return [Rect(area.x + i * offset, area.y + i * offset, w, h) for i in range(count)]
    cols = count if mode == "Columns" else 1 if mode == "Rows" else math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)
    return [Rect(area.x + (i % cols) * area.width // cols,
                 area.y + (i // cols) * area.height // rows,
                 area.width // cols, area.height // rows) for i in range(count)]

