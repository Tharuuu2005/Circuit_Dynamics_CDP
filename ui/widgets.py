"""Building blocks: buttons, labels, panels, the status pill."""
from PyQt5 import QtCore, QtGui, QtWidgets

from . import theme
from .icons import icon

Qt = QtCore.Qt


def button(text, kind=None, icon_name=None, color=None, tip=None, h=None):
    b = QtWidgets.QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if icon_name:
        b.setIcon(icon(icon_name, color or "#000"))
        b.setIconSize(QtCore.QSize(20, 20))
    if tip:
        b.setToolTip(tip)
    if h:
        b.setMinimumHeight(h)
    b.setCursor(Qt.PointingHandCursor)
    return b


def label(text="", size=15, weight=QtGui.QFont.Normal, display=False, muted=False, wrap=False):
    lb = QtWidgets.QLabel(text)
    lb.setFont(theme.font(size, weight, display))
    if muted:
        lb.setProperty("muted", True)
    lb.setWordWrap(wrap)
    return lb


class Panel(QtWidgets.QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")


# ====================================================================== status pill
class StatusPill(QtWidgets.QWidget):
    """a coloured dot and a short text: 'Desk online', 'Reconnecting…'."""

    def __init__(self, palette, parent=None):
        super().__init__(parent)
        self.c = palette
        self.text, self.level = "", "muted"
        self.setMinimumHeight(30)

    def set(self, text, level):
        if (text, level) != (self.text, self.level):
            self.text, self.level = text, level
            f = theme.font(14, QtGui.QFont.DemiBold)
            self.setMinimumWidth(QtGui.QFontMetrics(f).horizontalAdvance(text) + 46)
            self.update()

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        col = QtGui.QColor({"ok": self.c["open"], "bad": self.c["alarm"], "live": self.c["signal"]}
                           .get(self.level, self.c["faint"]))
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QtGui.QPen(QtGui.QColor(self.c["line"]), 1))
        p.setBrush(QtGui.QColor(self.c["panel"]))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(QtCore.QPointF(17, r.center().y()), 4.5, 4.5)
        p.setPen(QtGui.QColor(self.c["text"]))
        p.setFont(theme.font(14, QtGui.QFont.DemiBold))
        p.drawText(r.adjusted(30, 0, -12, 0), Qt.AlignVCenter, self.text)
        p.end()
