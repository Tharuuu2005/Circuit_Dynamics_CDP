"""First start on a PC: node 1 or node 2, then the pairing key."""
import os

from PyQt5 import QtCore, QtGui, QtWidgets

import secure
from . import theme
from .widgets import button, label

Qt = QtCore.Qt


class SetupDialog(QtWidgets.QDialog):
    def __init__(self, key_path, role=None, parent=None):
        super().__init__(parent)
        self.key_path = key_path
        self.role = role
        self.pairing = None
        self.setWindowTitle("Set up SDR Link")
        self.setMinimumSize(760, 520)
        self.setStyleSheet(theme.qss("app"))
        self.c = theme.PALETTES["app"]
        self.stack = QtWidgets.QStackedWidget(self)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(40, 34, 40, 30)
        lay.addWidget(self.stack)
        self.stack.addWidget(self._role_page())
        self.stack.addWidget(self._key_page())
        self.stack.addWidget(self._done_page())
        if role:
            self.stack.setCurrentIndex(1)

    # ---- step 1
    def _role_page(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setSpacing(14)
        v.addWidget(label("Which node is this PC?", 30, QtGui.QFont.DemiBold, display=True))
        v.addWidget(label("Make one PC node 1 and the other node 2. Each sends on its own frequency.",
                          15, muted=True, wrap=True))
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(18)
        for node, text in ((1, "Sends on F1, listens on F2"), (2, "Sends on F2, listens on F1")):
            b = QtWidgets.QPushButton()
            b.setMinimumSize(300, 170)
            b.setCursor(Qt.PointingHandCursor)
            bl = QtWidgets.QVBoxLayout(b)
            bl.setContentsMargins(22, 22, 22, 22)
            big = label(f"Node {node}", 34, QtGui.QFont.DemiBold, display=True)
            big.setStyleSheet(f"color: {self.c['signal']};")
            bl.addWidget(big)
            bl.addStretch(1)
            bl.addWidget(label(text, 14, muted=True, wrap=True))
            b.clicked.connect(lambda _, n=node: self._pick(n))
            row.addWidget(b)
        v.addLayout(row)
        v.addStretch(1)
        return w

    def _pick(self, node):
        self.role = node
        self.stack.setCurrentIndex(1)

    # ---- step 2
    def _key_page(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setSpacing(14)
        v.addWidget(label("Pair the two PCs", 30, QtGui.QFont.DemiBold, display=True))
        v.addWidget(label("Everything on the radio link is encrypted with a key that only your two PCs "
                          "have. Create the key on one PC, save a copy (a USB stick is fine) and load "
                          "that copy on the other PC.", 15, muted=True, wrap=True))
        v.addSpacing(10)
        make = button("Create a new pairing key", "primary", h=56)
        make.clicked.connect(self._create)
        load = button("Load the key from the other PC…", h=56)
        load.clicked.connect(self._load)
        v.addWidget(make)
        v.addWidget(load)
        self.err = label("", 14)
        self.err.setStyleSheet(f"color: {self.c['alarm']};")
        v.addWidget(self.err)
        v.addStretch(1)
        return w

    def _create(self):
        self.pairing = secure.new_pairing()
        secure.save_pairing(self.pairing, self.key_path)
        self._show_done(created=True)

    def _load(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Pairing key from the other PC", "",
                                                        "Pairing key (*.key)")
        if not path:
            return
        try:
            p = secure.load_pairing(path)
        except secure.PairingError as ex:
            self.err.setText(str(ex))
            return
        if p is None:
            self.err.setText("That file is empty.")
            return
        self.pairing = p
        secure.save_pairing(p, self.key_path)
        self._show_done(created=False)

    # ---- step 3
    def _done_page(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setSpacing(14)
        v.addWidget(label("Paired", 30, QtGui.QFont.DemiBold, display=True))
        self.code = label("", 44, QtGui.QFont.DemiBold, display=True)
        self.code.setStyleSheet(f"color: {self.c['signal']};")
        v.addWidget(self.code)
        self.done_text = label("", 15, muted=True, wrap=True)
        v.addWidget(self.done_text)
        self.save_btn = button("Save a copy for the other PC…", h=52)
        self.save_btn.clicked.connect(self._save_copy)
        v.addWidget(self.save_btn)
        self.saved = label("", 14, muted=True)
        v.addWidget(self.saved)
        v.addStretch(1)
        go = button("Start", "primary", h=52)
        go.clicked.connect(self.accept)
        v.addWidget(go, 0, Qt.AlignRight)
        return w

    def _show_done(self, created):
        self.code.setText(secure.fingerprint(self.pairing))
        if created:
            self.done_text.setText("This is the pairing code. The other PC shows the same code once it has "
                                   "loaded the copy. Keep the copy private: whoever has it can use the link.")
            self.save_btn.show()
        else:
            self.done_text.setText("Check that the other PC shows the same pairing code (Settings).")
            self.save_btn.hide()
        self.stack.setCurrentIndex(2)

    def _save_copy(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save the pairing key", "pairing.key",
                                                        "Pairing key (*.key)")
        if path:
            secure.save_pairing(self.pairing, path)
            self.saved.setText(f"Saved as {os.path.basename(path)}")
