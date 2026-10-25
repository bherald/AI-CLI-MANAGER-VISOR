"""Create isolated activity examples and a procedural sample picture; no downloads."""
import argparse
import json
import shutil
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen, QPolygon

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("destination", type=Path)
args = parser.parse_args()
destination = args.destination.resolve()
destination.mkdir(parents=True, exist_ok=True, mode=0o700)
for name in ("learning-board.json", "picture-topic.json", "shared-chalkboard.json", "sample-picture.png"):
    if (destination / name).exists():
        raise SystemExit("Refusing to overwrite an existing activity example: " + name)
for name in ("learning-board.json", "picture-topic.json", "shared-chalkboard.json"):
    shutil.copyfile(Path(__file__).resolve().parents[1] / "examples/activity" / name, destination / name)
image = QImage(960, 540, QImage.Format.Format_RGB32)
painter = QPainter(image)
painter.setRenderHint(QPainter.RenderHint.Antialiasing)
sky = QLinearGradient(0, 0, 0, 540)
sky.setColorAt(0, QColor("#bee4eb"))
sky.setColorAt(1, QColor("#f5f1dc"))
painter.fillRect(image.rect(), sky)
painter.setPen(Qt.PenStyle.NoPen)
painter.setBrush(QColor("#f5cd67"))
painter.drawEllipse(QRect(690, 55, 110, 110))
painter.setBrush(QColor("#9cbcb2"))
painter.drawPolygon(QPolygon([QPoint(0, 355), QPoint(220, 150), QPoint(450, 370)]))
painter.setBrush(QColor("#6d9e9a"))
painter.drawPolygon(QPolygon([QPoint(260, 375), QPoint(560, 125), QPoint(900, 380)]))
painter.setBrush(QColor("#75a17d"))
painter.drawEllipse(QRect(-150, 340, 1350, 400))
painter.setBrush(QColor("#f4ebd7"))
painter.drawRoundedRect(QRect(345, 280, 260, 175), 5, 5)
painter.setBrush(QColor("#38586c"))
painter.drawPolygon(QPolygon([QPoint(315, 292), QPoint(470, 190), QPoint(635, 292)]))
painter.setPen(QPen(QColor("#b1d4db"), 3))
for x in (375, 435, 495):
    painter.drawLine(x, 267, x + 30, 235)
for y in (242, 258):
    painter.drawLine(377, y, 549, y)
painter.setPen(Qt.PenStyle.NoPen)
painter.setBrush(QColor("#b5d8dc"))
painter.drawRoundedRect(QRect(380, 325, 65, 55), 3, 3)
painter.drawRoundedRect(QRect(510, 325, 60, 55), 3, 3)
painter.setBrush(QColor("#aa8260"))
painter.drawRect(QRect(461, 360, 34, 95))
painter.end()
if not image.save(str(destination / "sample-picture.png"), "PNG"):
    raise SystemExit("Could not write sample picture")
print(json.dumps({"documents": [str(destination / name) for name in ("learning-board.json", "picture-topic.json", "shared-chalkboard.json")],
                  "sample_picture": str(destination / "sample-picture.png")}))
