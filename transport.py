"""
Transport layer: Selective Repeat ARQ over the GNU Radio PHY (UDP <-> Socket PDU).

Design (what to say in the viva)
--------------------------------
* Full duplex: each node TXes on one frequency and RXes on the other, so DATA and
  ACKs never collide. Both nodes can send files to each other at the same time.
* Selective Repeat with SACK: every ACK carries `cum` (all seq < cum received) plus a
  bitmap of the next 512 sequence numbers. Each ACK is a complete snapshot of the
  receiver state, so a lost ACK costs nothing - the next one repairs it.
* Loss detection without timeouts (RACK-style): the PHY delivers frames in order
  (FIFO). So if a frame sent AFTER frame s is acknowledged and s is not, s was lost
  -> retransmit at once. The adaptive RTO (RFC 6298 srtt/rttvar) only handles
  tail losses (last frames of a file / lost final ACK).
* Unambiguous timing: each DATA frame carries its transmission number (txn) and each
  ACK echoes the (seq, txn) of the frame that triggered it. So every ACK gives an
  exact RTT sample and an exact RACK reference, even for retransmitted frames
  (no Karn's-algorithm ambiguity / deadlock when RTT exceeds the RTO).
* Pacing: frames are released at the PHY bit rate (token bucket), so the GNU Radio
  queue stays short and RTT stays low. No time.sleep(0.008) guesses. A delay-based
  controller (like TCP Vegas) backs the rate off if RTT rises above the minimum RTT,
  i.e. if frames are queueing because the PC/radio can't drain at the nominal rate.
* Delayed/batched ACKs: one ACK per 8 frames or 5 ms, immediate on completion.
* Binary end to end - no base64 (base64 adds 33% airtime). Integrity: PHY CRC32 per
  frame + SHA-256 over the whole file.
* duplex="fdd" (quiet FDD, default): both directions at once, the radio is silent when
  idle. For an E200 whose own transmitter deafens its receiver:
    - the receiver ACKs per 32 KB (not per frame) plus once when the sender pauses, so a
      deaf receiver loses few incoming frames to its own ACKs;
    - a sender that stops hearing ACKs while it transmits (it is the deaf one) switches to
      "burst, then listen" (as TDD) for a while, so the ACKs reach it.
* Link quality: every beacon carries the number of frames the node has sent and the
  share of the peer's frames it received, so both sides see the delivery ratio of BOTH
  directions. A lossy direction gets shorter frames (1009 -> 500 -> 240 bytes).
* Small messages (chat lines, game moves) jump the queue: priority frame type, sent twice
  back to back (a frame alone after silence is decoded less reliably than one right behind
  another), resent after ~1.5 RTT without an ACK, never wait behind the file transfers.
* duplex="fdd-cont": the previous FDD (idle symbols sent continuously).
* duplex="tdd" (half-duplex, time division) for radios whose own transmitter deafens
  their receiver: the sender transmits a BURST of frames then goes quiet and listens;
  the receiver never transmits while frames are arriving and ACKs once the channel
  goes quiet; the sender resumes as soon as the ACK for the burst's last frame
  arrives. Burst size and listen window adapt to the measured RTT. The PHY sends
  silence between frames in this mode. duplex="fdd" = both directions at once.

Frame formats (big-endian), first byte MAGIC so the PHY framer can prioritise ACKs:
  DATA : B magic | B type=1 | B src | B dst | H xfer | I seq | I total | B txn | payload
  ACK  : B magic | B type=2 | B src | B dst | H xfer | I cum | I nrecv | B flags
         | I echo_seq | B echo_txn | bitmap
  HELLO: B magic | B type=3 | B src | B dst | H 0    | d t_send | d t_echo | d echo_age
         | I frames sent by this node | H share of the peer's frames received (1/1000, 0xFFFF = unknown)
  CANCEL:B magic | B type=4 | B src | B dst | H xfer

A transfer's byte stream ("body") = H meta_len | meta JSON | file bytes.
"""
import collections
import hashlib
import json
import os
import random
import select
import socket
import struct
import sys
import threading
import time
import queue

import config

MAGIC = 0xA5
T_DATA, T_ACK, T_HELLO, T_CANCEL = 1, 2, 3, 4
T_DATA_P = 5            # DATA of a small message (chat line, game move): the radio's framer
                        # sends it ahead of queued file data (it treats every type != 1 as priority)
SMALL_BODY = 480        # a transfer this small is at most 2 frames even with 240-byte frames
BROADCAST = 0xFF

DATA_HDR = struct.Struct(">BBBBHIIB")      # 15 bytes
ACK_HDR = struct.Struct(">BBBBHIIBIB")     # 20 bytes + bitmap
HELLO = struct.Struct(">BBBBHdddIH")
CANCEL = struct.Struct(">BBBBH")
COMMON = struct.Struct(">BBBBH")

SEC_OVERHEAD = 32       # secure.OVERHEAD: address/counter header + AES-GCM tag on every frame
CHUNK = config.PHY_MTU - DATA_HDR.size - SEC_OVERHEAD   # 977 bytes of file per frame (largest)

CHUNKS = ((0.90, CHUNK), (0.70, 500), (0.0, 240))   # frame size by delivery ratio of the direction
BITMAP_BYTES = 64                          # SACK window: 512 frames beyond cum
FLAG_COMPLETE = 0x01


