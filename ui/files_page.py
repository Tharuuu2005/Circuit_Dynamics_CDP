"""Files: the main page. Drop files or folders, follow them on the way, find them afterwards."""
import os
import subprocess
import sys
import time

from PyQt5 import QtCore, QtGui, QtWidgets

import media
from . import theme
from .icons import icon
from .widgets import Panel, StatusPill, button, label

Qt = QtCore.Qt


def open_path(path):
    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))


def show_in_folder(path):
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_path(os.path.dirname(path))


def _left(seconds):
    if seconds is None:
        return ""
    if seconds < 60:
        return f"about {max(1, int(seconds))} s left"
    if seconds < 3600:
        return f"about {int(seconds // 60)} min {int(seconds % 60)} s left"
    return f"about {seconds / 3600:.1f} h left"


class DropZone(QtWidgets.QFrame):
    dropped = QtCore.pyqtSignal(list)

    def __init__(self, palette, parent=None):
        super().__init__(parent)
        self.c = palette
        self.hot = False
        self.setAcceptDrops(True)
        self.setMinimumHeight(150)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(32, 22, 32, 22)
        lay.setSpacing(22)
        ic = QtWidgets.QLabel()
        ic.setPixmap(icon("upload", palette["action"], 52, 1.6).pixmap(52, 52))
        lay.addWidget(ic)
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(4)
        col.addStretch(1)
        col.addWidget(label("Drop files or folders here", 24, QtGui.QFont.DemiBold, display=True))
        self.sub = label("They go to the other PC over the radio link, encrypted, and are checked on arrival.",
                         14, muted=True, wrap=True)
        col.addWidget(self.sub)
        col.addStretch(1)
        lay.addLayout(col, 1)
        self.b_files = button("Send files…", "primary", tip="Ctrl+O", h=46)
        self.b_folder = button("Send a folder…", tip="Ctrl+Shift+O", h=46)
        lay.addWidget(self.b_files)
        lay.addWidget(self.b_folder)

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        p.setBrush(QtGui.QColor(self.c["raised"] if self.hot else self.c["panel"]))
        pen = QtGui.QPen(QtGui.QColor(self.c["action"] if self.hot else self.c["line"]), 2 if self.hot else 1.5)
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(r, 14, 14)
        p.end()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.hot = True
            self.update()

    def dragLeaveEvent(self, e):
        self.hot = False
        self.update()

    def dropEvent(self, e):
        self.hot = False
        self.update()
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.dropped.emit(paths)


