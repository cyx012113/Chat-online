"""Small code-native application assets."""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap


def app_icon() -> QIcon:
    """Return a crisp application icon without external file dependencies."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#2563EB"))
    painter.drawRoundedRect(QRectF(5, 7, 54, 43), 11, 11)
    painter.setBrush(QColor("#16803C"))
    painter.drawRoundedRect(QRectF(31, 30, 27, 24), 8, 8)
    painter.setBrush(QColor("#FFFFFF"))
    painter.drawRoundedRect(QRectF(15, 20, 25, 4), 2, 2)
    painter.drawRoundedRect(QRectF(15, 29, 18, 4), 2, 2)
    painter.end()
    return QIcon(pixmap)