class Params:
    window = 384                 # max frames in flight per transfer (<= 8*BITMAP_BYTES)
    ack_every = 8                # receiver: ACK after this many new frames ...
    ack_delay = 0.005            # ... or this many seconds after the first un-ACKed one
    min_rto = 0.15
    max_rto = 2.0
    init_rto = 0.6
    rto_burst = 16               # frames retransmitted per RTO expiry
    stall_timeout = 20.0         # abort transfer if no ACK progress for this long
    hello_interval = 0.5
    link_timeout = 3.0
    pace_factor = 0.98           # fraction of PHY bit rate used by the sender
    queue_hi = 0.25              # smoothed queueing delay (srtt - min RTT) -> slow down
    queue_lo = 0.10              # below this -> speed back up towards PHY rate
    max_concurrent_tx = 4
    rate_floor = 0.3             # the delay valve never slows the sender below this share
    # quiet FDD
    fdd_ack_bytes = 32000        # receiver: ACK per this much new data ...
    fdd_ack_frames = 64          # ... or this many frames
    ack_starve = 0.4             # sender: no ACK for max(this, 4 x RTT) while sending -> listen mode
    listen_mode_hold = 15.0      # stay in "burst, then listen" this long after starving


def _now():
    return time.perf_counter()


# ============================================================================ sender
class TxTransfer:
    def __init__(self, xfer, dst, body, meta, params, chunk=CHUNK):
        self.xfer, self.dst, self.meta, self.p = xfer, dst, meta, params
        self.body = body
        self.chunk_size = chunk
        self.total = max(1, (len(body) + chunk - 1) // chunk)
        self.acked = bytearray(self.total)
        self.n_acked = 0
        self.cum = 0                     # all seq < cum acked
        self.next_new = 0
        self.last_tx = [0.0] * self.total
        self.tx_count = bytearray(self.total)
        self.inflight = collections.deque()   # (tx_time, seq) in send order
        self.retx = collections.deque()
        self.in_retx = bytearray(self.total)
        self.t_start = None
        self.t_progress = _now()
        self.frames_sent = 0
        self.retransmissions = 0
        self.last_newly = 0
        self.saw_drop = False
        self.done = False
        self.failed = None

    def chunk(self, seq):
        c = self.chunk_size
        return self.body[seq * c:(seq + 1) * c]

    def has_new(self):
        return self.next_new < self.total and self.next_new - self.cum < self.p.window

    def has_work(self):
        return bool(self.retx) or self.has_new()

    def next_seq(self):
        while self.retx:
            s = self.retx.popleft()
            self.in_retx[s] = 0
            if not self.acked[s]:
                return s
        if self.has_new():
            s = self.next_new
            self.next_new += 1
            return s
        return None

    def mark_sent(self, seq, t):
        if self.t_start is None:
            self.t_start = t
        if self.tx_count[seq]:
            self.retransmissions += 1
        self.tx_count[seq] = self.tx_count[seq] % 255 + 1
        self.last_tx[seq] = t
        self.inflight.append((t, seq))
        self.frames_sent += 1

    def queue_retx(self, s):
        if not self.acked[s] and not self.in_retx[s]:
            self.in_retx[s] = 1
            self.retx.append(s)

    def on_ack(self, cum, bitmap, echo_seq, echo_txn, now):
        """Returns an RTT sample (or None)."""
        newly = []
        cum = min(cum, self.total)
        for s in range(self.cum, cum):
            if not self.acked[s]:
                self.acked[s] = 1
                newly.append(s)
        for i, byte in enumerate(bitmap):
            if not byte:
                continue
            for b in range(8):
                if byte & (1 << b):
                    s = cum + i * 8 + b
                    if s < self.total and not self.acked[s]:
                        self.acked[s] = 1
                        newly.append(s)
        while self.cum < self.total and self.acked[self.cum]:
            self.cum += 1
        self.last_newly = len(newly)
        if newly:
            self.n_acked += len(newly)
            self.t_progress = now
            if self.n_acked >= self.total:
                self.done = True
        # The ACK echoes the exact transmission that triggered it. If that is the
        # latest transmission of echo_seq we know its send time precisely.
        if echo_seq >= self.total or self.tx_count[echo_seq] != echo_txn:
            return None
        t_echo = self.last_tx[echo_seq]
        # RACK: FIFO channel -> anything sent before t_echo and still unacked is lost
        dq = self.inflight
        while dq and dq[0][0] < t_echo:
            t, s = dq.popleft()
            if not self.acked[s] and self.last_tx[s] == t:
                self.queue_retx(s)
        return now - t_echo

    def check_rto(self, now, rto):
        """Tail-loss recovery. Returns True if an RTO fired."""
        dq = self.inflight
        while dq and (self.acked[dq[0][1]] or self.last_tx[dq[0][1]] != dq[0][0]):
            dq.popleft()
        if not dq or now - dq[0][0] < rto:
            return False
        fired = 0
        while dq and fired < self.p.rto_burst and now - dq[0][0] >= rto:
            t, s = dq.popleft()
            if not self.acked[s] and self.last_tx[s] == t:
                self.queue_retx(s)
                fired += 1
        return fired > 0


# ========================================================================== receiver
class RxTransfer:
    def __init__(self, src, xfer, total):
        self.src, self.xfer, self.total = src, xfer, total
        self.chunks = [None] * total
        self.got = bytearray(total)
        self.n = 0
        self.cum = 0
        self.unacked = 0
        self.first_unacked_t = None
        self.echo = (0, 0)          # (seq, txn) of the most recent arrival
        self.unacked_bytes = 0
        self.quiet_acked = True     # an ACK went out since the sender last paused
        self.t_dup_ack = 0.0
        self.t_start = _now()
        self.t_last = self.t_start
        self.bytes = 0
        self.dups = 0
        self.done = False

    def add(self, seq, payload):
        if seq >= self.total:
            return False
        self.t_last = _now()
        if self.got[seq]:
            self.dups += 1
            return False
        self.got[seq] = 1
        self.chunks[seq] = payload
        self.n += 1
        self.bytes += len(payload)
        while self.cum < self.total and self.got[self.cum]:
            self.cum += 1
        if self.n == self.total:
            self.done = True
        return True

    def ack_bitmap(self):
        bm = bytearray(BITMAP_BYTES)
        last = -1
        base = self.cum
        for i in range(min(BITMAP_BYTES * 8, self.total - base)):
            if self.got[base + i]:
                bm[i >> 3] |= 1 << (i & 7)
                last = i >> 3
        return bytes(bm[:last + 1])

    def body(self):
        return b"".join(self.chunks)


# ========================================================================= transport
class Transport:
    """
    events -> callback(dict). Event 'type's:
      link {up: bool}
      tx_progress {xfer, acked, total, bytes, rate_Bps, retx}
      tx_done {xfer, meta, seconds, bytes, rate_Bps, retx, frames}
      tx_failed {xfer, meta, reason}
      rx_start {xfer, src, total}
      rx_progress {xfer, got, total, rate_Bps}
      rx_done {xfer, src, meta, data, seconds, rate_Bps, ok}
    """

    def __init__(self, node_id, peer_id, tx_port, rx_port, phy_rate, on_event=None,
                 params=None, host="127.0.0.1", duplex="fdd", crypto=None):
        self.me, self.peer = node_id, peer_id
        self.crypto = crypto               # secure.LinkCrypto: every frame sealed / opened
        self.mode = duplex
        self.tdd = (duplex == "tdd")
        self.quiet = (duplex == "fdd")
        self._hybrid_until = 0.0           # quiet FDD: "burst, then listen" until this time
        self._last_ack_rx = 0.0
        self._sending_since = None
        self._last_data_tx = -1e9
        # link quality (frame counters exchanged in beacons)
        self._sent_frames = 0
        self._rx_frames = 0
        self._q_samples = collections.deque()   # (t, peer frames sent, our frames received)
        self.quality_in = None             # share of the peer's frames we receive
        self._q_in_t = 0.0
        self.quality_out = None            # share of our frames the peer receives (its report)
        self._q_out_t = 0.0
        self._data_delivery = None         # first-try delivery of our DATA frames (recent transfers)
        # TDD state
        self._frame_air = phy_rate.frame_airtime(config.PHY_MTU)
        self._quiet = max(3 * self._frame_air, 0.02)  # no frame for this long = burst over
        self._last_data_rx = -1e9
        self._burst_n = 0
        self._burst_last = None            # (xfer, seq, txn) of last frame in burst
        self._listen_until = None
        self._pending_done = {}            # (src, xfer) -> [total, seq, txn, repeats]
        self.p = params or Params()
        self.phy = phy_rate
        self.on_event = on_event or (lambda e: None)
        self.tx_addr = (host, tx_port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for opt in (socket.SO_RCVBUF, socket.SO_SNDBUF):
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, opt, 4 * 1024 * 1024)
            except OSError:
                pass
        # WSAECONNRESET (10054) on a UDP socket is NOT a radio/buffer crash: Windows
        # reports an ICMP "port unreachable" (e.g. the GNU Radio flowgraph is not
        # running yet) as an error on the NEXT recv(). The socket stays usable, so the
        # receive loop simply catches ConnectionResetError and carries on (see _loop).
        # (Python's socket.ioctl() can't set SIO_UDP_CONNRESET, so we don't try.)
        self.sock.bind((host, rx_port))
        self.sock.setblocking(False)

        self._jobs = queue.Queue()
        self._active = []          # TxTransfer
        self._pending = collections.deque()
        self._rx = {}              # (src, xfer) -> RxTransfer
        self._completed = {}       # (src, xfer) -> (total, t)
        self._xfer_id = random.randint(1, 0xFFFF)
        self._rr = 0
        # pacing (token bucket in bytes)
        self._rate_max = self.phy.byte_rate * self.p.pace_factor
        self._rate = self._rate_max
        self._t_rate_adj = 0.0
        self._rtt_win = collections.deque()  # recent RTT samples (t, rtt) for the delay valve
        self._qd, self._t_qd = 0.0, 0.0      # last queueing-delay estimate and when
        self._tokens = 0.0
        self._bucket = 24 * (config.PHY_MTU + config.PHY_OVERHEAD)
        self._t_tokens = _now()
        # RTT estimation
        self.srtt, self.rttvar, self.rto = None, None, self.p.init_rto
        self.min_rtt = 1e9
        self._rto_backoff = 1.0
        # link
        self._last_heard = 0.0
        self._link_up = False
        self._next_hello = 0.0
        self._peer_hello_t = None
        self._peer_hello_rx = 0.0
        self.link_rtt = None
        self._t_link_rtt = 0.0
        self._peer_hears_us = False
        self.stats = collections.Counter()
        self._running = False
        self._thread = None

    # ---------------------------------------------------------------- public API
    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="transport", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self.sock.close()

    def send_bytes(self, data: bytes, meta: dict) -> int:
        """Queue a transfer. Returns its xfer id."""
        self._xfer_id = (self._xfer_id % 0xFFFF) + 1
        xid = self._xfer_id
        meta = dict(meta)
        meta.setdefault("size", len(data))
        meta["sha256"] = hashlib.sha256(data).hexdigest()
        mj = json.dumps(meta, separators=(",", ":")).encode()
        body = struct.pack(">H", len(mj)) + mj + data
        self._jobs.put((xid, body, meta))
        return xid

    def send_text(self, text: str) -> int:
        return self.send_bytes(text.encode("utf-8"), {"kind": "text"})

    def cancel(self, xid):
        self._jobs.put(("cancel", xid))

    @property
    def link_up(self):
        return self._link_up

    @property
    def link_state(self):
        """'down' (we hear nothing), 'one-way' (we hear the peer, but its beacons show
        it does not hear us), or 'up' (both directions working)."""
        if not self._link_up:
            return "down"
        return "up" if self._peer_hears_us else "one-way"

    @property
    def current_rtt(self):
        """Beacon RTT if measured in the last 3 s, else None."""
        if self.link_rtt and _now() - self._t_link_rtt < 3.0:
            return self.link_rtt
        return None

    def chunk_size(self):
        """frame payload for a new transfer: shorter frames on a lossy direction (judged by how
        many DATA frames of recent transfers got through on the first try)."""
        q = self._data_delivery
        for limit, size in CHUNKS:
            if q is None or q >= limit:
                return size
        return CHUNKS[-1][1]

    @property
    def current_quality_in(self):
        """share of the peer's frames that reach us (last few seconds), or None."""
        return self.quality_in if _now() - self._q_in_t < 6.0 else None

    @property
    def current_quality_out(self):
        """share of our frames that reach the peer (its report), or None."""
        return self.quality_out if _now() - self._q_out_t < 6.0 else None

    def telemetry(self):
        """the transport's live numbers for the hidden telecom panel."""
        return dict(state=self.link_state, beacon_rtt=self.current_rtt, srtt=self.srtt,
                    min_rtt=None if self.min_rtt > 1e8 else self.min_rtt, rto=self.rto * self._rto_backoff,
                    rate=self._rate, rate_max=self._rate_max, queue_delay=self._qd,
                    chunk=self.chunk_size(), first_try=self._data_delivery,
                    q_out=self.current_quality_out, q_in=self.current_quality_in,
                    listen_mode=self.listen_mode, active_tx=len(self._active), pending_tx=len(self._pending),
                    active_rx=len(self._rx), duplex=self.mode)

    @property
    def listen_mode(self):
        return self.quiet and _now() < self._hybrid_until

    def _quality_sample(self, now, peer_sent, peer_q):
        if peer_q != 0xFFFF:
            self.quality_out, self._q_out_t = min(1.0, peer_q / 1000.0), now
        dq = self._q_samples
        if dq and peer_sent < dq[-1][1]:       # the peer restarted
            dq.clear()
        dq.append((now, peer_sent, self._rx_frames))
        while len(dq) > 2 and now - dq[1][0] >= 3.0:
            dq.popleft()
        t0, s0, r0 = dq[0]
        if now - t0 >= 1.5 and peer_sent - s0 >= 20:     # only with traffic: idle beacons say little
            self.quality_in = max(0.0, min(1.0, (self._rx_frames - r0) / (peer_sent - s0)))
            self._q_in_t = now

    # ---------------------------------------------------------------- internals
    def _emit(self, **e):
        try:
            self.on_event(e)
        except Exception as ex:  # never let the GUI kill the transport thread
            print("event handler error:", ex, file=sys.stderr)

    def _send(self, frame, ctrl=False):
        if self.crypto is not None:
            frame = self.crypto.seal(frame)
        try:
            self.sock.sendto(frame, self.tx_addr)
        except (BlockingIOError, OSError) as ex:
            self.stats["send_err"] += 1
            if self.stats["send_err"] < 5:
                print("send error:", ex, file=sys.stderr)
            return False
        self._tokens -= len(frame) + config.PHY_OVERHEAD
        self.stats["tx_bytes"] += len(frame)
        self.stats["tx_ctrl" if ctrl else "tx_data"] += 1
        self._sent_frames += 1
        return True

    def _refill(self, now):
        self._recover_rate(now)
        self._tokens = min(self._bucket, self._tokens + (now - self._t_tokens) * self._rate)
        self._t_tokens = now

    def _update_rtt(self, r):
        self.min_rtt = min(self.min_rtt, r)
        if self.srtt is None:
            self.srtt, self.rttvar = r, r / 2
        else:
            self.rttvar = 0.75 * self.rttvar + 0.25 * abs(self.srtt - r)
            self.srtt = 0.875 * self.srtt + 0.125 * r
        self.rto = min(self.p.max_rto, max(self.p.min_rto, self.srtt + 4 * self.rttvar))
        self._rto_backoff = 1.0
        # Delay-based safety valve: only persistent queueing (PC or radio can't drain at the
        # nominal rate, or the two nodes run different profiles) slows the sender. The queue
        # estimate is the SMALLEST recent RTT minus the path minimum, so ACKs that were held
        # back (quiet FDD ACKs every 32 KB, listen mode) or CPU jitter don't count as queueing.
        now = _now()
        win = self._rtt_win
        win.append((now, r))
        while len(win) > 4 and now - win[0][0] > 2.0:
            win.popleft()
        qd = min(x for _, x in win) - self.min_rtt
        self._qd, self._t_qd = qd, now
        if qd > self._queue_hi() and now - self._t_rate_adj > max(0.1, self.srtt):
            self._rate = max(self._rate_max * self.p.rate_floor, self._rate * 0.8)
            self._t_rate_adj = now

    def _queue_hi(self):
        return max(self.p.queue_hi, self.min_rtt)

    def _recover_rate(self, now):
        """speed back up by TIME, not per ACK: at a low rate quiet-FDD ACKs are seconds apart,
        and recovering only when one arrives left the sender stuck near its floor."""
        if self._rate >= self._rate_max or now - self._t_rate_adj < max(0.5, 2 * (self.srtt or 0.1)):
            return
        if self._qd < 0.5 * self._queue_hi() or now - self._t_qd > 1.0:
            self._rate = min(self._rate_max, self._rate * 1.25)
            self._t_rate_adj = now

    # ---- receive path
    def _handle(self, pkt, now):
        if len(pkt) < COMMON.size or pkt[0] != MAGIC:
            self.stats["rx_junk"] += 1
            return
        _, typ, src, dst, xfer = COMMON.unpack_from(pkt)
        if src == self.me or (dst != self.me and dst != BROADCAST):
            return
        self._last_heard = now
        self._rx_frames += 1
        if not self._link_up:
            self._link_up = True
            # back after a drop: start the ACK clocks afresh (the silence was the drop, not a
            # deaf receiver or a congested link)
            self._last_ack_rx = now
            self._sending_since = None
            self._rto_backoff = 1.0
            self._hybrid_until = 0.0
            self._emit(type="link", up=True)
        if typ in (T_DATA, T_DATA_P) and len(pkt) >= DATA_HDR.size:
            self._on_data(pkt, src, xfer, now)
        elif typ == T_ACK and len(pkt) >= ACK_HDR.size:
            self._on_ack(pkt, xfer, now)
        elif typ == T_HELLO and len(pkt) >= HELLO.size:
            _, _, _, _, _, t_send, t_echo, echo_age, peer_sent, peer_q = HELLO.unpack_from(pkt)
            self._peer_hello_t, self._peer_hello_rx = t_send, now
            self._quality_sample(now, peer_sent, peer_q)
            # The echo is only meaningful if the peer heard OUR beacon recently; with a
            # one-way link it keeps echoing a stale timestamp (possibly from an earlier
            # session), which used to show up as an RTT of millions of ms.
            fresh_echo = t_echo > 0 and 0 <= echo_age < 2 * self.p.hello_interval
            # If the peer's beacons stop echoing ours, the peer can't hear us.
            self._peer_hears_us = fresh_echo
            if fresh_echo:
                r = now - t_echo - echo_age
                if 0 < r < 5.0:
                    self.link_rtt = r
                    self._t_link_rtt = now
        elif typ == T_CANCEL:
            self._rx.pop((src, xfer), None)

    def _on_data(self, pkt, src, xfer, now):
        _, _, _, _, _, seq, total, txn = DATA_HDR.unpack_from(pkt)
        key = (src, xfer)
        self.stats["rx_data"] += 1
        self._last_data_rx = now
        done = self._completed.get(key)
        if done and done[0] == total:
            # duplicate after completion -> our final ACK was lost: re-ACK
            if self.tdd:
                self._pending_done[key] = [total, seq, txn, 1]
            else:
                self._send_ack_raw(src, xfer, total, 0, FLAG_COMPLETE, seq, txn, b"")
            return
        rx = self._rx.get(key)
        if rx is None or rx.total != total:
            rx = RxTransfer(src, xfer, total)
            self._rx[key] = rx
            self._emit(type="rx_start", xfer=xfer, src=src, total=total)
        is_new = rx.add(seq, bytes(pkt[DATA_HDR.size:]))
        self.stats["rx_new_bytes" if is_new else "rx_dup"] += (len(pkt) - DATA_HDR.size) if is_new else 1
        rx.echo = (seq, txn)
        rx.unacked += 1
        rx.unacked_bytes += len(pkt) - DATA_HDR.size
        rx.quiet_acked = False
        if rx.first_unacked_t is None:
            rx.first_unacked_t = now
        if rx.done:
            self._finish_rx(rx, now)
        elif self.quiet:
            # few ACKs: a radio deafened by its own transmitter loses incoming frames during each
            if (rx.unacked_bytes >= self.p.fdd_ack_bytes or rx.unacked >= self.p.fdd_ack_frames
                    or (not is_new and now - rx.t_dup_ack > 0.05)):
                if not is_new:
                    rx.t_dup_ack = now
                self._send_ack(rx)
        elif not self.tdd and (rx.unacked >= self.p.ack_every or not is_new):
            self._send_ack(rx)
        # (TDD: ACKs are sent from _housekeeping once the channel goes quiet)
        if is_new and rx.n % 64 == 0:
            dt = max(1e-6, now - rx.t_start)
            self._emit(type="rx_progress", xfer=xfer, got=rx.n, total=total,
                       rate_Bps=rx.bytes / dt)

    def _send_ack_raw(self, dst, xfer, cum, nrecv, flags, echo_seq, echo_txn, bitmap):
        frame = ACK_HDR.pack(MAGIC, T_ACK, self.me, dst, xfer, cum, nrecv, flags,
                             echo_seq, echo_txn) + bitmap
        self._send(frame, ctrl=True)

    def _send_ack(self, rx):
        self._send_ack_raw(rx.src, rx.xfer, rx.cum, rx.n,
                           FLAG_COMPLETE if rx.done else 0, rx.echo[0], rx.echo[1],
                           rx.ack_bitmap())
        rx.unacked = 0
        rx.unacked_bytes = 0
        rx.first_unacked_t = None

    def _finish_rx(self, rx, now):
        key = (rx.src, rx.xfer)
        if self.tdd:                          # send when the channel goes quiet
            self._pending_done[key] = [rx.total, rx.echo[0], rx.echo[1], 2]
        else:
            for _ in range(2):                # send the completion ACK twice
                self._send_ack(rx)
        self._completed[key] = (rx.total, now)
        self._rx.pop(key, None)
        body = rx.body()
        ok, meta, data = False, {}, b""
        try:
            ml = struct.unpack(">H", body[:2])[0]
            meta = json.loads(body[2:2 + ml].decode())
            data = body[2 + ml:]
            ok = hashlib.sha256(data).hexdigest() == meta.get("sha256")
        except Exception as ex:
            meta = {"error": str(ex)}
        dt = max(1e-6, now - rx.t_start)
        self._emit(type="rx_done", xfer=rx.xfer, src=rx.src, meta=meta, data=data,
                   seconds=dt, rate_Bps=len(body) / dt, ok=ok, dups=rx.dups)

    def _on_ack(self, pkt, xfer, now):
        _, _, _, _, _, cum, nrecv, flags, echo_seq, echo_txn = ACK_HDR.unpack_from(pkt)
        bitmap = pkt[ACK_HDR.size:]
        self.stats["rx_ack"] += 1
        self._last_ack_rx = now
        if self._listen_until is not None and self._burst_last is not None:
            bx, bs, bt = self._burst_last
            if xfer == bx and (flags & FLAG_COMPLETE or (echo_seq == bs and echo_txn == bt)):
                self._listen_until = None      # receiver has seen the whole burst
        for t in self._active:
            if t.xfer == xfer and not t.done:
                if flags & FLAG_COMPLETE:
                    cum, bitmap = t.total, b""
                rtt = t.on_ack(cum, bitmap, echo_seq, echo_txn, now)
                self.stats["acked_bytes"] += t.last_newly * t.chunk_size
                if rtt is not None:
                    self._update_rtt(rtt)
                if t.done:
                    self._finish_tx(t, now)
                elif t.n_acked % 32 < 8:
                    self._emit_tx_progress(t, now)
                break

    def _emit_tx_progress(self, t, now):
        dt = max(1e-6, now - (t.t_start or now))
        nbytes = min(len(t.body), t.n_acked * t.chunk_size)
        self._emit(type="tx_progress", xfer=t.xfer, acked=t.n_acked, total=t.total,
                   bytes=nbytes, rate_Bps=nbytes / dt, retx=t.retransmissions)

    def _finish_tx(self, t, now):
        dt = max(1e-6, now - (t.t_start or now))
        if t.total >= 20 and not t.saw_drop:   # enough frames to judge this direction (frames lost
                                               # to a link drop say nothing about it)
            first_try = max(0.0, 1.0 - t.retransmissions / max(1, t.frames_sent))
            d = self._data_delivery
            self._data_delivery = first_try if d is None else 0.5 * d + 0.5 * first_try
        self._active.remove(t)
        self._emit(type="tx_done", xfer=t.xfer, meta=t.meta, seconds=dt, bytes=len(t.body),
                   rate_Bps=len(t.body) / dt, retx=t.retransmissions, frames=t.frames_sent)

    # ---- transmit path
    def _pump(self, now):
        self._refill(now)
        frame_cost = config.PHY_MTU + config.PHY_OVERHEAD
        # admit new jobs
        while not self._jobs.empty():
            j = self._jobs.get_nowait()
            if j[0] == "cancel":
                for t in list(self._active) + list(self._pending):
                    if t[0] == j[1] if isinstance(t, tuple) else t.xfer == j[1]:
                        if isinstance(t, tuple):
                            self._pending.remove(t)
                            self._emit(type="tx_failed", xfer=t[0], meta=t[2], reason="cancelled")
                        else:
                            t.failed = "cancelled"
                self._send(CANCEL.pack(MAGIC, T_CANCEL, self.me, self.peer, j[1]), ctrl=True)
            else:
                self._pending.append(j)
        if self._pending:
            keep = collections.deque()
            big = sum(1 for t in self._active if not self._is_small(t))
            while self._pending:
                xid, body, meta = self._pending.popleft()
                small = len(body) <= SMALL_BODY and not self.tdd
                if small or big < self.p.max_concurrent_tx:
                    self._active.append(TxTransfer(xid, self.peer, body, meta, self.p, self.chunk_size()))
                    big += 0 if small else 1
                else:
                    keep.append((xid, body, meta))
            self._pending = keep
        for t in list(self._active):
            if not self._link_up:
                t.saw_drop = True
                t.t_progress = now         # the give-up clock runs only while the link is up: a
                                           # transfer waits out a link drop and then continues
            if t.failed:
                self._active.remove(t)
                self._emit(type="tx_failed", xfer=t.xfer, meta=t.meta, reason=t.failed)
            elif t.t_start and now - t.t_progress > self.p.stall_timeout:
                t.failed = "no ACK progress (link down?)"
        if not self._link_up:
            return          # nothing heard from the peer: frames sent now would be lost; the
                            # transfers wait and continue with the missing frames when it is back
        # retransmission timeouts
        rto = self.rto * self._rto_backoff
        if self.quiet:
            # quiet FDD ACKs once per fdd_ack_bytes: never time out before the next ACK is due
            rto = max(rto, 1.5 * self.p.fdd_ack_bytes / self._rate_max + (self.srtt or 0.1))
        fired = False
        for t in self._active:
            if not self._is_small(t) and t.check_rto(now, rto):
                fired = True
        self._send_small(now)
        if fired:
            self.stats["rto"] += 1
            self._rto_backoff = min(8.0, self._rto_backoff * 1.5)
        # quiet FDD: a sender that stops hearing ACKs while it transmits is deafened by its own
        # transmitter -> "burst, then listen" for a while (the receiver ACKs when we pause)
        if self.quiet:
            sending = any(t.t_start and t.inflight for t in self._active)
            if not sending:
                self._sending_since = None
            elif self._sending_since is None:
                self._sending_since = now
            starve = max(self.p.ack_starve, 4 * (self.srtt or 0.1),
                         3 * self.p.fdd_ack_bytes / self._rate_max)
            if (sending and now >= self._hybrid_until
                    and now - max(self._last_ack_rx, self._sending_since) > starve):
                self._hybrid_until = now + self.p.listen_mode_hold
                self.stats["listen_mode"] += 1
                self._emit(type="listen_mode", on=True)
        bursty = self.tdd or (self.quiet and now < self._hybrid_until)
        # TDD / listen mode: don't transmit while listening for the burst ACK. TDD also waits
        # while the peer is sending to us (we'd deafen ourselves and lose its frames).
        if bursty:
            if self._listen_until is not None:
                if now < self._listen_until:
                    return
                self._listen_until = None
                self.stats["listen_timeout"] += 1
            if self.tdd and now - self._last_data_rx < self._quiet:
                return
        else:
            self._listen_until = None
            self._burst_n = 0
        burst_max = self._burst_size()
        # paced transmission, round-robin over active transfers
        n = len(self._active)
        while n and self._tokens >= frame_cost * 0.5:
            sent = False
            for k in range(n):
                t = self._active[(self._rr + k) % n]
                if t.done or self._is_small(t) or not t.has_work():
                    continue
                s = t.next_seq()
                if s is None:
                    continue
                txn = t.tx_count[s] % 255 + 1
                frame = DATA_HDR.pack(MAGIC, T_DATA, self.me, t.dst, t.xfer, s, t.total, txn) + t.chunk(s)
                if self._send(frame):
                    if t.tx_count[s]:
                        self.stats["retx"] += 1
                    t.mark_sent(s, now)
                    self._last_data_tx = now
                    if bursty:
                        self._burst_n += 1
                        self._burst_last = (t.xfer, s, txn)
                sent = True
                self._rr = (self._rr + k + 1) % n
                break
            if not sent:
                break
            if bursty and self._burst_n >= burst_max:
                break
        if bursty and self._burst_n and (self._burst_n >= burst_max or
                                           not any(t.has_work() for t in self._active if not self._is_small(t))):
            # burst finished: go quiet and listen for the receiver's ACK
            self._listen_until = now + self._listen_gap()
            self._burst_n = 0
            self.stats["bursts"] += 1

    def _is_small(self, t):
        return t.total <= 2 and len(t.body) <= SMALL_BODY and not self.tdd

    def _send_small(self, now):
        """small messages: priority frames, each sent twice back to back, resent after ~1.5 RTT."""
        again = max(0.15, 1.5 * (self.srtt or 0.2))
        for t in self._active:
            if t.done or not self._is_small(t):
                continue
            for seq in range(t.total):
                if t.acked[seq] or (t.tx_count[seq] and now - t.last_tx[seq] < again):
                    continue
                txn = t.tx_count[seq] % 255 + 1
                frame = DATA_HDR.pack(MAGIC, T_DATA_P, self.me, t.dst, t.xfer, seq, t.total, txn) + t.chunk(seq)
                if self._send(frame):
                    self._send(frame)
                    t.mark_sent(seq, now)
                    self.stats["small_tx"] += 1

    def _ack_delay(self):
        """longest a received frame waits for its ACK (quiet FDD: ACKs are rare on purpose)."""
        return 4 * self._quiet if self.quiet else self.p.ack_delay

    def _burst_size(self):
        """Frames per burst before listening for the ACK. TDD: ~3x RTT of airtime (=> ~75%
        duty cycle). Quiet-FDD listen mode: ~8x RTT (the peer keeps sending meanwhile)."""
        k, lo = (8, 32) if self.quiet else (3, 16)
        if self.min_rtt < 1e8:
            return int(min(256, max(lo, k * self.min_rtt / self._frame_air)))
        return 32 if self.tdd else 64

    def _listen_gap(self):
        """Max time to wait for the burst ACK (normally it arrives sooner)."""
        if self.srtt is not None:
            return min(1.5, max(0.08, 1.5 * self.srtt + 0.03))
        return 0.5

    def _housekeeping(self, now):
        # delayed ACKs (FDD) / ACK when the channel goes quiet (TDD)
        quiet = now - self._last_data_rx >= self._quiet
        for rx in list(self._rx.values()):
            if now - rx.t_last > 60:                      # abandoned transfer
                self._rx.pop((rx.src, rx.xfer), None)
                continue
            if not self.tdd and quiet and not rx.quiet_acked:
                rx.quiet_acked = True          # the sender paused: tell it what arrived. Twice,
                self._send_ack(rx)             # back to back: the second copy is not alone after
                rx.unacked = 1                 # silence, so it is decoded far more reliably
                self._send_ack(rx)
                continue
            if not rx.unacked or rx.first_unacked_t is None:
                continue
            if (self.tdd and quiet) or (not self.tdd and now - rx.first_unacked_t >= self._ack_delay()):
                self._send_ack(rx)
        if self._pending_done and (quiet or not self.tdd):
            for key, v in list(self._pending_done.items()):
                total, seq, txn, rep = v
                self._send_ack_raw(key[0], key[1], total, total, FLAG_COMPLETE, seq, txn, b"")
                v[3] -= 1
                if v[3] <= 0:
                    del self._pending_done[key]
        for k, (_, t) in list(self._completed.items()):
            if now - t > 60:
                del self._completed[k]
        # beacons / link state
        if now >= self._next_hello and not (self.tdd and now - self._last_data_rx < 0.3):
            # random jitter so the two nodes' beacons can't stay synchronised (in TDD a
            # node can't hear a beacon that arrives while it is sending its own)
            self._next_hello = now + self.p.hello_interval * random.uniform(0.7, 1.3)
            fresh = self._peer_hello_t is not None and now - self._peer_hello_rx < 2 * self.p.hello_interval
            echo_t = self._peer_hello_t if fresh else 0.0
            age = (now - self._peer_hello_rx) if fresh else 0.0
            q = 0xFFFF if self.quality_in is None else int(round(1000 * self.quality_in))
            self._send(HELLO.pack(MAGIC, T_HELLO, self.me, BROADCAST, 0, now, echo_t, age,
                                  (self._sent_frames + 1) & 0xFFFFFFFF, q), ctrl=True)
        if self._link_up and now - self._last_heard > self.p.link_timeout:
            self._link_up = False
            self._peer_hello_t = None      # stop echoing a stale timestamp
            self.link_rtt = None
            self._peer_hears_us = False
            self._emit(type="link", up=False)

    def _next_wakeup(self, now):
        w = 0.02
        frame_cost = config.PHY_MTU + config.PHY_OVERHEAD
        if any(t.has_work() for t in self._active if not self._is_small(t)):
            deficit = frame_cost * 0.5 - self._tokens
            w = min(w, max(0.0005, deficit / self._rate))
        for rx in self._rx.values():
            if rx.first_unacked_t is not None:
                if self.tdd:
                    w = min(w, max(0.001, self._last_data_rx + self._quiet - now))
                else:
                    w = min(w, max(0.0, rx.first_unacked_t + self._ack_delay() - now))
            if not self.tdd and not rx.quiet_acked:
                w = min(w, max(0.001, self._last_data_rx + self._quiet - now))
        if self._listen_until is not None:
            w = min(w, max(0.001, self._listen_until - now))
        if self.tdd:
            if self._pending_done:
                w = min(w, max(0.001, self._last_data_rx + self._quiet - now))
        return w

    def _loop(self):
        while self._running:
            now = _now()
            try:
                r, _, _ = select.select([self.sock], [], [], self._next_wakeup(now))
            except (OSError, ValueError):
                if not self._running:
                    break
                time.sleep(0.05)               # never let a socket hiccup end the transport
                continue
            now = _now()
            if r:
                for _ in range(512):
                    try:
                        pkt = self.sock.recv(4096)
                    except (BlockingIOError, InterruptedError):
                        break
                    except ConnectionResetError:
                        continue
                    except OSError:
                        break
                    self.stats["rx_bytes"] += len(pkt)
                    if self.crypto is not None:
                        pkt = self.crypto.open(pkt)
                        if pkt is None:
                            continue
                    self._handle(pkt, now)
            try:
                self._pump(now)
                self._housekeeping(now)
            except Exception as ex:
                import traceback
                traceback.print_exc()
                self._emit(type="error", error=str(ex))
