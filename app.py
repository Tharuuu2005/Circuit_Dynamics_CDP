#!/usr/bin/env python3
"""
SDR Link 7: file transfer between two PCs over two ANTSDR E200 radios (BPSK, FDD, encrypted),
with messages and 4 in a Row. Start it with "SDR Link.exe" (or this file); the first start
asks for the node number (1 or 2) and pairs the two PCs.

    python app.py                    # normal start (the radio starts by itself)
    python app.py --node 2           # force the node number
    python app.py --same-pc --no-radio --data data_n2 --node 2   # both on one PC (tools/fake_radio.py)

Keys: F1 all shortcuts, Ctrl+1..4 pages, F11 full screen, Ctrl+Shift+D the hidden radio-link panel
"""
import argparse
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_DATA = os.path.join(HERE, "data")
os.makedirs(_DATA, exist_ok=True)
if sys.stdout is None or sys.stderr is None:      # pythonw (SDR Link.exe): keep a log file instead
    _log = open(os.path.join(_DATA, "app.log"), "a", buffering=1, encoding="utf-8")
    _log.write(time.strftime("\n==== %Y-%m-%d %H:%M:%S start\n"))
    sys.stdout = sys.stderr = _log


def _excepthook(t, v, tb):
    msg = "".join(traceback.format_exception(t, v, tb))
    try:
        with open(os.path.join(_DATA, "error.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S\n") + msg + "\n")
    except OSError:
        pass
    sys.__excepthook__(t, v, tb)


sys.excepthook = _excepthook
try:
    from PyQt5 import QtCore, QtGui, QtWidgets  # noqa: E402
except ImportError:
    _excepthook(*sys.exc_info())
    sys.exit(3)

import config  # noqa: E402
import core  # noqa: E402
import files  # noqa: E402
import secure  # noqa: E402
from ui import theme  # noqa: E402
from ui.common_pages import ChatPage, GamePage, SettingsPage  # noqa: E402
from ui.files_page import FilesPage  # noqa: E402
from ui.icons import app_icon, icon  # noqa: E402
from ui.setup import SetupDialog  # noqa: E402
from ui.telecom import TelecomPanel  # noqa: E402
from ui.widgets import Panel, label  # noqa: E402

Qt = QtCore.Qt
SHORTCUTS = [("Ctrl+O", "Send files"), ("Ctrl+Shift+O", "Send a folder"), ("Drag and drop", "Send files or folders"),
             ("Enter", "Send a message"), ("Ctrl+Shift+A", "Attach a photo or file to a message"),
             ("Ctrl+N", "New game"), ("1 … 7", "Drop a disc"), ("Ctrl+1 … Ctrl+4", "Switch pages"),
             ("F11", "Full screen"), ("F1", "This list"), ("Ctrl+Q", "Quit")]


class Bridge(QtCore.QObject):
    event = QtCore.pyqtSignal(object)


