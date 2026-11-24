"""Pages both units have: messages, 4 in a Row, settings."""
import os
import time

from PyQt5 import QtCore, QtGui, QtWidgets

import connect4
import media
import secure
from . import theme
from .icons import icon
from .widgets import Panel, button, label

Qt = QtCore.Qt


# ====================================================================== messages
class Bubble(QtWidgets.QFrame):
    def __init__(self, palette, mine, text=None, pixmap=None, path=None, parent=None):
        super().__init__(parent)
        c = palette
        bg = c["action"] if mine else c["raised"]
        fg = c["action_text"] if mine else c["text"]
        self.setStyleSheet(f"QFrame {{ background: {bg}; border-radius: 14px; }} QLabel {{ color: {fg}; }}")
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 8)
        v.setSpacing(4)
        self.path = path
        if pixmap is not None:
            pic = QtWidgets.QLabel()
            pic.setPixmap(pixmap)
            pic.setCursor(Qt.PointingHandCursor)
            pic.mousePressEvent = lambda e: path and media.open_file(path)
            v.addWidget(pic)
        if text:
            t = QtWidgets.QLabel(text)
            t.setWordWrap(True)
            t.setTextInteractionFlags(Qt.TextSelectableByMouse)
            t.setFont(theme.font(15))
            t.setMaximumWidth(520)
            if path and pixmap is None:
                t.setCursor(Qt.PointingHandCursor)
                t.mousePressEvent = lambda e: media.open_file(path)
            v.addWidget(t)
        self.status = QtWidgets.QLabel(time.strftime("%H:%M"))
        self.status.setFont(theme.font(11))
        self.status.setStyleSheet(f"color: {fg}; opacity: 0.7;")
        self.status.setAlignment(Qt.AlignRight)
        v.addWidget(self.status)

    def set_status(self, text):
        self.status.setText(text)


