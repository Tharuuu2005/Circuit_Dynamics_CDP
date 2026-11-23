#!/usr/bin/env python3
"""
SDR LINK - messenger + 4 in a Row over two ANTSDR E200s (GNU Radio 3.10).

    python app.py --node 1 --start-phy        # PC 1
    python app.py --node 2 --start-phy        # PC 2
    python app.py --node 1 --f1 3300 --f2 3375 --start-phy    # choose the frequencies (MHz)

Both PCs must use the same --profile, --duplex and F1 / F2.
Without radios, on one PC:  python tools/fake_radio.py   then
    python app.py --node 1 --same-pc      and      python app.py --node 2 --same-pc

Keys: Enter send · Ctrl+I image · Ctrl+U audio · Ctrl+O any file · Ctrl+G game tab ·
      Ctrl+N new game · 1-7 drop a disc (Esc leaves the message box) · F5 radio on/off
"""
import argparse
import json
import os
import queue
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config  # noqa: E402
import connect4  # noqa: E402
import media  # noqa: E402
from radio import Radio, health_text  # noqa: E402
from transport import Transport  # noqa: E402

BG, PANEL, PANEL2 = "#18222b", "#22303b", "#2b3b48"
FG, MUTED = "#e6edf2", "#8ea3b3"
OK, BAD, WARN, ACCENT = "#4cc38a", "#e5604f", "#e8b04a", "#5fb3e6"
DISC = {1: "#e5604f", 2: "#f2c14e"}          # node 1 red, node 2 yellow
BOARD_BG, HOLE = "#1d4f91", "#0f1b26"


