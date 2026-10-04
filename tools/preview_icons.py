"""Render every shared SVG at toolbar size and 2x for visual review."""
import os
from pathlib import Path
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication
from ui.quick.icons import IconProvider

app = QApplication([])
QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / "segoeui.ttf"))
provider = IconProvider()
paths = sorted(provider.root.glob("*.svg"))
columns = 6
sheet = QImage(columns * 170, ((len(paths) + columns - 1) // columns) * 110, QImage.Format_ARGB32)
sheet.fill(QColor("#181818"))
painter = QPainter(sheet)
painter.setFont(QFont("Segoe UI", 10))
for index, path in enumerate(paths):
    x, y = (index % columns) * 170, (index // columns) * 110
    painter.setPen(QColor("#a0a0a0"))
    painter.drawText(QRectF(x + 14, y + 74, 155, 26), path.stem)
    for size, offset, color in ((20, 18, "f0f2f6"), (40, 74, "ffd21e")):
        image = provider.requestImage(f"{path.stem}/{color}", None, QSize(size, size))
        if image.isNull():
            raise RuntimeError(f"Invalid SVG: {path}")
        painter.drawImage(x + offset, y + 20, image)
painter.end()
target = Path(sys.argv[1] if len(sys.argv) > 1 else "test-results/icons.png")
target.parent.mkdir(parents=True, exist_ok=True)
assert sheet.save(str(target))
print(f"Rendered {len(paths)} icons: {target}")
