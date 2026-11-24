"""
The link engine behind the app (no UI): radio process + watchdog, encrypted transport, a
reliable outbox for app messages, settings, and the telecom telemetry for the hidden panel.

    node = Node(1, pairing, Settings.load(), on_event=callback)
    node.start()
    node.send_msg("chat", {...})        # reliable; sent again when the link is back
    node.send_data(data, meta)          # one verified transfer (files.py sends blocks this way)

Events (callback from background threads; the UI hands them to its own thread):
    link {state}                 'up' / 'one-way' / 'down'
    msg {kind, obj}              app message from the peer (kind: chat, game, file)
    file {meta, data, xfer}      a transfer that is not a JSON message (file blocks)
    sent {xfer, meta, frames, retx, seconds} ;  failed {xfer, meta, reason}
    progress {xfer, pct}         sending ;  rx_progress {xfer, bytes}   receiving
    radio {text, level}          radio health ;  restarted {why}

Telemetry: one sample per second (Node.samples, last 15 min) with every number of the
link and the radio, also written to data/telemetry/YYYY-MM-DD.csv.
"""
import collections
import csv
import json
import os
import threading
import time

import config
import secure
from radio import Radio, health_text
from transport import CHUNK, Transport


class Settings:
    """data/settings.json: node number, radio settings, folder for received files."""
    DEFAULTS = dict(node=None, profile=config.DEFAULT_PROFILE, duplex=config.DEFAULT_DUPLEX,
                    f1_mhz=config.F1_DEFAULT / 1e6, f2_mhz=config.F2_DEFAULT / 1e6,
                    tx_atten=config.DEFAULT_TX_ATTEN, rx_gain=config.DEFAULT_RX_GAIN, recv_dir=None)

    def __init__(self, path, d):
        self.path = path
        self.__dict__.update({**self.DEFAULTS, **{k: v for k, v in d.items() if k in self.DEFAULTS}})

    @classmethod
    def load(cls, data_dir=config.DATA_DIR):
        path = os.path.join(data_dir, "settings.json")
        try:
            with open(path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        return cls(path, d)

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        d = {k: getattr(self, k) for k in self.DEFAULTS}
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, self.path)

    @property
    def f1(self):
        return int(round(self.f1_mhz * 1e6))

    @property
    def f2(self):
        return int(round(self.f2_mhz * 1e6))


TELEMETRY_FIELDS = [
    "time", "link", "beacon_rtt_ms", "srtt_ms", "min_rtt_ms", "rto_ms", "queue_delay_ms",
    "pacing_KBps", "pacing_max_KBps", "air_out_KBps", "air_in_KBps", "goodput_out_KBps", "goodput_in_KBps",
    "frames_out_ps", "frames_in_ps", "data_frames_out_ps", "acks_in_ps", "retx_ps", "rto_events",
    "dup_frames_in", "frames_through_to_peer", "frames_through_from_peer", "first_try_delivery",
    "frame_payload_B", "listen_mode", "transfers_out", "transfers_in",
    "rejected_total", "rejected_wrong_address", "rejected_same_node", "rejected_failed_check",
    "rejected_replay", "radio_rx_x", "radio_tx_x", "radio_overflows", "radio_underflows",
    "radio_tx_queue", "radio_dropped", "radio_bursts", "radio_restarts",
]