class ChatPage(QtWidgets.QWidget):
    def __init__(self, node, fm, palette, parent=None):
        super().__init__(parent)
        self.node, self.fm, self.c = node, fm, palette
        self.pending = {}                   # xfer -> bubble (text)
        self.file_bubbles = {}              # fid -> bubble (photos / files from this page)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)
        root.addWidget(label("Messages", 30, QtGui.QFont.DemiBold, display=True))
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        self.col = QtWidgets.QVBoxLayout(inner)
        self.col.setContentsMargins(4, 4, 12, 4)
        self.col.setSpacing(10)
        self.col.addStretch(1)
        self.empty = label("No messages yet. Write below, or attach a photo or file.", 15, muted=True, wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.col.addWidget(self.empty)
        self.scroll.setWidget(inner)
        panel = Panel()
        pv = QtWidgets.QVBoxLayout(panel)
        pv.setContentsMargins(14, 14, 14, 14)
        pv.addWidget(self.scroll)
        root.addWidget(panel, 1)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        att = button("", None, "attach", palette["muted"], "Attach a photo or file (Ctrl+Shift+A)", 46)
        att.clicked.connect(self.attach)
        self.edit = QtWidgets.QLineEdit()
        self.edit.setPlaceholderText("Write a message")
        self.edit.setMinimumHeight(46)
        self.edit.returnPressed.connect(self.send_text)
        send = button("Send", "primary", "send", palette["action_text"], "Send (Enter)", 46)
        send.clicked.connect(self.send_text)
        row.addWidget(att)
        row.addWidget(self.edit, 1)
        row.addWidget(send)
        root.addLayout(row)
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+Shift+A"), self, self.attach)
        tm = QtCore.QTimer(self)
        tm.timeout.connect(self._files_refresh)
        tm.start(500)
        self._seen = set()

    def focus_input(self):
        self.edit.setFocus()

    def _add(self, mine, **kw):
        self.empty.hide()
        b = Bubble(self.c, mine, **kw)
        row = QtWidgets.QHBoxLayout()
        if mine:
            row.addStretch(1)
        row.addWidget(b)
        if not mine:
            row.addStretch(1)
        self.col.insertLayout(self.col.count() - 2, row)
        QtCore.QTimer.singleShot(30, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()))
        return b

    def send_text(self):
        text = self.edit.text().strip()
        if not text:
            return
        self.edit.clear()
        xid = self.node.send_msg("chat", {"text": text})
        self.pending[xid] = self._add(True, text=text)
        self.pending[xid].set_status("Sending…")

    def attach(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Attach photos or files")
        for p in paths:
            fid = self.fm.send_paths([p], chat=True)
            if fid:
                pm = self._pix(p) if media.detect_kind(p) == "image" else None
                b = self._add(True, text=None if pm else os.path.basename(p), pixmap=pm, path=p)
                b.set_status("Sending…")
                self.file_bubbles[fid[0]] = b
                self._seen.add(fid[0])

    @staticmethod
    def _pix(path):
        img = QtGui.QImage(path)
        if img.isNull():
            return None
        return QtGui.QPixmap.fromImage(img.scaled(320, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _files_refresh(self):
        """photos / files attached here: progress on our side, a bubble at the other side."""
        for fid in list(self.fm.order):
            it = self.fm.items[fid]
            if not it.chat:
                continue
            if it.direction == "in" and it.status == "done" and fid not in self._seen:
                self._seen.add(fid)
                pm = self._pix(it.path) if it.kind == "image" else None
                b = self._add(False, text=None if pm else f"{it.name}  ({media.human(it.size)})",
                              pixmap=pm, path=it.path)
                b.set_status(time.strftime("%H:%M"))
                self.new_message.emit()
            b = self.file_bubbles.get(fid)
            if b and it.direction == "out":
                if it.status == "done":
                    b.set_status(time.strftime("%H:%M") + "  ✓ delivered")
                    del self.file_bubbles[fid]
                elif it.status in ("failed", "cancelled"):
                    b.set_status("Not delivered")
                    del self.file_bubbles[fid]
                elif it.active:
                    b.set_status(f"Sending {100 * it.progress:.0f} %")

    new_message = QtCore.pyqtSignal()

    # events from the node
    def on_msg(self, obj):
        text = str(obj.get("text", ""))
        if text:
            self._add(False, text=text)

    def on_sent(self, xid):
        b = self.pending.pop(xid, None)
        if b:
            b.set_status(time.strftime("%H:%M") + "  ✓ delivered")

    def on_failed(self, xid, reason):
        b = self.pending.pop(xid, None)
        if b:
            b.set_status("Not delivered: the link was down")


# ====================================================================== 4 in a Row
class Board(QtWidgets.QWidget):
    clicked = QtCore.pyqtSignal(int)

    def __init__(self, game, palette, colours, parent=None):
        super().__init__(parent)
        self.g, self.c, self.colours = game, palette, colours
        self.hover = None
        self.anim = None                  # (col, row, y) of a falling disc
        self.setMouseTracking(True)
        self.setMinimumSize(560, 480)
        self.t = QtCore.QTimer(self)
        self.t.timeout.connect(self._step)

    def geom(self):
        cols, rows = connect4.COLS, connect4.ROWS
        cell = min(self.width() / cols, (self.height() - 20) / rows)
        w, h = cell * cols, cell * rows
        return cell, (self.width() - w) / 2, (self.height() - h) / 2 + 10

    def drop(self, col, row):
        self.anim = [col, row, -1.0, 0.0]
        self.t.start(16)

    def _step(self):
        if not self.anim:
            self.t.stop()
            return
        a = self.anim
        a[3] += 0.045
        a[2] += a[3]
        target = connect4.ROWS - 1 - a[1]
        if a[2] >= target:
            self.anim = None
            self.t.stop()
        self.update()

    def mouseMoveEvent(self, e):
        cell, x0, _ = self.geom()
        c = int((e.x() - x0) // cell)
        self.hover = c if 0 <= c < connect4.COLS else None
        self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        if self.hover is not None:
            self.clicked.emit(self.hover)

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        cell, x0, y0 = self.geom()
        cols, rows = connect4.COLS, connect4.ROWS
        board = QtCore.QRectF(x0 - 8, y0 - 8, cell * cols + 16, cell * rows + 16)
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor(self.c["raised"]))
        p.drawRoundedRect(board, 18, 18)
        if self.hover is not None and self.g.my_turn():
            p.setBrush(QtGui.QColor(self.c["line"]))
            p.drawRoundedRect(QtCore.QRectF(x0 + self.hover * cell + 4, y0 - 4, cell - 8, cell * rows + 8), 12, 12)
        win = set(map(tuple, self.g.line or []))
        cells = self.g.board.cells
        for r in range(rows):
            for c in range(cols):
                who = cells[r][c]
                cx, cy = x0 + (c + 0.5) * cell, y0 + (rows - 1 - r + 0.5) * cell
                if self.anim and (c, r) == (self.anim[0], self.anim[1]):
                    who = 0
                col = QtGui.QColor(self.colours.get(who, self.c["bg"]))
                p.setBrush(col)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QtCore.QPointF(cx, cy), cell * 0.38, cell * 0.38)
                if (r, c) in win:
                    p.setPen(QtGui.QPen(QtGui.QColor(self.c["text"]), 3))
                    p.setBrush(Qt.NoBrush)
                    p.drawEllipse(QtCore.QPointF(cx, cy), cell * 0.43, cell * 0.43)
        if self.anim:
            c, r, y, _ = self.anim
            who = cells[r][c]
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(self.colours.get(who, "#888")))
            p.drawEllipse(QtCore.QPointF(x0 + (c + 0.5) * cell, y0 + (y + 0.5) * cell), cell * 0.38, cell * 0.38)
        p.setPen(QtGui.QColor(self.c["muted"]))
        p.setFont(theme.font(13, display=True))
        for c in range(cols):
            p.drawText(QtCore.QRectF(x0 + c * cell, y0 + rows * cell + 8, cell, 20), Qt.AlignCenter, str(c + 1))
        p.end()


class GamePage(QtWidgets.QWidget):
    def __init__(self, node, palette, peer_name="other PC", parent=None):
        super().__init__(parent)
        self.node, self.c = node, palette
        self.peer_name = peer_name
        me, peer = node.node, node.peer_node
        self.game = connect4.Game(me, peer, lambda obj: node.send_msg("game", obj))
        # the same disc colours on both PCs: node 1 coral, node 2 yellow
        self.colours = {1: "#E2574C", 2: "#F2C14E", 0: palette["bg"]}
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(label("4 in a Row", 30, QtGui.QFont.DemiBold, display=True))
        top.addStretch(1)
        self.score = label("", 18, QtGui.QFont.DemiBold, display=True)
        top.addWidget(self.score)
        root.addLayout(top)
        self.status = label("", 18)
        root.addWidget(self.status)
        self.board = Board(self.game, palette, self.colours)
        self.board.clicked.connect(self.play)
        root.addWidget(self.board, 1)
        row = QtWidgets.QHBoxLayout()
        new = button("New game", "primary", tip="Ctrl+N")
        new.clicked.connect(self.new_game)
        new.setFocusPolicy(Qt.NoFocus)
        row.addWidget(new)
        res = button("Resign", "danger")
        res.clicked.connect(self.resign)
        res.setFocusPolicy(Qt.NoFocus)
        row.addWidget(res)
        row.addStretch(1)
        row.addWidget(label("Keys 1 to 7 drop a disc", 14, muted=True))
        root.addLayout(row)
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+N"), self, self.new_game)
        self.refresh()

    def keyPressEvent(self, e):
        t = e.text()
        if t and t in "1234567":
            self.play(int(t) - 1)
        else:
            super().keyPressEvent(e)

    def new_game(self):
        self.game.new_game()
        self.refresh()

    def resign(self):
        self.game.resign()
        self.refresh()

    def play(self, col):
        n = len(self.game.moves)
        err = self.game.play(col)
        if err:
            self.status.setText(err[0].upper() + err[1:])
            return
        if len(self.game.moves) > n:
            r, c = self.game.last
            self.board.drop(c, r)
        self.refresh()

    def on_msg(self, obj):
        n = len(self.game.moves)
        self.game.on_message(obj)
        if len(self.game.moves) > n and self.game.last:
            r, c = self.game.last
            self.board.drop(c, r)
        self.refresh()

    def refresh(self):
        g = self.game
        s = g.score
        self.score.setText(f"You {s[g.me]}   Them {s[g.peer]}")
        if g.id is None:
            t = "Press Ctrl+N to start a game"
        elif g.result:
            t = {"win": "You win", "loss": f"The {self.peer_name} wins", "draw": "Draw: the board is full",
                 "resigned": "You resigned", "peer resigned": f"The {self.peer_name} resigned: you win"}[g.result]
            t += ". Ctrl+N plays again."
        else:
            t = "Your move" if g.my_turn() else f"Waiting for the {self.peer_name}'s move"
        self.status.setText(t)
        self.board.update()


# ====================================================================== settings
class SettingsPage(QtWidgets.QWidget):
    restart_needed = QtCore.pyqtSignal(str)

    def __init__(self, node, fm, palette, key_path, parent=None):
        super().__init__(parent)
        self.node, self.fm, self.c, self.key_path = node, fm, palette, key_path
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        root.addWidget(label("Settings", 30, QtGui.QFont.DemiBold, display=True))

        def card(title, text, *buttons):
            p = Panel()
            h = QtWidgets.QHBoxLayout(p)
            h.setContentsMargins(22, 16, 22, 16)
            col = QtWidgets.QVBoxLayout()
            col.addWidget(label(title, 16, QtGui.QFont.DemiBold))
            t = label(text, 14, muted=True, wrap=True)
            col.addWidget(t)
            h.addLayout(col, 1)
            for b in buttons:
                h.addWidget(b)
            root.addWidget(p)
            return t
        other = node.peer_node
        sw = button(f"Switch to node {other}")
        sw.clicked.connect(self.switch_node)
        card(f"This PC is node {node.node}", f"The other PC must be node {other}.", sw)
        exp = button("Save a copy of the key…")
        exp.clicked.connect(self.export_key)
        imp = button("Use a key from a file…")
        imp.clicked.connect(self.import_key)
        card(f"Pairing code {secure.fingerprint(node.pairing)}",
             "Both PCs must show the same code. Everything on the radio link is encrypted with this key.", exp, imp)
        ch = button("Change…")
        ch.clicked.connect(self.change_folder)
        op = button("Open")
        op.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.fm.recv_dir)))
        self.folder = card("Received files", fm.recv_dir, ch, op)
        self.note = label("", 14, muted=True, wrap=True)
        root.addWidget(self.note)
        root.addStretch(1)
        import config
        root.addWidget(label(f"SDR Link {config.VERSION}", 12, muted=True))

    def switch_node(self):
        other = self.node.peer_node
        if QtWidgets.QMessageBox.question(self, "Switch node", f"Make this PC node {other}? The app closes; "
                                          "open it again to use the new setting.") == QtWidgets.QMessageBox.Yes:
            self.node.s.node = other
            self.node.s.save()
            QtWidgets.QApplication.quit()

    def change_folder(self):
        p = QtWidgets.QFileDialog.getExistingDirectory(self, "Folder for received files", self.fm.recv_dir)
        if p:
            self.fm.recv_dir = p
            self.node.s.recv_dir = p
            self.node.s.save()
            self.folder.setText(p)

    def export_key(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save the pairing key", "pairing.key",
                                                        "Pairing key (*.key)")
        if path:
            secure.save_pairing(self.node.pairing, path)
            self.note.setText("Saved. On the other PC: Settings, Use a key from a file. "
                              "Keep this file private: it opens the link.")

    def import_key(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Pairing key", "", "Pairing key (*.key)")
        if not path:
            return
        try:
            p = secure.load_pairing(path)
        except secure.PairingError as ex:
            self.note.setText(str(ex))
            return
        secure.save_pairing(p, self.key_path)
        self.restart_needed.emit(f"New pairing code {secure.fingerprint(p)}. Close and open the app to use it.")