class HelpOverlay(QtWidgets.QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("HelpOverlay { background: rgba(4, 10, 16, 150); }")
        outer = QtWidgets.QVBoxLayout(self)
        box = Panel()
        box.setFixedWidth(600)
        outer.addWidget(box, 0, Qt.AlignCenter)
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(28, 24, 28, 24)
        v.addWidget(label("Keyboard shortcuts", 24, QtGui.QFont.DemiBold, display=True))
        g = QtWidgets.QGridLayout()
        g.setVerticalSpacing(8)
        g.setHorizontalSpacing(22)
        for i, (k, t) in enumerate(SHORTCUTS):
            g.addWidget(label(k, 15, QtGui.QFont.DemiBold, display=True), i, 0)
            g.addWidget(label(t, 15, muted=True), i, 1)
        v.addLayout(g)
        v.addWidget(label("Esc closes", 13, muted=True), 0, Qt.AlignRight)
        self.hide()

    def toggle(self):
        if self.isVisible():
            self.hide()
            self.parent().setFocus()
        else:
            self.setGeometry(self.parent().rect())
            self.show()
            self.raise_()
            self.setFocus()

    def keyPressEvent(self, e):
        self.hide()
        self.parent().setFocus()

    def mousePressEvent(self, e):
        self.hide()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, node, key_path):
        super().__init__()
        self.node = node
        self.c = theme.PALETTES["app"]
        self.setWindowTitle(f"SDR Link, node {node.node}")
        self.setWindowIcon(QtGui.QIcon(app_icon()))
        self.setStyleSheet(theme.qss("app"))
        self.setMinimumSize(1100, 700)
        self.bridge = Bridge()
        self.bridge.event.connect(self._event)
        recv = node.s.recv_dir or os.path.join(node.data_dir, "received")
        self.fm = files.FileManager(node, recv)
        self.files = FilesPage(self.fm, node, self.c)
        self.chat = ChatPage(node, self.fm, self.c)
        self.game = GamePage(node, self.c)
        self.settings = SettingsPage(node, self.fm, self.c, key_path)
        self.settings.restart_needed.connect(lambda t: QtWidgets.QMessageBox.information(self, "SDR Link", t))
        self.chat.new_message.connect(lambda: self._mark_unread(self.chat))
        pages = [("Files", "file", self.files), ("Messages", "chat", self.chat),
                 ("4 in a Row", "game", self.game), ("Settings", "settings", self.settings)]

        central = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        rail = QtWidgets.QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(196)
        rv = QtWidgets.QVBoxLayout(rail)
        rv.setContentsMargins(14, 20, 14, 18)
        rv.setSpacing(4)
        mark = QtWidgets.QHBoxLayout()
        lg = QtWidgets.QLabel()
        lg.setPixmap(app_icon(64).scaled(34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        mark.addWidget(lg)
        mark.addWidget(label("SDR Link", 19, QtGui.QFont.DemiBold, display=True))
        mark.addStretch(1)
        rv.addLayout(mark)
        rv.addSpacing(22)
        self.stack = QtWidgets.QStackedWidget()
        self.nav = []
        group = QtWidgets.QButtonGroup(self)
        for i, (name, ic, page) in enumerate(pages):
            btn = QtWidgets.QPushButton(f"  {name}")
            btn.setProperty("kind", "rail")
            btn.setCheckable(True)
            btn.setIcon(icon(ic, self.c["muted"], 22))
            btn.setIconSize(QtCore.QSize(22, 22))
            btn.setMinimumHeight(46)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setToolTip(f"Ctrl+{i + 1}")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, k=i: self.show_page(k))
            group.addButton(btn)
            rv.addWidget(btn)
            page.setFocusPolicy(Qt.StrongFocus)
            self.stack.addWidget(page)
            self.nav.append((btn, name, page))
            QtWidgets.QShortcut(QtGui.QKeySequence(f"Ctrl+{i + 1}"), self, lambda k=i: self.show_page(k))
        rv.addStretch(1)
        self.me_lbl = label(f"This PC: node {node.node}", 13, muted=True)
        rv.addWidget(self.me_lbl)
        self.link_lbl = label("", 13, muted=True, wrap=True)
        rv.addWidget(self.link_lbl)
        h.addWidget(rail)
        h.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.unread = set()
        self.telecom = TelecomPanel(node, self.fm, self.c, central)
        self.help = HelpOverlay(central)
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+Shift+D"), self, self.telecom.toggle)
        QtWidgets.QShortcut(QtGui.QKeySequence("F1"), self, self.help.toggle)
        QtWidgets.QShortcut(QtGui.QKeySequence("F11"), self, self.toggle_full)
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+Q"), self, self.close)
        self.show_page(0)
        tm = QtCore.QTimer(self)
        tm.timeout.connect(self._tick)
        tm.start(1000)
        node.on_event = self.bridge.event.emit
        node.start()

    # ---- navigation
    def show_page(self, k):
        btn, name, page = self.nav[k]
        btn.setChecked(True)
        self.stack.setCurrentIndex(k)
        self.unread.discard(page)
        self._nav_labels()
        if page is self.chat:
            self.chat.focus_input()
        else:
            page.setFocus()

    def _nav_labels(self):
        for btn, name, page in self.nav:
            btn.setText(f"  {name}" + ("   ●" if page in self.unread else ""))
            btn.setStyleSheet(f"color: {self.c['signal']};" if page in self.unread else "")

    def _mark_unread(self, page):
        if self.stack.currentWidget() is not page:
            self.unread.add(page)
            self._nav_labels()

    def toggle_full(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.telecom.isVisible():
            self.telecom.place()
        if self.help.isVisible():
            self.help.setGeometry(self.centralWidget().rect())

    # ---- events from the link (UI thread)
    def _event(self, e):
        if self.fm.on_event(e):
            return
        t = e["type"]
        if t == "msg":
            if e["kind"] == "chat":
                self.chat.on_msg(e["obj"])
                self._mark_unread(self.chat)
            elif e["kind"] == "game":
                self.game.on_msg(e["obj"])
                self._mark_unread(self.game)
        elif t == "sent":
            self.chat.on_sent(e["xfer"])
        elif t == "failed":
            self.chat.on_failed(e["xfer"], e["reason"])

    def _tick(self):
        st = self.node.tr.link_state
        self.link_lbl.setText({"up": "Connected", "one-way": "Connecting…", "down": "Reconnecting…"}[st])

    def closeEvent(self, e):
        busy = [it for it in self.fm.items.values() if it.active]
        if busy and QtWidgets.QMessageBox.question(
                self, "SDR Link", f"{len(busy)} file(s) are still on the way. Close anyway?") \
                != QtWidgets.QMessageBox.Yes:
            e.ignore()
            return
        self.node.stop()
        super().closeEvent(e)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", type=int, choices=(1, 2))
    ap.add_argument("--data", default=config.DATA_DIR, help="folder for key, settings, logs and received files")
    ap.add_argument("--same-pc", action="store_true", help="both nodes on one PC (tools/fake_radio.py)")
    ap.add_argument("--no-radio", action="store_true", help="don't start the radio (GRC flowgraph or fake radio)")
    ap.add_argument("--full-screen", action="store_true")
    a = ap.parse_args()
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QtWidgets.QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QtWidgets.QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("SDR Link")
    app.setWindowIcon(QtGui.QIcon(app_icon()))
    app.setFont(theme.font(15))
    os.makedirs(a.data, exist_ok=True)
    settings = core.Settings.load(a.data)
    key_path = os.path.join(a.data, "pairing.key")
    try:
        pairing = secure.load_pairing(key_path)
    except secure.PairingError:
        pairing = None
    node = a.node or settings.node
    if pairing is None or node is None:
        dlg = SetupDialog(key_path, node)
        if dlg.exec_() != QtWidgets.QDialog.Accepted or dlg.pairing is None:
            return 0
        pairing, node = dlg.pairing, dlg.role
    if settings.node != node:
        settings.node = node
        settings.save()
    ports = config.SAME_PC_PORTS[node] if a.same_pc else None
    n = core.Node(node, pairing, settings, data_dir=a.data, ports=ports, start_radio=not a.no_radio)
    w = MainWindow(n, key_path)
    if a.full_screen:
        w.showFullScreen()
    else:
        w.showMaximized()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