class Node:
    def __init__(self, node, pairing, settings, on_event=None, data_dir=config.DATA_DIR,
                 ports=None, start_radio=True, extra_phy=()):
        self.node = int(node)
        self.peer_node = 3 - self.node
        self.pairing = pairing
        self.s = settings
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.on_event = on_event or (lambda e: None)
        self.log = collections.deque(maxlen=2000)
        self.crypto = secure.LinkCrypto(pairing, self.node, os.path.join(data_dir, f".link_node{self.node}.state"))
        self.rate = config.get_profile(settings.profile)
        if ports is None:
            n = config.NODES[self.node]
            ports = (n["tx_port"], n["rx_port"], n["ctrl_port"])
        self.ports = ports
        self.tr = Transport(self.node, self.peer_node, ports[0], ports[1], self.rate,
                            on_event=self._on_transport, duplex=settings.duplex, crypto=self.crypto)
        self.radio = Radio(self.node, settings.profile, settings.duplex, ports,
                           on_line=self._radio_line, on_health=self._radio_health,
                           extra_args=list(extra_phy))
        self.start_radio = start_radio
        self.health = {}
        self.t_health = 0.0
        self.health_txt = ("radio starting", "ok")
        self._radio_totals = collections.Counter()
        self._out = {}                  # xfer -> (kind, obj, retry)
        self._retry = []                # messages to send again once the link is back
        self._lock = threading.Lock()
        self._state = "down"
        self._ever_up = False
        self._running = False
        self.samples = collections.deque(maxlen=900)
        self._prev = None
        self.transfers = collections.deque(maxlen=300)   # finished transfers, radio-level numbers
        self.t_heard_never = time.monotonic()

    # ------------------------------------------------------------------ life
    def start(self):
        self._running = True
        self.tr.start()
        if self.start_radio:
            self.radio.start(self.s.f1, self.s.f2, self.s.tx_atten, self.s.rx_gain)
        threading.Thread(target=self._keeper, name="link-keeper", daemon=True).start()
        self.note(f"started as node {self.node} {secure.addr_text(self.crypto.me)}, peer "
                  f"{secure.addr_text(self.crypto.peer)}, pairing code {secure.fingerprint(self.pairing)}, "
                  f"{self.s.profile} {self.s.duplex}")

    def stop(self):
        self._running = False
        self.radio.stop()
        self.tr.stop()

    def note(self, text):
        self.log.append(time.strftime("%H:%M:%S ") + text)

    def _emit(self, **e):
        try:
            self.on_event(e)
        except Exception as ex:
            self.note(f"event handler error: {ex}")

    # ------------------------------------------------------------------ sending
    def send_msg(self, kind, obj, retry=True):
        """small reliable message; with retry it is sent again when the link comes back."""
        data = json.dumps(obj, separators=(",", ":")).encode()
        with self._lock:
            xid = self.tr.send_bytes(data, {"kind": kind})
            self._out[xid] = (kind, obj, retry)
        return xid

    def send_data(self, data, meta):
        with self._lock:
            xid = self.tr.send_bytes(data, meta)
            self._out[xid] = (meta.get("kind"), None, False)
        return xid

    # ------------------------------------------------------------------ radio settings
    def apply_rf(self, f1_mhz=None, f2_mhz=None, tx_atten=None, rx_gain=None):
        """live change from the hidden panel (saved, kept across restarts)."""
        rf = {}
        if f1_mhz is not None:
            self.s.f1_mhz, rf["f1"] = f1_mhz, int(round(f1_mhz * 1e6))
        if f2_mhz is not None:
            self.s.f2_mhz, rf["f2"] = f2_mhz, int(round(f2_mhz * 1e6))
        if tx_atten is not None:
            self.s.tx_atten = rf["tx_atten"] = float(tx_atten)
        if rx_gain is not None:
            self.s.rx_gain = rf["rx_gain"] = float(rx_gain)
        self.s.save()
        self.radio.update_settings(self.s.f1, self.s.f2, self.s.tx_atten, self.s.rx_gain)
        if self.radio.running and rf:
            self.radio.set_rf(**rf)
        self.note(f"radio settings changed: {rf}")

    def restart_radio(self):
        self.note("radio restarted from the panel")
        self.radio.stop()
        self.radio.start(self.s.f1, self.s.f2, self.s.tx_atten, self.s.rx_gain)

    # ------------------------------------------------------------------ internals
    def _radio_line(self, line):
        self.note("radio: " + line)

    def _radio_health(self, h):
        self.health, self.t_health = h, time.monotonic()
        for k in ("overflows", "underflows"):
            self._radio_totals[k] += h.get(k, 0)
        self.health_txt = health_text(h)
        self._emit(type="radio", text=self.health_txt[0], level=self.health_txt[1])

    def _on_transport(self, e):
        t = e["type"]
        if t == "rx_done":
            meta = e["meta"]
            kind = meta.get("kind")
            if not e["ok"]:
                self.note("a received transfer failed its SHA-256 check and was dropped")
                return
            if kind in ("chat", "game", "file") and len(e["data"]) < 64000:
                try:
                    obj = json.loads(e["data"].decode())
                except ValueError:
                    return
                self._emit(type="msg", kind=kind, obj=obj)
            else:
                self._emit(type="file", meta=meta, data=e["data"], xfer=e["xfer"])
            self.transfers.append(dict(time=time.time(), dir="in", kind=kind, bytes=len(e["data"]),
                                       seconds=round(e["seconds"], 3), rate=round(e["rate_Bps"]),
                                       dups=e.get("dups", 0)))
        elif t == "tx_done":
            with self._lock:
                self._out.pop(e["xfer"], None)
            self._emit(type="sent", xfer=e["xfer"], meta=e["meta"], frames=e["frames"], retx=e["retx"],
                       seconds=e["seconds"])
            self.transfers.append(dict(time=time.time(), dir="out", kind=e["meta"].get("kind"),
                                       bytes=e["bytes"], seconds=round(e["seconds"], 3),
                                       rate=round(e["rate_Bps"]), frames=e["frames"], retx=e["retx"]))
        elif t == "tx_failed":
            with self._lock:
                item = self._out.pop(e["xfer"], None)
                if item and item[2]:
                    self._retry.append(item)
                    return
            self._emit(type="failed", xfer=e["xfer"], meta=e["meta"], reason=e["reason"])
            self.note(f"not delivered: {e['meta'].get('name', e['meta'].get('kind'))} ({e['reason']})")
        elif t == "tx_progress":
            self._emit(type="progress", xfer=e["xfer"], pct=100 * e["acked"] / max(1, e["total"]))
        elif t == "rx_progress":
            self._emit(type="rx_progress", xfer=e["xfer"], bytes=e["got"] * CHUNK)
        elif t == "listen_mode":
            self.note("this radio misses ACKs while it transmits: burst + listen mode for 15 s")
        elif t == "error":
            self.note("transport error: " + e["error"])

    def _keeper(self):
        """once a second: link state, watchdog (auto-restart), resend queued messages, telemetry."""
        while self._running:
            time.sleep(1.0)
            tr = self.tr
            st = tr.link_state
            if st != self._state:
                self._state = st
                self._ever_up |= st == "up"
                self.note(f"link {st}")
                self._emit(type="link", state=st)
            if st == "up" and self._retry:
                with self._lock:
                    again, self._retry = self._retry, []
                for kind, obj, _ in again:
                    self.send_msg(kind, obj)
            if self.start_radio:
                why = self.radio.watchdog(tr.link_up)
                if why:
                    self.note(f"radio restarted automatically: {why}")
                    self._emit(type="restarted", why=why)
            try:
                self._sample()
            except Exception as ex:
                self.note(f"telemetry error: {ex}")

    # ------------------------------------------------------------------ telemetry
    def _sample(self):
        now = time.monotonic()
        tr, c, h = self.tr, self.crypto.stats, self.health
        st = dict(tr.stats)
        tl = tr.telemetry()
        prev, self._prev = self._prev, (now, st)
        dt = (now - prev[0]) if prev else 1.0
        d = (lambda k: (st.get(k, 0) - prev[1].get(k, 0)) / dt) if prev else (lambda k: 0.0)
        ms = lambda x: None if x is None else round(1000 * x, 1)
        fresh = now - self.t_health < 3
        s = dict(
            time=time.strftime("%H:%M:%S"), link=tl["state"], beacon_rtt_ms=ms(tl["beacon_rtt"]),
            srtt_ms=ms(tl["srtt"]), min_rtt_ms=ms(tl["min_rtt"]), rto_ms=ms(tl["rto"]),
            queue_delay_ms=ms(tl["queue_delay"]), pacing_KBps=round(tl["rate"] / 1e3, 1),
            pacing_max_KBps=round(tl["rate_max"] / 1e3, 1),
            air_out_KBps=round(d("tx_bytes") / 1e3, 1), air_in_KBps=round(d("rx_bytes") / 1e3, 1),
            goodput_out_KBps=round(d("acked_bytes") / 1e3, 1), goodput_in_KBps=round(d("rx_new_bytes") / 1e3, 1),
            frames_out_ps=round(d("tx_data") + d("tx_ctrl") + d("small_tx"), 1),
            frames_in_ps=round(d("rx_data") + d("rx_ack"), 1), data_frames_out_ps=round(d("tx_data"), 1),
            acks_in_ps=round(d("rx_ack"), 1), retx_ps=round(d("retx"), 1), rto_events=st.get("rto", 0),
            dup_frames_in=st.get("rx_dup", 0),
            frames_through_to_peer=None if tl["q_out"] is None else round(tl["q_out"], 3),
            frames_through_from_peer=None if tl["q_in"] is None else round(tl["q_in"], 3),
            first_try_delivery=None if tl["first_try"] is None else round(tl["first_try"], 3),
            frame_payload_B=tl["chunk"], listen_mode=int(tl["listen_mode"]),
            transfers_out=tl["active_tx"] + tl["pending_tx"], transfers_in=tl["active_rx"],
            rejected_total=c["rejected"], rejected_wrong_address=c["foreign"],
            rejected_same_node=c["same_node"], rejected_failed_check=c["auth"], rejected_replay=c["replay"],
            radio_rx_x=h.get("speed") if fresh else None, radio_tx_x=h.get("tx_speed") if fresh else None,
            radio_overflows=self._radio_totals["overflows"], radio_underflows=self._radio_totals["underflows"],
            radio_tx_queue=h.get("queue") if fresh else None, radio_dropped=h.get("dropped"),
            radio_bursts=h.get("bursts"), radio_restarts=self.radio.restarts,
        )
        self.samples.append(s)
        self._write_csv(s)

    def _write_csv(self, s):
        folder = os.path.join(self.data_dir, "telemetry")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, time.strftime("%Y-%m-%d") + f"_node{self.node}.csv")
        new = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, TELEMETRY_FIELDS)
            if new:
                w.writeheader()
            w.writerow(s)

    def diagnosis(self):
        """plain-language reason the link is not up (or None), for the main screen."""
        if self.tr.link_state == "up":
            return None
        c = self.crypto.stats
        if c["same_node"] > 0 and self.crypto.recent_rejects(15) > 0:
            return ("Both PCs are set to the same node",
                    f"This PC is node {self.node}. Set the other PC to node {self.peer_node} "
                    "(Settings, Switch node).")
        if c["foreign"] > 0 and self.crypto.recent_rejects(15) > 0:
            return ("The other PC uses a different pairing key",
                    f"Its frames arrive but don't match this pairing code ({secure.fingerprint(self.pairing)}). "
                    "Load the same pairing key on both PCs (Settings).")
        if self.start_radio and not self.radio.running:
            return ("This PC's radio isn't running", "It restarts by itself. Check that the E200 is plugged in "
                                                    "and switched on. Ctrl+Shift+D shows why it stopped.")
        if self.start_radio and time.monotonic() - self.t_health > 8:
            return ("This PC's radio isn't ready yet", "Check that the E200 is plugged in and switched on.")
        if self.tr.link_state == "one-way":
            return ("The other PC can't hear this one", "Its radio may be too far away, or this PC's radio "
                                                        "isn't sending. The link keeps trying.")
        if self._ever_up:
            return ("Lost contact with the other PC",
                    "The link comes back by itself as soon as the radios hear each other again, and files on "
                    "the way continue where they stopped. Is the other PC still running, with its E200 on?")
        return ("Nothing heard from the other PC yet",
                f"Start SDR Link on the other PC (node {self.peer_node}) with the same pairing code "
                f"{secure.fingerprint(self.pairing)}. The link connects by itself.")

    def overview(self):
        """grouped key numbers for the hidden panel."""
        tr, c, s = self.tr, self.crypto, self.s
        tl = tr.telemetry()
        last = self.samples[-1] if self.samples else {}
        tx, rx = config.node_freqs(self.node, s.f1, s.f2)
        p = config.PROFILES[s.profile]
        f = lambda q: "-" if q is None else f"{100 * q:.1f} %"
        ms = lambda x: "-" if x is None else f"{1000 * x:.0f} ms"
        h = self.health
        return [
            ("Node", [("This PC", f"node {self.node}, address {secure.addr_text(c.me)}"),
                      ("Other PC", f"node {self.peer_node}, address {secure.addr_text(c.peer)}"),
                      ("Pairing code", f"{secure.fingerprint(self.pairing)} (AES-256-GCM, replay protected)")]),
            ("Radio", [("TX / RX", f"{tx / 1e6:.3f} MHz / {rx / 1e6:.3f} MHz"),
                       ("Modulation", f"BPSK, {p['samp_rate'] / 1e6:g} Msps, {p['sps']} samples/symbol, "
                                      f"{self.rate.bit_rate / 1e6:.2f} Mbit/s raw ({s.profile})"),
                       ("RF bandwidth", f"{config.rf_bandwidth(p['samp_rate'], p['sps']) / 1e6:.2f} MHz"),
                       ("Duplex", s.duplex + ("  (burst + listen now)" if tl["listen_mode"] else "")),
                       ("TX atten / RX gain", f"{s.tx_atten:g} dB / {s.rx_gain:g} dB"),
                       ("Radio health", self.health_txt[0]),
                       ("TX queue / dropped", f"{h.get('queue', '-')} frames / {h.get('dropped', '-')}"),
                       ("Overflows / underflows", f"{self._radio_totals['overflows']} / "
                                                  f"{self._radio_totals['underflows']} since start"),
                       ("Automatic restarts", str(self.radio.restarts))]),
            ("Link", [("State", tl["state"]),
                      ("Round trip", f"beacon {ms(tl['beacon_rtt'])}, data {ms(tl['srtt'])}, "
                                     f"min {ms(tl['min_rtt'])}, timeout {ms(tl['rto'])}"),
                      ("Speed out", f"{last.get('goodput_out_KBps', 0)} KB/s data, "
                                    f"{last.get('air_out_KBps', 0)} KB/s on the air"),
                      ("Speed in", f"{last.get('goodput_in_KBps', 0)} KB/s data, "
                                   f"{last.get('air_in_KBps', 0)} KB/s on the air"),
                      ("Pacing", f"{tl['rate'] / 1e3:.1f} of {tl['rate_max'] / 1e3:.1f} KB/s, "
                                 f"queueing {ms(tl['queue_delay'])}"),
                      ("Frames through", f"to the other PC {f(tl['q_out'])}, from it {f(tl['q_in'])}"),
                      ("First-try delivery", f"{f(tl['first_try'])}, frame payload {tl['chunk']} B"),
                      ("Frames", f"out {last.get('frames_out_ps', 0)}/s, in {last.get('frames_in_ps', 0)}/s, "
                                 f"resent {last.get('retx_ps', 0)}/s"),
                      ("Totals", f"{tr.stats['tx_data']} data + {tr.stats['tx_ctrl']} control frames out, "
                                 f"{tr.stats['retx']} resent, {tr.stats['rto']} timeouts, "
                                 f"{tr.stats['rx_dup']} duplicates in"),
                      ("Rejected frames", f"{c.stats['rejected']} (other pairing {c.stats['foreign']}, "
                                          f"same node {c.stats['same_node']}, failed check {c.stats['auth']}, "
                                          f"replay {c.stats['replay']})")]),
        ]
