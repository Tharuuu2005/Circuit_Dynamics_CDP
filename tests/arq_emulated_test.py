"""
ARQ stress test with an emulated PHY (no GNU Radio needed).

Emulates each direction of the radio link as: FIFO queue drained at the exact PHY bit
rate (incl. per-frame PHY overhead) + fixed latency + random and burst frame loss.

    python tests/arq_emulated_test.py --profile robust --size 4 --loss 0.02
"""
import argparse
import heapq
import os
import random
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import config  # noqa: E402
from transport import Transport, Params  # noqa: E402


class EmulatedLink(threading.Thread):
    def __init__(self, listen_port, deliver_port, byte_rate, latency, loss, burst):
        super().__init__(daemon=True)
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
        self.s.bind(("127.0.0.1", listen_port))
        self.s.settimeout(0.001)
        self.out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.dst = ("127.0.0.1", deliver_port)
        self.rate, self.lat, self.loss, self.burst = byte_rate, latency, loss, burst
        self.busy_until = 0.0
        self.heap = []
        self.n = self.lost = 0
        self.burst_left = 0
        self.running = True

    def run(self):
        while self.running:
            try:
                pkt = self.s.recv(4096)
                now = time.perf_counter()
                start = max(now, self.busy_until)
                self.busy_until = start + (len(pkt) + config.PHY_OVERHEAD) / self.rate
                self.n += 1
                drop = False
                if self.burst_left > 0:
                    self.burst_left -= 1
                    drop = True
                elif random.random() < self.loss:
                    drop = True
                elif self.burst and random.random() < self.burst:
                    self.burst_left = random.randint(5, 40)   # e.g. a Wi-Fi burst
                    drop = True
                if drop:
                    self.lost += 1
                else:
                    heapq.heappush(self.heap, (self.busy_until + self.lat, self.n, pkt))
            except socket.timeout:
                pass
            now = time.perf_counter()
            while self.heap and self.heap[0][0] <= now:
                _, _, p = heapq.heappop(self.heap)
                self.out.sendto(p, self.dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="robust", choices=list(config.PROFILES))
    ap.add_argument("--size", type=float, default=4.0, help="MB node1 -> node2")
    ap.add_argument("--reverse", type=float, default=1.0, help="MB node2 -> node1 simultaneously")
    ap.add_argument("--loss", type=float, default=0.02)
    ap.add_argument("--burst", type=float, default=0.0005, help="prob. of starting a burst loss")
    ap.add_argument("--latency", type=float, default=0.03)
    a = ap.parse_args()
    rate = config.get_profile(a.profile)
    print(f"profile={a.profile} PHY {rate.bit_rate/1e6:.2f} Mbps, loss={a.loss*100:.1f}% "
          f"burst={a.burst} latency={a.latency*1000:.0f} ms")
    # node1: tx->61001 rx<-61002 ; node2: tx->61011 rx<-61012
    l12 = EmulatedLink(61001, 61012, rate.byte_rate, a.latency, a.loss, a.burst)
    l21 = EmulatedLink(61011, 61002, rate.byte_rate, a.latency, a.loss, a.burst)
    l12.start(); l21.start()
    done = {}
    ev = threading.Event()
    rx_store = {}

    def mk(name):
        def on(e):
            if e["type"] in ("tx_done", "rx_done", "tx_failed"):
                done[(name, e["type"], e["xfer"])] = e
                ev.set()
            if e["type"] == "rx_done":
                rx_store[(name, e["xfer"])] = e
        return on
    n1 = Transport(1, 2, 61001, 61002, rate, mk("n1"))
    n2 = Transport(2, 1, 61011, 61012, rate, mk("n2"))
    n1.start(); n2.start()
    time.sleep(1.0)
    big = os.urandom(int(a.size * 1e6))
    rev = os.urandom(int(a.reverse * 1e6)) if a.reverse > 0 else None
    t0 = time.perf_counter()
    x_txt = n1.send_text("hello from node 1")
    x_big = n1.send_bytes(big, {"kind": "file", "name": "big.bin"})
    x_rev = n2.send_bytes(rev, {"kind": "file", "name": "rev.bin"}) if rev else None
    want = [("n2", "rx_done", x_txt), ("n2", "rx_done", x_big), ("n1", "tx_done", x_big)]
    if rev:
        want += [("n1", "rx_done", x_rev), ("n2", "tx_done", x_rev)]
    deadline = time.time() + 300
    while time.time() < deadline and not all(w in done for w in want):
        if any(k[1] == "tx_failed" for k in done):
            break
        ev.wait(0.5); ev.clear()
    ok = True
    for w in want:
        e = done.get(w)
        if not e:
            print("MISSING", w); ok = False; continue
        if w[1] == "rx_done":
            data_ok = e["ok"] and (e["data"] == (big if w[2] == x_big and w[0] == "n2" else
                                                 rev if w[0] == "n1" else b"hello from node 1"))
            print(f"{w[0]} received xfer {w[2]} {e['meta'].get('name', e['meta'].get('kind'))}: "
                  f"{len(e['data'])} B in {e['seconds']:.2f}s = {e['rate_Bps']/1e3:.0f} KB/s "
                  f"intact={data_ok} dups={e.get('dups')}")
            ok &= data_ok
        else:
            print(f"{w[0]} tx_done xfer {w[2]}: {e['seconds']:.2f}s, {e['rate_Bps']/1e3:.0f} KB/s, "
                  f"frames={e['frames']} retx={e['retx']}")
    eff = (a.size * 1e6) / (time.perf_counter() - t0) / rate.byte_rate
    print(f"link 1->2: {l12.n} frames, {l12.lost} lost | link 2->1: {l21.n} frames, {l21.lost} lost")
    print(f"node1 srtt={n1.srtt and n1.srtt*1000:.1f} ms rto={n1.rto*1000:.0f} ms stats={dict(n1.stats)}")
    print(f"goodput efficiency vs raw PHY rate: {eff*100:.0f}%")
    print("RESULT:", "PASS" if ok else "FAIL")
    n1.stop(); n2.stop()
    l12.running = l21.running = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
