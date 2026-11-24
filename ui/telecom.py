"""
Hidden telecom panel (Ctrl+Shift+D): every number of the radio link, for those who want
them. Opens over the middle of the window; Esc closes it.

  Overview   node, radio and link numbers, live
  Graphs     speed, round trip, frames getting through, radio real-time factor (last 5 min)
  Transfers  every radio transfer and every file, with frames, resends and speed
  Log        link, radio and transfer events; the 1-second telemetry CSV files
  Radio      frequencies, TX attenuation, RX gain (live); profile, duplex (next start)
"""
import os
import time

from PyQt5 import QtCore, QtGui, QtWidgets

import config
import media
from . import theme
from .files_page import open_path
from .widgets import Panel, button, label

Qt = QtCore.Qt


class Chart(QtWidgets.QWidget):
    def __init__(self, palette, title, unit, parent=None):
        super().__init__(parent)
        self.c, self.title, self.unit = palette, title, unit
        self.series = []               # (name, colour, [values or None])
        self.fixed_max = None
        self.setMinimumHeight(150)

    def set(self, series, fixed_max=None):
        self.series, self.fixed_max = series, fixed_max
        self.update()

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        c = self.c
        r = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QtGui.QPen(QtGui.QColor(c["line"]), 1))
        p.setBrush(QtGui.QColor(c["panel"]))
        p.drawRoundedRect(r, 10, 10)
        left, top, right, bottom = 54, 34, 14, 22
        plot = QtCore.QRectF(left, top, self.width() - left - right, self.height() - top - bottom)
        vals = [v for _, _, vs in self.series for v in vs if v is not None]
        vmax = self.fixed_max or max([1e-9] + vals) * 1.15
        p.setFont(theme.font(12))
        for k in range(5):
            y = plot.bottom() - plot.height() * k / 4
            p.setPen(QtGui.QPen(QtGui.QColor(c["line"]), 1, Qt.DotLine if k else Qt.SolidLine))
            p.drawLine(QtCore.QPointF(plot.left(), y), QtCore.QPointF(plot.right(), y))
            p.setPen(QtGui.QColor(c["faint"]))
            v = vmax * k / 4
            p.drawText(QtCore.QRectF(0, y - 8, left - 8, 16), Qt.AlignRight | Qt.AlignVCenter,
                       f"{v:.0f}" if vmax >= 10 else f"{v:.2f}")
        p.setPen(QtGui.QColor(c["faint"]))
        n_pts = max((len(vs) for _, _, vs in self.series), default=0)
        p.drawText(QtCore.QRectF(plot.left(), plot.bottom() + 4, plot.width(), 16), Qt.AlignLeft,
                   f"{n_pts // 60} min ago" if n_pts >= 120 else f"{n_pts} s ago")
        p.drawText(QtCore.QRectF(plot.left(), plot.bottom() + 4, plot.width(), 16), Qt.AlignRight, "now")
        x0 = 14
        p.setFont(theme.font(13, QtGui.QFont.DemiBold))
        p.setPen(QtGui.QColor(c["text"]))
        p.drawText(QtCore.QPointF(x0, 22), f"{self.title} ({self.unit})")
        x = x0 + QtGui.QFontMetrics(p.font()).horizontalAdvance(f"{self.title} ({self.unit})") + 18
        p.setFont(theme.font(12))
        for name, col, vs in self.series:
            last = next((v for v in reversed(vs) if v is not None), None)
            txt = f"{name} {last:.1f}" if last is not None else f"{name} -"
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(col))
            p.drawRoundedRect(QtCore.QRectF(x, 13, 14, 4), 2, 2)
            p.setPen(QtGui.QColor(c["muted"]))
            p.drawText(QtCore.QPointF(x + 19, 22), txt)
            x += QtGui.QFontMetrics(p.font()).horizontalAdvance(txt) + 36
            n = max(2, len(vs))
            pen = QtGui.QPen(QtGui.QColor(col), 1.8)
            p.setPen(pen)
            path, started = QtGui.QPainterPath(), False
            for i, v in enumerate(vs):
                if v is None:
                    started = False
                    continue
                pt = QtCore.QPointF(plot.left() + plot.width() * i / (n - 1),
                                    plot.bottom() - plot.height() * min(v, vmax) / vmax)
                if started:
                    path.lineTo(pt)
                else:
                    path.moveTo(pt)
                    started = True
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.end()