class App:
    def __init__(self, root, a):
        self.root, self.a = root, a
        self.me, self.peer = a.node, 2 if a.node == 1 else 1
        self.f1, self.f2 = a.f1, a.f2
        self.rate = config.get_profile(a.profile)
        self.events = queue.Queue()
        ports = config.SAME_PC_PORTS[self.me] if a.same_pc else (
            a.tx_port or config.NODES[self.me]["tx_port"], a.rx_port or config.NODES[self.me]["rx_port"],
            a.ctrl_port or config.NODES[self.me]["ctrl_port"])
        self.tr = Transport(self.me, self.peer, ports[0], ports[1], self.rate, on_event=self.events.put,
                            duplex=a.duplex)
        self.radio = Radio(self.me, a.profile, a.duplex, ports,
                           on_line=lambda s: self.events.put({"type": "phy", "line": s}),
                           on_health=lambda h: self.events.put({"type": "health", "h": h}),
                           extra_args=a.phy_args)
        self.game = connect4.Game(self.me, self.peer, self._send_game)
        self.game_out = {}                 # xfer -> message (resent if not delivered)
        self.game_retry = []
        self.rows, self.marks = {}, {}
        self.thumbs, self.last_path = [], None
        self.recv_dir = os.path.join(HERE, "received")
        self.anim = None
        self.hover_col = None
        self._build()
        self.tr.start()
        if a.start_phy:
            self.toggle_radio()
        self.say("sys", f"Node {self.me}. F1 {self.f1 / 1e6:g} MHz, F2 {self.f2 / 1e6:g} MHz, {a.duplex} mode. "
                        "Same settings on the other PC.")
        self.root.after(50, self._poll)
        self.root.after(500, self._tick)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    # ================================================================== layout
    def _build(self):
        r = self.root
        r.title(f"SDR LINK v{config.VERSION} - node {self.me}")
        r.configure(bg=BG)
        r.geometry("1320x840")
        r.minsize(1100, 720)
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=PANEL2)
        st.configure("TFrame", background=BG)
        st.configure("P.TFrame", background=PANEL)
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("M.TLabel", background=BG, foreground=MUTED)
        st.configure("P.TLabel", background=PANEL, foreground=FG)
        st.configure("PM.TLabel", background=PANEL, foreground=MUTED)
        st.configure("H.TLabel", background=PANEL, foreground=FG, font=("Segoe UI", 15, "bold"))
        st.configure("TButton", background=PANEL2, foreground=FG, padding=(10, 5), borderwidth=0)
        st.map("TButton", background=[("active", "#38505f"), ("disabled", PANEL)],
               foreground=[("disabled", MUTED)])
        st.configure("A.TButton", background="#2f6f99")
        st.map("A.TButton", background=[("active", "#3b84b5")])
        st.configure("TCheckbutton", background=BG, foreground=FG)
        st.map("TCheckbutton", background=[("active", BG)])
        st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG, rowheight=22,
                     borderwidth=0)
        st.configure("Treeview.Heading", background=BG, foreground=MUTED, borderwidth=0)
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(16, 6))
        st.map("TNotebook.Tab", background=[("selected", PANEL2)], foreground=[("selected", FG)])
        st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL)

        # ---- top bar
        top = ttk.Frame(r, padding=(14, 10, 14, 6))
        top.pack(fill="x")
        self.dot = tk.Label(top, text="●", fg=BAD, bg=BG, font=("Segoe UI", 18))
        self.dot.pack(side="left")
        self.link_lbl = ttk.Label(top, text="Link down", font=("Segoe UI", 13, "bold"))
        self.link_lbl.pack(side="left", padx=(4, 14))
        self.qual_lbl = ttk.Label(top, text="", style="M.TLabel", font=("Segoe UI", 10))
        self.qual_lbl.pack(side="left")
        self.radio_btn = ttk.Button(top, text="Start radio  (F5)", style="A.TButton", command=self.toggle_radio)
        self.radio_btn.pack(side="right")
        self.info_lbl = ttk.Label(top, text="", style="M.TLabel")
        self.info_lbl.pack(side="right", padx=14)
        self._show_info()

        # ---- bottom: transfers + radio controls (packed before the body so they stay visible)
        bottom = ttk.Frame(r, padding=(14, 0, 14, 10))
        bottom.pack(side="bottom", fill="x")
        cols = ("dir", "name", "size", "progress", "rate", "status")
        self.tree = ttk.Treeview(bottom, columns=cols, show="headings", height=4)
        for c, w in zip(cols, (60, 280, 90, 80, 100, 520)):
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="x")
        self.pbar = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.pbar.pack(fill="x", pady=(4, 6))
        rf = ttk.Frame(bottom)
        rf.pack(fill="x")
        self.health_lbl = tk.Label(rf, text="radio: not started", bg=BG, fg=MUTED, anchor="w")
        self.health_lbl.pack(side="left", fill="x", expand=True)
        self.f1_var = tk.StringVar(value=f"{self.f1 / 1e6:g}")
        self.f2_var = tk.StringVar(value=f"{self.f2 / 1e6:g}")
        self.atten = tk.DoubleVar(value=config.DEFAULT_TX_ATTEN)
        self.gain = tk.DoubleVar(value=config.DEFAULT_RX_GAIN)
        for label, var, kind in (("F1 MHz", self.f1_var, "e"), ("F2 MHz", self.f2_var, "e"),
                                 ("TX atten dB", self.atten, (0, 89)), ("RX gain dB", self.gain, (0, 71))):
            ttk.Label(rf, text=label, style="M.TLabel").pack(side="left", padx=(10, 4))
            if kind == "e":
                tk.Entry(rf, width=7, textvariable=var, bg=PANEL, fg=FG, insertbackground=FG, relief="flat").pack(side="left")
            else:
                tk.Spinbox(rf, from_=kind[0], to=kind[1], increment=1, width=4, textvariable=var, bg=PANEL, fg=FG,
                           relief="flat", buttonbackground=PANEL).pack(side="left")
        ttk.Button(rf, text="Apply", command=self.apply_rf).pack(side="left", padx=(10, 0))

        # ---- body: chat | game / received
        body = tk.PanedWindow(r, orient="horizontal", bg=BG, sashwidth=6, bd=0)
        body.pack(fill="both", expand=True, padx=14, pady=(2, 8))
        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, minsize=520, stretch="always")
        body.add(right, minsize=470)

        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.chat = ScrolledText(left, bg=PANEL, fg=FG, insertbackground=FG, relief="flat", padx=10, pady=8,
                                 font=("Segoe UI", 11), wrap="word", state="disabled", cursor="arrow")
        self.chat.grid(row=0, column=0, sticky="nsew")
        for tag, col in (("me", ACCENT), ("peer", OK), ("sys", MUTED), ("bad", BAD), ("tick", OK), ("time", MUTED),
                         ("link", ACCENT)):
            self.chat.tag_config(tag, foreground=col)
        self.chat.tag_config("link", underline=True)
        self.chat.tag_config("time", font=("Segoe UI", 9))
        row = ttk.Frame(left)
        row.grid(row=1, column=0, sticky="ew", pady=(8, 4))
        row.columnconfigure(0, weight=1)
        self.entry = tk.Entry(row, bg=PANEL, fg=FG, insertbackground=FG, relief="flat", font=("Segoe UI", 12))
        self.entry.grid(row=0, column=0, sticky="ew", ipady=8)
        self.entry.bind("<Return>", lambda e: self.send_text())
        self.entry.bind("<Escape>", lambda e: self.canvas.focus_set())
        ttk.Button(row, text="Send", style="A.TButton", command=self.send_text).grid(row=0, column=1, padx=(8, 0))
        btns = ttk.Frame(left)
        btns.grid(row=2, column=0, sticky="ew")
        for text, kind in (("Image…  Ctrl+I", "image"), ("Audio…  Ctrl+U", "audio"), ("File…  Ctrl+O", "file")):
            ttk.Button(btns, text=text, command=lambda k=kind: self.send_file(k)).pack(side="left", padx=(0, 6))
        self.compress = tk.BooleanVar(value=False)
        self.quality = tk.IntVar(value=70)
        self.autoplay = tk.BooleanVar(value=False)
        ttk.Checkbutton(btns, text="Compress photos", variable=self.compress).pack(side="left", padx=(10, 0))
        tk.Spinbox(btns, from_=20, to=95, increment=5, width=3, textvariable=self.quality, bg=PANEL, fg=FG,
                   relief="flat", buttonbackground=PANEL).pack(side="left", padx=4)
        ttk.Checkbutton(btns, text="Auto-play audio", variable=self.autoplay).pack(side="left", padx=(10, 0))

        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True)
        gtab = ttk.Frame(self.nb, style="P.TFrame", padding=14)
        rtab = ttk.Frame(self.nb, style="P.TFrame", padding=14)
        self.nb.add(gtab, text="4 in a Row")
        self.nb.add(rtab, text="Received")
        self._build_game(gtab)
        self._build_received(rtab)

        for seq, fn in (("<Control-i>", lambda e: self.send_file("image")), ("<Control-u>", lambda e: self.send_file("audio")),
                        ("<Control-o>", lambda e: self.send_file("file")), ("<Control-n>", lambda e: self.new_game()),
                        ("<Control-g>", lambda e: (self.nb.select(0), self.canvas.focus_set())),
                        ("<F5>", lambda e: self.toggle_radio())):
            r.bind_all(seq, fn)
        for k in range(1, 8):
            r.bind_all(str(k), lambda e, c=k - 1: self._key_drop(e, c))

    def _build_game(self, f):
        f.columnconfigure(0, weight=1)
        head = ttk.Frame(f, style="P.TFrame")
        head.grid(row=0, column=0, sticky="ew")
        self.turn_lbl = ttk.Label(head, text="No game yet", style="H.TLabel")
        self.turn_lbl.pack(side="left")
        self.score_lbl = ttk.Label(head, text="", style="PM.TLabel", font=("Segoe UI", 11))
        self.score_lbl.pack(side="right")
        self.cell = 56
        w, h = connect4.COLS * self.cell + 20, connect4.ROWS * self.cell + 20 + 34
        self.canvas = tk.Canvas(f, width=w, height=h, bg=PANEL, highlightthickness=0, takefocus=1)
        self.canvas.grid(row=1, column=0, pady=(12, 8))
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))
        bar = ttk.Frame(f, style="P.TFrame")
        bar.grid(row=2, column=0)
        ttk.Button(bar, text="New game  Ctrl+N", style="A.TButton", command=self.new_game).pack(side="left", padx=4)
        ttk.Button(bar, text="Resign", command=self.resign).pack(side="left", padx=4)
        ttk.Label(f, text=f"You are {'red' if self.me == 1 else 'yellow'}  ·  click a column or press 1-7",
                  style="PM.TLabel").grid(row=3, column=0, pady=(8, 0))
        self._draw_board()

    def _build_received(self, f):
        f.rowconfigure(1, weight=1)
        f.columnconfigure(0, weight=1)
        ttk.Label(f, text="Last received", style="PM.TLabel").grid(row=0, column=0, sticky="w")
        self.preview = tk.Label(f, bg=PANEL, fg=MUTED, text="(nothing yet)")
        self.preview.grid(row=1, column=0, sticky="nsew")
        self.preview_info = ttk.Label(f, text="", style="P.TLabel", wraplength=440)
        self.preview_info.grid(row=2, column=0, sticky="w", pady=(6, 0))
        pr = ttk.Frame(f, style="P.TFrame")
        pr.grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.open_btn = ttk.Button(pr, text="Open", command=lambda: self.last_path and media.open_file(self.last_path),
                                   state="disabled")
        self.open_btn.pack(side="left")
        ttk.Button(pr, text="Received folder",
                   command=lambda: media.open_file(self._dir())).pack(side="left", padx=6)

    def _dir(self):
        os.makedirs(self.recv_dir, exist_ok=True)
        return self.recv_dir

    def _show_info(self):
        tx, rx = config.node_freqs(self.me, self.f1, self.f2)
        self.info_lbl.configure(text=f"node {self.me}  ·  TX {tx / 1e6:g} MHz  RX {rx / 1e6:g} MHz  ·  "
                                     f"{self.a.profile} {self.rate.bit_rate / 1e6:.1f} Mbit/s BPSK  ·  {self.a.duplex}")

    # ================================================================== chat
    def say(self, who, text, xid=None):
        c = self.chat
        c.configure(state="normal")
        c.insert("end", time.strftime("%H:%M  "), "time")
        if who == "me":
            c.insert("end", "You: ", "me")
        elif who == "peer":
            c.insert("end", f"Node {self.peer}: ", "peer")
        c.insert("end", text, "sys" if who in ("sys", "bad") else "")
        if who == "bad":
            c.tag_add("bad", "end-1c linestart", "end-1c")
        if xid is not None:
            m = f"m{xid}"
            c.mark_set(m, "end-1c")
            c.mark_gravity(m, "left")
            self.marks[xid] = m
        c.insert("end", "\n")
        c.configure(state="disabled")
        c.see("end")

    def _mark(self, xid, text, tag):
        m = self.marks.pop(xid, None)
        if m:
            self.chat.configure(state="normal")
            self.chat.insert(m, text, tag)
            self.chat.configure(state="disabled")

    def _thumb(self, path, label, tag_name):
        """an image (or file line) in the chat; click opens it."""
        c = self.chat
        c.configure(state="normal")
        shown = False
        if media.HAVE_PIL and os.path.splitext(path)[1].lower() in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
            try:
                from PIL import Image, ImageTk
                im = Image.open(path)
                im.thumbnail((300, 220))
                ph = ImageTk.PhotoImage(im)
                self.thumbs.append(ph)
                c.insert("end", "        ")
                c.image_create("end", image=ph, padx=2, pady=4)
                c.insert("end", "\n")
                shown = True
            except Exception:
                pass
        c.insert("end", "        ")
        c.insert("end", ("open " if shown else "") + label, ("link", tag_name))
        c.insert("end", "\n")
        c.tag_bind(tag_name, "<Button-1>", lambda e, p=path: media.open_file(p))
        c.tag_bind(tag_name, "<Enter>", lambda e: c.configure(cursor="hand2"))
        c.tag_bind(tag_name, "<Leave>", lambda e: c.configure(cursor="arrow"))
        c.configure(state="disabled")
        c.see("end")

    def send_text(self):
        t = self.entry.get().strip()
        if not t:
            return
        self.entry.delete(0, "end")
        xid = self.tr.send_text(t)
        self.say("me", t, xid=xid)

    def send_file(self, kind):
        ft = {"image": [("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff")],
              "audio": [("Audio", "*.wav *.mp3 *.ogg *.flac *.m4a *.aac")], "file": []}[kind] + [("All files", "*.*")]
        path = filedialog.askopenfilename(filetypes=ft)
        if not path:
            return
        try:
            data, meta = media.load_for_send(path, compress=self.compress.get(), quality=self.quality.get())
        except Exception as ex:
            messagebox.showerror("Send", str(ex))
            return
        xid = self.tr.send_bytes(data, meta)
        est = len(data) / (self.rate.byte_rate * 0.9)
        note = f", compressed from {media.human(meta['orig_size'])}" if meta.get("compressed") else ""
        self.say("me", f"{meta['name']} ({media.human(len(data))}{note}), "
                       + (f"about {est:.0f} s" if est >= 1.5 else "under 2 s"), xid=xid)
        if meta.get("kind") == "image":
            self._thumb(path, meta["name"], f"t{xid}s")
        self._row("tx", xid, meta["name"], len(data), "queued")

    # ================================================================== game
    def _send_game(self, obj):
        xid = self.tr.send_bytes(json.dumps(obj, separators=(",", ":")).encode(), {"kind": "game"})
        self.game_out[xid] = obj

    def new_game(self):
        first = self.game.new_game()
        self.nb.select(0)
        self.say("sys", "4 in a Row: new game, " + ("you move first." if first == self.me else
                                                     f"node {self.peer} moves first."))
        self._draw_board()

    def resign(self):
        if self.game.active:
            self.game.resign()
            self.say("sys", "4 in a Row: you resigned.")
            self._draw_board()

    def _play(self, col):
        if self.anim:
            return
        err = self.game.play(col)
        if err:
            self._flash(err)
            return
        self._animate()

    def _key_drop(self, e, col):
        if e.widget is self.entry or isinstance(e.widget, (tk.Entry, tk.Spinbox)):
            return
        self.nb.select(0)
        self._play(col)

    def _col_at(self, x):
        c = int((x - 10) // self.cell)
        return c if 0 <= c < connect4.COLS else None

    def _click(self, e):
        self.canvas.focus_set()
        c = self._col_at(e.x)
        if c is not None:
            self._play(c)

    def _motion(self, e):
        self._set_hover(self._col_at(e.x))

    def _set_hover(self, c):
        if c != self.hover_col:
            self.hover_col = c
            self._draw_board()

    def _flash(self, text):
        self.turn_lbl.configure(text=text, foreground=WARN)
        self.root.after(1600, self._draw_board)

    def _animate(self):
        """drop the last disc from the top to its row."""
        if not self.game.last:
            self._draw_board()
            return
        r, c = self.game.last
        y_end = self._cy(r)
        self.anim = [c, 26.0, y_end, self.game.board.cells[r][c]]
        self._anim_step()

    def _anim_step(self):
        c, y, y_end, who = self.anim
        y = min(y_end, y + max(12.0, (y - 20) * 0.35))
        self.anim[1] = y
        self._draw_board(falling=(c, y, who))
        if y < y_end:
            self.root.after(16, self._anim_step)
        else:
            self.anim = None
            self._draw_board()

    def _cx(self, c):
        return 10 + c * self.cell + self.cell / 2

    def _cy(self, r):
        return 34 + 10 + (connect4.ROWS - 1 - r) * self.cell + self.cell / 2

    def _draw_board(self, falling=None):
        g, cv = self.game, self.canvas
        cv.delete("all")
        cs, rad = self.cell, self.cell * 0.38
        w = connect4.COLS * cs + 20
        # header row: hover disc
        if g.my_turn() and self.hover_col is not None and not self.anim:
            x = self._cx(self.hover_col)
            cv.create_oval(x - rad * 0.8, 17 - rad * 0.8, x + rad * 0.8, 17 + rad * 0.8, fill=DISC[self.me], outline="")
        cv.create_rectangle(10, 44, w - 10, 44 + connect4.ROWS * cs, fill=BOARD_BG, outline="")
        if self.hover_col is not None and g.my_turn():
            x0 = 10 + self.hover_col * cs
            cv.create_rectangle(x0, 44, x0 + cs, 44 + connect4.ROWS * cs, fill="#2763ad", outline="")
        if falling:
            fc, fy, who = falling
            x = self._cx(fc)
            cv.create_oval(x - rad, fy - rad, x + rad, fy + rad, fill=DISC[who], outline="")
        win = set(g.line or [])
        for r in range(connect4.ROWS):
            for c in range(connect4.COLS):
                who = g.board.cells[r][c]
                if falling and (r, c) == g.last and self.anim:
                    who = 0
                x, y = self._cx(c), self._cy(r)
                fill = DISC[who] if who else HOLE
                cv.create_oval(x - rad, y - rad, x + rad, y + rad, fill=fill, outline="")
                if (r, c) in win:
                    cv.create_oval(x - rad - 3, y - rad - 3, x + rad + 3, y + rad + 3, outline="#ffffff", width=3)
                elif g.last == (r, c) and who and not self.anim:
                    cv.create_oval(x - 5, y - 5, x + 5, y + 5, fill="#ffffff", outline="")
        for c in range(connect4.COLS):
            cv.create_text(self._cx(c), 44 + connect4.ROWS * cs + 12, text=str(c + 1), fill=MUTED,
                           font=("Segoe UI", 9))
        cv.configure(height=44 + connect4.ROWS * cs + 26)
        # status
        if g.id is None:
            t, col = "No game yet: Ctrl+N to start", FG
        elif g.result:
            t = {"win": "You win!", "loss": f"Node {self.peer} wins", "draw": "Draw",
                 "resigned": "You resigned", "peer resigned": "They resigned: you win"}[g.result]
            col = OK if g.result in ("win", "peer resigned") else (MUTED if g.result == "draw" else BAD)
        elif g.my_turn():
            t, col = "Your turn", DISC[self.me]
        else:
            t, col = f"Node {self.peer}'s turn", MUTED
        self.turn_lbl.configure(text=t, foreground=col)
        self.score_lbl.configure(text=f"You {g.score[self.me]}  :  {g.score[self.peer]}  Node {self.peer}")

    # ================================================================== radio
    def toggle_radio(self):
        if self.radio.running:
            self.radio.stop()
            self.radio_btn.configure(text="Start radio  (F5)")
            self.health_lbl.configure(text="radio: stopped", fg=MUTED)
        else:
            if not self._take_freqs():
                return
            self.radio.start(self.f1, self.f2, self.atten.get(), self.gain.get())
            self.radio_btn.configure(text="Stop radio  (F5)")

    def _take_freqs(self):
        try:
            f1 = int(round(float(self.f1_var.get()) * 1e6))
            f2 = int(round(float(self.f2_var.get()) * 1e6))
        except ValueError:
            messagebox.showerror("Frequency", "F1 and F2 are numbers in MHz, e.g. 3300 and 3375")
            return False
        err, warn = config.check_freqs(f1, f2)
        if err:
            messagebox.showerror("Frequency", err)
            return False
        if (f1, f2) != (self.f1, self.f2):
            config.save_freqs(f1, f2)
            if warn:
                self.say("bad", warn)
            self.f1, self.f2 = f1, f2
            self._show_info()
            self.say("sys", f"F1 {f1 / 1e6:g} MHz, F2 {f2 / 1e6:g} MHz. Set the same on the other PC.")
            return "changed"
        return True

    def apply_rf(self):
        took = self._take_freqs()
        if not took:
            return
        if self.radio.running:
            rf = dict(tx_atten=self.atten.get(), rx_gain=self.gain.get())
            if took == "changed":
                rf.update(f1=self.f1, f2=self.f2)
            self.radio.set_rf(**rf)
            self.radio.update_settings(self.f1, self.f2, self.atten.get(), self.gain.get())

    # ================================================================== transfers table
    def _row(self, d, xid, name, size, status):
        vals = ("TX ↑" if d == "tx" else "RX ↓", name, media.human(size), "0%", "", status)
        if (d, xid) in self.rows:
            self.tree.item(self.rows[(d, xid)], values=vals)
        else:
            self.rows[(d, xid)] = self.tree.insert("", 0, values=vals)

    def _update(self, d, xid, **kw):
        item = self.rows.get((d, xid))
        if not item:
            return
        vals = list(self.tree.item(item, "values"))
        for k, i in (("name", 1), ("size", 2), ("progress", 3), ("rate", 4), ("status", 5)):
            if k in kw:
                vals[i] = kw[k]
        self.tree.item(item, values=vals)

    # ================================================================== events
    def _poll(self):
        try:
            for _ in range(300):
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(40, self._poll)

    def _handle(self, e):
        t = e["type"]
        if t == "link":
            self.say("sys", f"Link to node {self.peer} {'up' if e['up'] else 'DOWN'}")
        elif t == "listen_mode":
            self.say("sys", "This radio is missing ACKs while it transmits (its own transmitter deafens its "
                            "receiver): sending in bursts and listening in between for a while.")
        elif t == "tx_progress":
            if ("tx", e["xfer"]) in self.rows:
                pct = 100 * e["acked"] / e["total"]
                self.pbar["value"] = pct
                self._update("tx", e["xfer"], progress=f"{pct:.0f}%", rate=f"{e['rate_Bps'] / 1e3:.0f} KB/s",
                             status=f"sending, {e['retx']} resent")
        elif t == "tx_done":
            kind = e["meta"].get("kind")
            self.game_out.pop(e["xfer"], None)
            if kind != "game":
                self._mark(e["xfer"], "  ✓", "tick")
            if ("tx", e["xfer"]) in self.rows:
                self.pbar["value"] = 100
                self._update("tx", e["xfer"], progress="100%", rate=f"{e['rate_Bps'] / 1e3:.0f} KB/s",
                             status=f"delivered in {e['seconds']:.1f} s, {e['retx']} resent")
        elif t == "tx_failed":
            obj = self.game_out.pop(e["xfer"], None)
            if obj is not None:                      # a game message: send it again once the link is back
                self.game_retry.append(obj)
                return
            self._mark(e["xfer"], "  ✗ not delivered", "bad")
            self._update("tx", e["xfer"], status="FAILED: " + e["reason"])
        elif t == "rx_start":
            if e["total"] > 3:
                self._row("rx", e["xfer"], "(incoming)", e["total"] * 1009, "receiving")
        elif t == "rx_progress":
            if ("rx", e["xfer"]) in self.rows:
                pct = 100 * e["got"] / e["total"]
                self.pbar["value"] = pct
                self._update("rx", e["xfer"], progress=f"{pct:.0f}%", rate=f"{e['rate_Bps'] / 1e3:.0f} KB/s")
        elif t == "rx_done":
            self._rx_done(e)
        elif t == "health":
            text, level = health_text(e["h"])
            self.health_lbl.configure(text="radio: " + text, fg=BAD if level == "bad" else MUTED)
        elif t == "phy":
            line = e["line"]
            if line.startswith("[rf]"):
                self.say("sys", "radio: " + line[5:])
            elif any(k in line for k in ("starting radio", "exited", "Error", "error", "busy", "Traceback")):
                self.say("sys", "radio: " + line)
        elif t == "error":
            self.say("bad", "transport error: " + e["error"])

    def _rx_done(self, e):
        m, data = e["meta"], e["data"]
        kind = m.get("kind")
        if not e["ok"]:
            self.say("bad", "a received message failed its SHA-256 check and was dropped")
            return
        if kind == "game":
            n_before = len(self.game.moves)
            try:
                note = self.game.on_message(json.loads(data.decode()))
            except (ValueError, KeyError):
                note = None
            if note:
                self.say("sys", "4 in a Row: " + note)
                if "new game" in note:
                    self.nb.select(0)
            if len(self.game.moves) > n_before and not self.anim:
                self._animate()
            else:
                self._draw_board()
            return
        if kind == "text":
            self.say("peer", data.decode("utf-8", "replace"))
            return
        path = media.save_received(data, m, self._dir())
        self.last_path = path
        self.open_btn.configure(state="normal")
        rate = f"{e['seconds']:.1f} s, {e['rate_Bps'] / 1e3:.0f} KB/s"
        self._update("rx", e["xfer"], name=m.get("name", "?"), size=media.human(len(data)), progress="100%",
                     rate=f"{e['rate_Bps'] / 1e3:.0f} KB/s", status=f"received in {e['seconds']:.1f} s, SHA-256 OK")
        self.pbar["value"] = 100
        self.say("peer", f"{m.get('name')} ({media.human(len(data))}, {rate})")
        self._thumb(path, m.get("name", "file"), f"t{e['xfer']}r")
        self.preview_info.configure(text=f"{m.get('name')}  ·  {media.human(len(data))}  ·  {rate}")
        if kind == "image":
            try:
                if media.HAVE_PIL:
                    from PIL import Image, ImageTk
                    im = Image.open(path)
                    im.thumbnail((460, 460))
                    self.preview_img = ImageTk.PhotoImage(im)
                else:
                    self.preview_img = tk.PhotoImage(file=path)
                self.preview.configure(image=self.preview_img, text="")
            except Exception:
                self.preview.configure(image="", text="(pip install pillow to preview)")
        else:
            self.preview.configure(image="", text="♫ audio received" if kind == "audio" else "file received")
            if kind == "audio" and self.autoplay.get():
                media.open_file(path)

    def _tick(self):
        tr = self.tr
        state = tr.link_state
        why = self.radio.watchdog(tr.link_up)
        if why:
            self.say("bad", f"radio restarted automatically: {why}")
        if state == "up":
            self.dot.configure(fg=OK)
            rtt = tr.current_rtt
            self.link_lbl.configure(text="Link up" + (f"  ·  {rtt * 1000:.0f} ms" if rtt else ""))
        elif state == "one-way":
            self.dot.configure(fg=WARN)
            self.link_lbl.configure(text=f"One-way: node {self.peer} can't hear us")
        else:
            self.dot.configure(fg=BAD)
            self.link_lbl.configure(text="Link down")
        qo, qi = tr.current_quality_out, tr.current_quality_in
        parts = []
        if qo is not None or qi is not None:
            f = lambda q: "-" if q is None else f"{100 * q:.0f}%"
            parts.append(f"frames getting through:  to node {self.peer} {f(qo)}  ·  from node {self.peer} {f(qi)}")
        if tr.listen_mode:
            parts.append("burst + listen mode")
        self.qual_lbl.configure(text="   ".join(parts))
        if self.radio.proc is not None and not self.radio.running:
            self.radio_btn.configure(text="Start radio  (F5)")
        if self.game_retry and state == "up":
            for obj in self.game_retry:
                self._send_game(obj)
            self.game_retry = []
        self.root.after(500, self._tick)

    def close(self):
        self.radio.stop()
        self.tr.stop()
        self.root.destroy()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", type=int, choices=(1, 2), required=True)
    ap.add_argument("--profile", choices=list(config.PROFILES), default=config.DEFAULT_PROFILE)
    ap.add_argument("--duplex", choices=config.DUPLEX_MODES, default=config.DEFAULT_DUPLEX,
                    help="fdd (default): both ways at once, silent when idle; fdd-cont; tdd")
    ap.add_argument("--f1", type=float, default=None, metavar="MHZ", help="node 1 TX / node 2 RX (remembered)")
    ap.add_argument("--f2", type=float, default=None, metavar="MHZ", help="node 2 TX / node 1 RX (remembered)")
    ap.add_argument("--start-phy", action="store_true", help="start the radio with the app")
    ap.add_argument("--same-pc", action="store_true", help="both nodes on one PC (tools/fake_radio.py, tests)")
    ap.add_argument("--tx-port", type=int, default=None)
    ap.add_argument("--rx-port", type=int, default=None)
    ap.add_argument("--ctrl-port", type=int, default=None)
    ap.add_argument("phy_args", nargs="*", help="extra radio options after --")
    a = ap.parse_args()
    a.f1, a.f2 = config.freqs_from_args(a.f1, a.f2)
    root = tk.Tk()
    App(root, a)
    root.mainloop()


if __name__ == "__main__":
    main()
