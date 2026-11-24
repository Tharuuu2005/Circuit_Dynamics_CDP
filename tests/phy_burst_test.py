"""
Burst-acquisition test: frames separated by random SILENT gaps (radio off), so the
receiver's AGC, Symbol Sync and Costas loop must re-acquire on every burst. Run for the
two modes that go silent: tdd (long preamble on every frame) and fdd (quiet FDD: long
preamble only on the first frame after silence, short preamble on back-to-back frames).

    python tests/phy_burst_test.py
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "phy"))
sys.path.insert(0, os.path.join(HERE, ".."))
import numpy as np  # noqa: E402
import pmt  # noqa: E402
from gnuradio import gr, blocks, channels  # noqa: E402
import phy_flowgraph as P  # noqa: E402


class Collector(gr.basic_block):
    def __init__(self):
        gr.basic_block.__init__(self, "collector", None, None)
        self.message_port_register_in(pmt.intern("in"))
        self.got = []
        self.set_msg_handler(pmt.intern("in"), self.handle)

    def handle(self, m):
        self.got.append(bytes(pmt.u8vector_elements(pmt.cdr(m))))


def run(mod="bpsk", snr=15, n=120, samp_rate=1e6, sps=4, cfo=2e-4, burst=(1, 4), duplex="tdd", **rxkw):
    tb = gr.top_block()
    tx = P.TxChain(mod, sps, 0.6, duplex=duplex)
    rx = P.RxChain(mod, sps, samp_rate, **rxkw)
    nv = 10 ** (-snr / 20) * 0.6
    thr = blocks.throttle(gr.sizeof_gr_complex, samp_rate, True)
    ch = channels.channel_model(nv, cfo, 1.0 + 50e-6, [1.0], 5)
    rot = blocks.multiply_const_cc(np.exp(0.9j))
    col = Collector()
    tb.connect(tx, thr, rot, ch, rx)
    tb.msg_connect(rx, "out", col, "in")
    rng = np.random.default_rng(2)
    pkts = [bytes([0xA5, 1]) + i.to_bytes(4, "big") +
            (rng.integers(0, 256, 994, dtype=np.uint8).tobytes() if i % 2 else bytes(994))
            for i in range(n)]
    tb.start()
    time.sleep(0.5)
    i = 0
    while i < n:                                     # bursts of 1..4 frames, then silence
        k = int(rng.integers(burst[0], burst[1] + 1))
        for p in pkts[i:i + k]:
            tx.framer._on_pdu(pmt.cons(pmt.PMT_NIL, pmt.init_u8vector(len(p), list(p))))
        i += k
        air = k * 1100 * 8 * sps / config_bps(mod) / samp_rate
        time.sleep(air + float(rng.uniform(0.01, 0.06)))   # silent gap 10-60 ms
    time.sleep(1.5)
    tb.stop()
    tb.wait()
    good = sum(1 for p in pkts if p in set(col.got))
    return good, n


def config_bps(mod):
    return 1 if mod == "bpsk" else 2


if __name__ == "__main__":
    ok = True
    for duplex in ("fdd", "tdd"):
        for snr in (20, 12, 8):
            g, n = run("bpsk", snr, duplex=duplex)
            ok &= g >= (0.95 if duplex == "fdd" else 0.85) * n
            print(f"{duplex} bursts, BPSK SNR={snr:>2} dB: {g}/{n} frames OK (PER {100*(1-g/n):.1f}%)", flush=True)
    print("RESULT:", "PASS" if ok else "FAIL")
