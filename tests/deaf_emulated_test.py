"""
Emulated radios (no GNU Radio) where node 2's receiver loses a share of the frames that
arrive while its OWN transmitter is on (self-desensitisation, the hardware symptom: one
direction ~100 KB/s, the other ~8 KB/s). Each direction is measured on its own.

  fdd-cont: transmitter always on (idle symbols) -> node 2 always partly deaf
  fdd:      quiet FDD -> node 2 deaf only during its own ACKs / while it sends data
  tdd:      take turns

    python tests/deaf_emulated_test.py                    # node 2 loses 75 % / 100 %
    python tests/deaf_emulated_test.py --deaf-p 0.75 --modes fdd
"""
import argparse
import collections
import heapq
import os
import random
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import config  # noqa: E402
from transport import Transport  # noqa: E402


class Radio(threading.Thread):
    """One direction: listens on a node's TX port, delivers to the other node's RX port."""

    def __init__(self, listen_port, deliver_port, byte_rate, latency, loss, continuous):
        super().__init__(daemon=True)
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
        self.s.bind(("127.0.0.1", listen_port))
        self.s.settimeout(0.001)
        self.out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.dst = ("127.0.0.1", deliver_port)
        self.rate, self.lat, self.loss = byte_rate, latency, loss
        self.continuous = continuous      # FDD: transmitter always on
        self.busy_until = 0.0
        self.onair = collections.deque()  # (start, end) of this transmitter's frames
        self.heap = []
        self.n = self.lost = self.deafened = 0
        self.rx_side_tx = None            # Radio object of the RECEIVING node's transmitter
        self.receiver_deaf = False
        self.deaf_p = 1.0
        self.running = True

    def transmitting(self, a, b):
        if self.continuous:
            return True
        return any(s < b and e > a for s, e in self.onair)

    def deafens(self, a, b):
        return self.transmitting(a, b) and random.random() < self.deaf_p

    def run(self):
        while self.running:
            try:
                pkt = self.s.recv(4096)
                now = time.perf_counter()
                start = max(now, self.busy_until)
                end = start + (len(pkt) + config.PHY_OVERHEAD) / self.rate
                self.busy_until = end
                self.onair.append((start, end))
                while self.onair and self.onair[0][1] < now - 2:
                    self.onair.popleft()
                self.n += 1
                if random.random() < self.loss:
                    self.lost += 1
                else:
                    heapq.heappush(self.heap, (end + self.lat, self.n, start, end, pkt))
            except socket.timeout:
                pass
            now = time.perf_counter()
            while self.heap and self.heap[0][0] <= now:
                _, _, a, b, p = heapq.heappop(self.heap)
                if self.receiver_deaf and self.rx_side_tx.deafens(a, b):
                    self.deafened += 1          # receiver was transmitting -> can't hear
                    continue
                self.out.sendto(p, self.dst)


def run(duplex, direction, deaf_p, profile="robust", kb=600, loss=0.01, latency=0.03, timeout=60, base=63000):
    rate = config.get_profile(profile)
    cont = duplex == "fdd-cont"
    r12 = Radio(base + 1, base + 12, rate.byte_rate, latency, loss, cont)   # node1 TX -> node2
    r21 = Radio(base + 11, base + 2, rate.byte_rate, latency, loss, cont)   # node2 TX -> node1
    r12.rx_side_tx, r21.rx_side_tx = r21, r12
    r12.receiver_deaf = True             # node 2 partly deaf while it transmits
    r21.deaf_p = deaf_p
    r12.start(); r21.start()
    ev = []
    n1 = Transport(1, 2, base + 1, base + 2, rate, lambda e: ev.append(("n1", e)), duplex=duplex)
    n2 = Transport(2, 1, base + 11, base + 12, rate, lambda e: ev.append(("n2", e)), duplex=duplex)
    n1.start(); n2.start()
    time.sleep(2.0)
    snd, rcv = (n1, "n2") if direction == "1to2" else (n2, "n1")
    data = os.urandom(int(kb * 1000))
    t0 = time.time()
    x = snd.send_bytes(data, {"kind": "file", "name": "f.bin"})
    got = None
    while time.time() - t0 < timeout and got is None:
        time.sleep(0.1)
        got = next((e for n, e in list(ev) if n == rcv and e["type"] == "rx_done" and e["xfer"] == x), None)
    ok = bool(got and got["ok"] and got["data"] == data)
    q = (n1.current_quality_out, n2.current_quality_out)
    n1.stop(); n2.stop(); r12.running = r21.running = False
    time.sleep(0.3)
    return (kb / got["seconds"] if ok else 0.0), q, dict(snd.stats)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--deaf-p", default="0.75,1.0", help="share of frames node 2 loses while transmitting")
    ap.add_argument("--modes", default="fdd-cont,fdd,tdd")
    ap.add_argument("--kb", type=float, default=600)
    a = ap.parse_args()
    ok = True
    base = 63000
    for p in [float(x) for x in a.deaf_p.split(",")]:
        print(f"node 2 loses {p:.0%} of the frames that arrive while it transmits:")
        for m in a.modes.split(","):
            r = {}
            for d in ("1to2", "2to1"):
                base += 100
                r[d], q, st = run(m, d, p, kb=a.kb, base=base)
            print(f"  {m:8s}  1->2 (into node 2) {r['1to2']:5.0f} KB/s   2->1 (out of node 2) {r['2to1']:5.0f} KB/s",
                  flush=True)
            if m == "fdd":
                ok &= min(r.values()) >= 60
    print("RESULT:", "PASS (quiet FDD >= 60 KB/s both ways)" if ok else "FAIL")