class TelecomPanel(QtWidgets.QWidget):
    def __init__(self, node, fm, palette, parent):
        super().__init__(parent)
        self.node, self.fm, self.c = node, fm, palette
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("TelecomPanel { background: rgba(4, 10, 16, 175); }")
        outer = QtWidgets.QVBoxLayout(self)
        self.box = Panel()
        outer.addWidget(self.box, 0, Qt.AlignCenter)
        v = QtWidgets.QVBoxLayout(self.box)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(label("Radio link", 24, QtGui.QFont.DemiBold, display=True))
        top.addSpacing(20)
        self.tabs = []
        group = QtWidgets.QButtonGroup(self)
        self.stack = QtWidgets.QStackedWidget()
        for i, (name, w) in enumerate((("Overview", self._overview()), ("Graphs", self._graphs()),
                                       ("Transfers", self._transfers()), ("Log", self._log()),
                                       ("Radio", self._radio()))):
            b = QtWidgets.QPushButton(name)
            b.setProperty("kind", "rail")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _, k=i: self.show_tab(k))
            group.addButton(b)
            top.addWidget(b)
            self.tabs.append(b)
            self.stack.addWidget(w)
        top.addStretch(1)
        top.addWidget(label("Esc closes", 13, muted=True))
        v.addLayout(top)
        v.addWidget(self.stack, 1)
        self.t = QtCore.QTimer(self)
        self.t.timeout.connect(self.refresh)
        self.show_tab(0)
        self.hide()

    # ------------------------------------------------------------------ tabs
    def _overview(self):
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(14)
        self.ov = []
        for name, rows in self.node.overview():
            p = QtWidgets.QFrame()
            p.setStyleSheet(f"QFrame {{ background: {self.c['raised']}; border-radius: 10px; }}")
            g = QtWidgets.QGridLayout(p)
            g.setContentsMargins(16, 14, 16, 14)
            g.setVerticalSpacing(7)
            g.setHorizontalSpacing(12)
            g.addWidget(label(name, 16, QtGui.QFont.DemiBold, display=True), 0, 0, 1, 2)
            vals = []
            for i, (k, _) in enumerate(rows):
                kl = label(k, 13, muted=True)
                vl = label("", 13, wrap=True)
                vl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                g.addWidget(kl, i + 1, 0, Qt.AlignTop)
                g.addWidget(vl, i + 1, 1, Qt.AlignTop)
                vals.append(vl)
            g.setColumnStretch(1, 1)
            g.setRowStretch(len(rows) + 1, 1)
            h.addWidget(p, 1)
            self.ov.append(vals)
        return w

    def _graphs(self):
        w = QtWidgets.QWidget()
        g = QtWidgets.QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(12)
        c = self.c
        self.ch_speed = Chart(c, "Speed", "KB/s")
        self.ch_rtt = Chart(c, "Round trip", "ms")
        self.ch_q = Chart(c, "Frames getting through", "%")
        self.ch_radio = Chart(c, "Radio speed", "x real time")
        g.addWidget(self.ch_speed, 0, 0, 1, 2)
        g.addWidget(self.ch_rtt, 1, 0)
        g.addWidget(self.ch_q, 1, 1)
        g.addWidget(self.ch_radio, 2, 0, 1, 2)
        return w

    def _transfers(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(label("Files", 15, QtGui.QFont.DemiBold))
        self.t_files = self._table(["Time", "Direction", "Name", "Size", "Seconds", "KB/s", "Radio frames",
                                    "Frames resent", "Blocks sent again", "Result"])
        v.addWidget(self.t_files, 3)
        v.addWidget(label("Every radio transfer (file blocks, messages, game moves)", 15, QtGui.QFont.DemiBold))
        self.t_radio = self._table(["Time", "Direction", "Kind", "Bytes", "Seconds", "KB/s", "Frames",
                                    "Resent / duplicates"])
        v.addWidget(self.t_radio, 2)
        return w

    def _table(self, cols):
        t = QtWidgets.QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.verticalHeader().hide()
        t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        t.horizontalHeader().setStretchLastSection(True)
        t.setShowGrid(False)
        t.setFont(theme.font(13))
        return t

    def _log(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        self.logbox = QtWidgets.QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setFont(theme.font(13))
        v.addWidget(self.logbox, 1)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(label("Every second all link and radio numbers are also saved to "
                            "data/telemetry/<date>_node<N>.csv (opens in Excel).", 13, muted=True, wrap=True), 1)
        b = button("Open the telemetry folder")
        b.clicked.connect(lambda: open_path(os.path.join(self.node.data_dir, "telemetry")))
        row.addWidget(b)
        cp = button("Copy the log")
        cp.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.logbox.toPlainText()))
        row.addWidget(cp)
        v.addLayout(row)
        return w

    def _radio(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        s = self.node.s

        def spin(lo, hi, val, dec, step):
            x = QtWidgets.QDoubleSpinBox()
            x.setRange(lo, hi)
            x.setDecimals(dec)
            x.setSingleStep(step)
            x.setValue(val)
            x.setMinimumWidth(150)
            return x
        v.addWidget(label("Live (applied at once, saved; set the same F1 / F2 on the other PC)", 15,
                          QtGui.QFont.DemiBold))
        g = QtWidgets.QGridLayout()
        g.setHorizontalSpacing(14)
        self.f1 = spin(70, 6000, s.f1_mhz, 3, 1)
        self.f2 = spin(70, 6000, s.f2_mhz, 3, 1)
        self.att = spin(0, 89, s.tx_atten, 1, 1)
        self.gain = spin(0, 73, s.rx_gain, 1, 1)
        for i, (k, x) in enumerate((("F1 MHz (node 1 sends)", self.f1), ("F2 MHz (node 2 sends)", self.f2),
                                    ("TX attenuation dB", self.att), ("RX gain dB", self.gain))):
            g.addWidget(label(k, 13, muted=True), 0, i)
            g.addWidget(x, 1, i)
        ap = button("Apply now", "primary")
        ap.clicked.connect(self.apply)
        g.addWidget(ap, 1, 4)
        g.setColumnStretch(5, 1)
        v.addLayout(g)
        v.addSpacing(10)
        v.addWidget(label("Next start (both PCs must use the same)", 15, QtGui.QFont.DemiBold))
        h = QtWidgets.QHBoxLayout()
        self.profile = QtWidgets.QComboBox()
        for name, pr in config.PROFILES.items():
            self.profile.addItem(f"{name}: {pr['samp_rate'] / 1e6:g} Msps, {pr['sps']} sps, "
                                 f"{pr['samp_rate'] / pr['sps'] / 1e6:g} Mbit/s", name)
        self.profile.setCurrentIndex(list(config.PROFILES).index(s.profile))
        self.duplex = QtWidgets.QComboBox()
        for d, t in (("fdd", "fdd: both ways at once, silent when idle"),
                     ("fdd-cont", "fdd-cont: both ways, always transmitting"),
                     ("tdd", "tdd: take turns")):
            self.duplex.addItem(t, d)
        self.duplex.setCurrentIndex(list(config.DUPLEX_MODES).index(s.duplex))
        sv = button("Save for next start")
        sv.clicked.connect(self.save_mode)
        h.addWidget(self.profile)
        h.addWidget(self.duplex)
        h.addWidget(sv)
        h.addStretch(1)
        v.addLayout(h)
        v.addSpacing(10)
        rs = button("Restart this PC's radio")
        rs.clicked.connect(self.node.restart_radio)
        v.addWidget(rs, 0, Qt.AlignLeft)
        self.note = label("", 13, muted=True, wrap=True)
        v.addWidget(self.note)
        v.addStretch(1)
        return w

    # ------------------------------------------------------------------ show / hide
    def show_tab(self, k):
        self.tabs[k].setChecked(True)
        self.stack.setCurrentIndex(k)
        self.refresh()

    def toggle(self):
        if self.isVisible():
            self.close_panel()
        else:
            self.place()
            self.refresh()
            self.show()
            self.raise_()
            self.setFocus()
            self.t.start(1000)

    def place(self):
        pr = self.parent().rect()
        self.setGeometry(pr)
        self.box.setFixedSize(min(1240, pr.width() - 40), min(780, pr.height() - 40))

    def close_panel(self):
        self.t.stop()
        self.hide()
        self.parent().setFocus()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_panel()
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if not self.box.geometry().contains(e.pos()):
            self.close_panel()

    # ------------------------------------------------------------------ data
    def refresh(self):
        if not self.isVisible() and self.sender() is self.t:
            return
        k = self.stack.currentIndex()
        if k == 0:
            for vals, (_, rows) in zip(self.ov, self.node.overview()):
                for vl, (_, v) in zip(vals, rows):
                    vl.setText(v)
        elif k == 1:
            S = list(self.node.samples)[-300:]
            col = lambda key: [x.get(key) for x in S]
            c = self.c
            pct = lambda key: [None if x.get(key) is None else 100 * x[key] for x in S]
            self.ch_speed.set([("data out", c["action"], col("goodput_out_KBps")),
                               ("data in", c["open"], col("goodput_in_KBps")),
                               ("on the air out", "#7FA7C9", col("air_out_KBps")),
                               ("on the air in", "#6FB89A", col("air_in_KBps"))],
                              fixed_max=max(10, self.node.rate.byte_rate / 1e3 * 1.1))
            self.ch_rtt.set([("data", c["signal"], col("srtt_ms")), ("beacon", c["action"], col("beacon_rtt_ms"))])
            self.ch_q.set([("to the other PC", c["action"], pct("frames_through_to_peer")),
                           ("from it", c["open"], pct("frames_through_from_peer"))], fixed_max=100)
            self.ch_radio.set([("receiver", c["open"], col("radio_rx_x")),
                               ("transmitter", c["action"], col("radio_tx_x"))], fixed_max=1.5)
        elif k == 2:
            self._fill_files()
            self._fill_radio()
        elif k == 3:
            txt = "\n".join(self.node.log)
            if txt != self.logbox.toPlainText():
                self.logbox.setPlainText(txt)
                self.logbox.verticalScrollBar().setValue(self.logbox.verticalScrollBar().maximum())

    def _fill_files(self):
        rows = list(reversed(self.fm.history))[:200]
        t = self.t_files
        t.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [time.strftime("%H:%M:%S", time.localtime(r.get("end") or r.get("start") or 0)),
                    "sent" if r["dir"] == "out" else "received", r["name"], media.human(r["size"]),
                    r.get("seconds", ""), "" if not r.get("rate") else f"{r['rate'] / 1e3:.1f}",
                    r.get("frames") or "", r.get("retx") if r["dir"] == "out" else "",
                    r.get("resends") or "", r["status"] + (f": {r['error']}" if r.get("error") else "")]
            for j, v in enumerate(vals):
                t.setItem(i, j, QtWidgets.QTableWidgetItem(str(v)))
        t.resizeColumnsToContents()

    def _fill_radio(self):
        rows = list(reversed(self.node.transfers))[:300]
        t = self.t_radio
        t.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [time.strftime("%H:%M:%S", time.localtime(r["time"])), "out" if r["dir"] == "out" else "in",
                    r.get("kind"), r["bytes"], r["seconds"],
                    f"{r['rate'] / 1e3:.1f}" if r["seconds"] >= 0.05 else "",
                    r.get("frames", ""), r.get("retx", r.get("dups", ""))]
            for j, v in enumerate(vals):
                t.setItem(i, j, QtWidgets.QTableWidgetItem(str(v)))
        t.resizeColumnsToContents()

    def apply(self):
        err, warn = config.check_freqs(int(self.f1.value() * 1e6), int(self.f2.value() * 1e6))
        if err:
            self.note.setText(err)
            return
        self.node.apply_rf(self.f1.value(), self.f2.value(), self.att.value(), self.gain.value())
        self.note.setText((warn + " " if warn else "") + "Applied and saved.")

    def save_mode(self):
        self.node.s.profile = self.profile.currentData()
        self.node.s.duplex = self.duplex.currentData()
        self.node.s.save()
        self.note.setText("Saved. Choose the same on the other PC, then close and open both apps.")
