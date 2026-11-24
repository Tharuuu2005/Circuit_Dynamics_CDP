"""Line icons drawn with QPainter on a 24 x 24 grid (all original)."""
from PyQt5 import QtCore, QtGui

P = QtCore.QPointF
R = QtCore.QRectF


def _draw(p, name):
    if False:
        pass
    elif name == "log":           # list with clock
        for y in (7, 12, 17):
            p.drawLine(P(9, y), P(20, y))
            p.drawEllipse(P(5, y), 1, 1)
    elif name == "people":
        p.drawEllipse(P(9, 8), 3.2, 3.2)
        p.drawArc(R(3, 13, 12, 10), 0, 180 * 16)
        p.drawEllipse(P(17, 9), 2.4, 2.4)
        p.drawArc(R(14, 14, 8, 8), 0, 180 * 16)
    elif name == "chat":
        path = QtGui.QPainterPath()
        path.addRoundedRect(R(3, 4, 18, 13), 4, 4)
        p.drawPath(path)
        p.drawLine(P(8, 17), P(7, 21))
        p.drawLine(P(7, 21), P(12, 17))
    elif name == "game":          # 4 discs in a row on a grid
        p.drawRoundedRect(R(3, 5, 18, 14), 2, 2)
        for x in (7, 12, 17):
            p.drawEllipse(P(x, 12), 2, 2)
    elif name == "settings":      # sliders
        for y, x in ((7, 9), (12, 15), (17, 7)):
            p.drawLine(P(4, y), P(20, y))
            p.save()
            p.setBrush(p.pen().color())
            p.drawEllipse(P(x, y), 2, 2)
            p.restore()
    elif name == "mic":
        p.drawRoundedRect(R(9, 3, 6, 11), 3, 3)
        p.drawArc(R(6, 7, 12, 10), 180 * 16, 180 * 16)
        p.drawLine(P(12, 17), P(12, 21))
    elif name == "camera":
        p.drawRoundedRect(R(3, 7, 13, 10), 2, 2)
        path = QtGui.QPainterPath(P(16, 10))
        path.lineTo(P(21, 7.5))
        path.lineTo(P(21, 16.5))
        path.lineTo(P(16, 14))
        p.drawPath(path)
    elif name == "eye":
        path = QtGui.QPainterPath(P(2.5, 12))
        path.quadTo(P(12, 3), P(21.5, 12))
        path.quadTo(P(12, 21), P(2.5, 12))
        p.drawPath(path)
        p.drawEllipse(P(12, 12), 3, 3)
    elif name == "bell":
        path = QtGui.QPainterPath(P(5, 17))
        path.cubicTo(P(7, 14), P(6, 5), P(12, 5))
        path.cubicTo(P(18, 5), P(17, 14), P(19, 17))
        path.closeSubpath()
        p.drawPath(path)
        p.drawArc(R(10, 17, 4, 4), 180 * 16, 180 * 16)
    elif name == "open":          # open padlock
        p.drawRoundedRect(R(5, 11, 14, 10), 2, 2)
        p.drawArc(R(8, 3, 8, 10), 0, 180 * 16)
        p.drawLine(P(16, 8), P(16, 6))
    elif name == "lock":
        p.drawRoundedRect(R(5, 11, 14, 10), 2, 2)
        p.drawArc(R(8, 4, 8, 10), 0, 180 * 16)
        p.drawLine(P(8, 9), P(8, 11))
        p.drawLine(P(16, 9), P(16, 11))
    elif name == "x":
        p.drawLine(P(6, 6), P(18, 18))
        p.drawLine(P(18, 6), P(6, 18))
    elif name == "check":
        p.drawLine(P(5, 12.5), P(10, 17.5))
        p.drawLine(P(10, 17.5), P(19, 7))
    elif name == "send":
        path = QtGui.QPainterPath(P(3, 11))
        path.lineTo(P(21, 4))
        path.lineTo(P(14, 21))
        path.lineTo(P(11, 13))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "attach":
        path = QtGui.QPainterPath(P(16, 7))
        path.lineTo(P(9, 14))
        path.cubicTo(P(7, 16), P(9, 18.5), P(11, 16.5))
        path.lineTo(P(18, 9.5))
        path.cubicTo(P(21, 6.5), P(17, 2.5), P(14, 5.5))
        path.lineTo(P(6.5, 13))
        path.cubicTo(P(2.5, 17), P(8, 22.5), P(12, 18.5))
        path.lineTo(P(18, 12.5))
        p.drawPath(path)
    elif name == "message":
        p.drawRoundedRect(R(3, 5, 18, 12), 3, 3)
        p.drawLine(P(7, 9.5), P(17, 9.5))
        p.drawLine(P(7, 13), P(13, 13))
    elif name == "phone_end":
        path = QtGui.QPainterPath(P(3, 14))
        path.cubicTo(P(8, 8), P(16, 8), P(21, 14))
        path.lineTo(P(18, 16))
        path.lineTo(P(15, 13))
        path.lineTo(P(9, 13))
        path.lineTo(P(6, 16))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "upload":        # arrow up out of a tray
        p.drawLine(P(12, 15), P(12, 3.5))
        p.drawLine(P(7.5, 8), P(12, 3.5))
        p.drawLine(P(16.5, 8), P(12, 3.5))
        path = QtGui.QPainterPath(P(4, 13))
        path.lineTo(P(4, 20))
        path.lineTo(P(20, 20))
        path.lineTo(P(20, 13))
        p.drawPath(path)
    elif name == "file":
        path = QtGui.QPainterPath(P(6, 3))
        path.lineTo(P(14, 3))
        path.lineTo(P(19, 8))
        path.lineTo(P(19, 21))
        path.lineTo(P(6, 21))
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(P(14, 3), P(14, 8))
        p.drawLine(P(14, 8), P(19, 8))
    elif name == "image":
        p.drawRoundedRect(R(3, 5, 18, 14), 2, 2)
        p.drawEllipse(P(8.5, 9.5), 1.6, 1.6)
        path = QtGui.QPainterPath(P(3.5, 17))
        path.lineTo(P(9, 12.5))
        path.lineTo(P(13, 16))
        path.lineTo(P(16, 13))
        path.lineTo(P(20.5, 17))
        p.drawPath(path)
    elif name == "audio":         # a note
        p.drawLine(P(9, 17), P(9, 5))
        p.drawLine(P(9, 5), P(19, 3))
        p.drawLine(P(19, 3), P(19, 15))
        p.drawEllipse(P(7, 17.5), 2.4, 2)
        p.drawEllipse(P(17, 15.5), 2.4, 2)
    elif name == "pulse":         # link activity (hidden panel)
        path = QtGui.QPainterPath(P(2.5, 12))
        for x, y in ((7, 12), (9.5, 5), (13, 19), (15.5, 12), (21.5, 12)):
            path.lineTo(P(x, y))
        p.drawPath(path)
    elif name == "phone":
        path = QtGui.QPainterPath(P(6, 3))
        path.lineTo(P(9.5, 3.5))
        path.lineTo(P(11, 8))
        path.lineTo(P(8.5, 10))
        path.cubicTo(P(10, 13), P(11, 14), P(14, 15.5))
        path.lineTo(P(16, 13))
        path.lineTo(P(20.5, 14.5))
        path.lineTo(P(21, 18))
        path.cubicTo(P(14, 22), P(2, 10), P(6, 3))
        p.drawPath(path)


def icon(name, color, size=22, width=1.8):
    pm = QtGui.QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.scale(size / 24, size / 24)
    pen = QtGui.QPen(QtGui.QColor(color), width)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.NoBrush)
    _draw(p, name)
    p.end()
    return QtGui.QIcon(pm)


def app_icon(size=256):
    """the app's mark: a door frame with two radio arcs (original)."""
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    s = size / 24
    p.scale(s, s)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor("#0E1A24"))
    p.drawRoundedRect(R(0.5, 0.5, 23, 23), 5.5, 5.5)
    pen = QtGui.QPen(QtGui.QColor("#E6EEF3"), 1.9)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.NoBrush)
    p.drawRoundedRect(R(5, 7, 8.5, 12.5), 1.2, 1.2)
    pen = QtGui.QPen(QtGui.QColor("#FFB547"), 1.9)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    p.setPen(pen)
    p.drawArc(R(12.5, 4.5, 7, 7), -60 * 16, 120 * 16)
    p.drawArc(R(11, 2.5, 11, 11), -60 * 16, 120 * 16)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor("#3DD68C"))
    p.drawEllipse(P(11.3, 13.5), 1.1, 1.1)
    p.end()
    return pm