class Row(QtWidgets.QFrame):
    """one file: name, size, direction, progress / result, actions."""

    def __init__(self, fm, palette, parent=None):
        super().__init__(parent)
        self.fm, self.c = fm, palette
        self.rec = None
        self.setObjectName("row")
        self.setStyleSheet(f"QFrame#row {{ background: {palette['panel']}; border: 1px solid {palette['line']};"
                           f" border-radius: 12px; }}")
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(16, 12, 14, 12)
        h.setSpacing(14)
        self.ic = QtWidgets.QLabel()
        self.ic.setFixedSize(44, 44)
        self.ic.setAlignment(Qt.AlignCenter)
        h.addWidget(self.ic)
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(3)
        self.name = label("", 16, QtGui.QFont.DemiBold)
        self.name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info = label("", 13, muted=True)
        col.addWidget(self.name)
        col.addWidget(self.info)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setStyleSheet(f"QProgressBar {{ background: {palette['line']}; border: none; border-radius: 3px; }}"
                               f"QProgressBar::chunk {{ background: {palette['action']}; border-radius: 3px; }}")
        col.addWidget(self.bar)
        h.addLayout(col, 1)
        self.state = label("", 14)
        self.state.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.state.setMinimumWidth(190)
        h.addWidget(self.state)
        self.b1 = button("")
        self.b2 = button("")
        for b in (self.b1, self.b2):
            b.setFocusPolicy(Qt.NoFocus)
            h.addWidget(b)
        self.b1.clicked.connect(lambda: self._act(1))
        self.b2.clicked.connect(lambda: self._act(2))
        self._eta = None
        self._hist = []

    def _act(self, k):
        r = self.rec
        if r is None:
            return
        live = self.fm.items.get(r["fid"])
        if live is not None and live.active:
            self.fm.cancel(r["fid"])
        elif r["status"] == "done" and r.get("path"):
            (open_path if k == 1 else show_in_folder)(r["path"])
        elif r["dir"] == "out" and r.get("path"):
            self.fm.retry(r["fid"])

    def set(self, rec, item=None):
        self.rec = rec
        c = self.c
        kind = rec.get("kind", "file")
        ic = {"image": "image", "audio": "audio"}.get(kind, "file")
        arrow = "up" if rec["dir"] == "out" else "down"
        self.ic.setPixmap(icon(ic, c["muted"], 30).pixmap(30, 30))
        self.ic.setStyleSheet(f"background: {c['raised']}; border-radius: 10px;")
        self.name.setText(rec["name"])
        peer = "To" if rec["dir"] == "out" else "From"
        size = media.human(rec["size"]) if rec["size"] else ("0 B" if rec["status"] != "preparing" else "")
        when = time.strftime("%H:%M", time.localtime(rec.get("end") or rec.get("start") or time.time()))
        if item is not None and item.active:
            p = item.progress
            self.bar.show()
            self.bar.setValue(int(1000 * p))
            now = time.monotonic()
            self._hist.append((now, item.bytes_done))
            self._hist = [x for x in self._hist if now - x[0] < 8]
            eta = None
            if len(self._hist) >= 2 and self._hist[-1][1] > self._hist[0][1]:
                r_ = (self._hist[-1][1] - self._hist[0][1]) / (self._hist[-1][0] - self._hist[0][0])
                eta = (item.size - item.bytes_done) / r_ if r_ > 0 else None
            st = {"preparing": "Preparing…", "waiting": "Waiting for the link",
                  "sending": f"{100 * p:.0f} %", "receiving": f"{100 * p:.0f} %",
                  "verifying": "Checking…"}.get(item.status, item.status)
            self.info.setText(f"{peer} the other PC   {size}   {_left(eta) if item.status in ('sending', 'receiving') else ''}")
            if self.fm.node.tr.link_state != "up" and item.status in ("sending", "receiving", "waiting"):
                st = "Paused: link down"
            self.state.setText(st)
            self.state.setStyleSheet(f"color: {c['text']};")
            self.b1.setText("Cancel")
            self.b1.setProperty("kind", "danger")
            self.b1.show()
            self.b2.hide()
        else:
            self.bar.hide()
            s = rec["status"]
            if s == "done":
                st, col = ("Delivered, verified" if rec["dir"] == "out" else "Received, verified"), c["open"]
            elif s == "cancelled":
                st, col = "Cancelled", c["muted"]
            else:
                st, col = "Not completed", c["alarm"]
            self.info.setText(f"{peer} the other PC   {size}   {when}" +
                              (f"   {rec['error']}" if rec.get("error") and s != "done" else ""))
            self.state.setText(st)
            self.state.setStyleSheet(f"color: {col};")
            self.b1.setProperty("kind", None)
            if s == "done" and rec.get("path") and os.path.exists(rec["path"]):
                self.b1.setText("Open")
                self.b2.setText("Show in folder")
                self.b1.show()
                self.b2.show()
            elif rec["dir"] == "out" and s != "done" and rec.get("path") and os.path.exists(rec["path"]):
                self.b1.setText("Send again")
                self.b1.show()
                self.b2.hide()
            else:
                self.b1.hide()
                self.b2.hide()
        self.b1.style().unpolish(self.b1)
        self.b1.style().polish(self.b1)


class FilesPage(QtWidgets.QWidget):
    def __init__(self, fm, node, palette, parent=None):
        super().__init__(parent)
        self.fm, self.node, self.c = fm, node, palette
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(label("Files", 30, QtGui.QFont.DemiBold, display=True))
        top.addStretch(1)
        self.pill = StatusPill(palette)
        top.addWidget(self.pill)
        root.addLayout(top)

        self.help = Panel()
        hv = QtWidgets.QVBoxLayout(self.help)
        hv.setContentsMargins(20, 14, 20, 14)
        self.help_t = label("", 17, QtGui.QFont.DemiBold)
        self.help_s = label("", 14, muted=True, wrap=True)
        hv.addWidget(self.help_t)
        hv.addWidget(self.help_s)
        self.help.hide()
        root.addWidget(self.help)

        self.drop = DropZone(palette)
        self.drop.dropped.connect(self.send)
        self.drop.b_files.clicked.connect(self.pick_files)
        self.drop.b_folder.clicked.connect(self.pick_folder)
        root.addWidget(self.drop)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        self.col = QtWidgets.QVBoxLayout(inner)
        self.col.setContentsMargins(0, 0, 8, 0)
        self.col.setSpacing(8)
        self.h_active = label("On the way", 16, QtGui.QFont.DemiBold)
        self.col.addWidget(self.h_active)
        self.active_box = QtWidgets.QVBoxLayout()
        self.active_box.setSpacing(8)
        self.col.addLayout(self.active_box)
        self.col.addSpacing(10)
        self.h_hist = label("Earlier", 16, QtGui.QFont.DemiBold)
        self.col.addWidget(self.h_hist)
        self.hist_box = QtWidgets.QVBoxLayout()
        self.hist_box.setSpacing(8)
        self.col.addLayout(self.hist_box)
        self.empty = label("Nothing sent or received yet.", 15, muted=True)
        self.col.addWidget(self.empty)
        self.col.addStretch(1)
        self.scroll.setWidget(inner)
        root.addWidget(self.scroll, 1)
        self.setAcceptDrops(True)
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+O"), self, self.pick_files)
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+Shift+O"), self, self.pick_folder)
        self.rows = {}
        self.t_down = None
        tm = QtCore.QTimer(self)
        tm.timeout.connect(self.refresh)
        tm.start(400)
        self.refresh()

    # drops anywhere on the page
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.send([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def pick_files(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Send files")
        self.send(paths)

    def pick_folder(self):
        p = QtWidgets.QFileDialog.getExistingDirectory(self, "Send a folder")
        if p:
            self.send([p])

    def send(self, paths):
        if paths:
            self.fm.send_paths(paths)
            self.refresh()

    def refresh(self):
        st = self.node.tr.link_state
        self.pill.set("Connected to the other PC" if st == "up" else "Reconnecting…", "ok" if st == "up" else "bad")
        now = time.monotonic()
        if st == "up":
            self.t_down = None
        elif self.t_down is None:
            self.t_down = now
        why = self.node.diagnosis() if self.t_down and now - self.t_down > 8 else None
        if why:
            self.help_t.setText(why[0])
            self.help_s.setText(why[1])
            self.help.show()
        else:
            self.help.hide()
        fm = self.fm
        live = [fm.items[f] for f in fm.order if fm.items[f].active]
        hist = [r for r in reversed(fm.history)][:60]
        want = [("a", it.fid) for it in live] + [("h", r["fid"]) for r in hist]
        if [k for k in want] != getattr(self, "_keys", None):
            self._keys = want
            for box in (self.active_box, self.hist_box):
                while box.count():
                    w = box.takeAt(0).widget()
                    if w:
                        w.setParent(None)
            self.rows = {}
            for k in want:
                row = Row(fm, self.c)
                (self.active_box if k[0] == "a" else self.hist_box).addWidget(row)
                self.rows[k] = row
        for it in live:
            self.rows[("a", it.fid)].set(it.record(), it)
        for r in hist:
            self.rows[("h", r["fid"])].set(r)
        self.h_active.setVisible(bool(live))
        self.h_hist.setVisible(bool(hist))
        self.empty.setVisible(not live and not hist)
