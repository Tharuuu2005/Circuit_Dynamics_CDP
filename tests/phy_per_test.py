"""PHY packet-error-rate test in simulation (no radios). Run: python tests/phy_per_test.py"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "phy"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import numpy as np, pmt
from gnuradio import gr, blocks, channels
import phy_flowgraph as P

class Collector(gr.basic_block):
    def __init__(self):
        gr.basic_block.__init__(self, "collector", None, None)
        self.message_port_register_in(pmt.intern("in")); self.got = []
        self.set_msg_handler(pmt.intern("in"), self.handle)
    def handle(self, m):
        self.got.append(bytes(pmt.u8vector_elements(pmt.cdr(m))))

def run(mod, snr, n=300, plen=1000, sps=4, cfo=2e-4, tag_pollution=False, duplex="fdd-cont"):
    tb = gr.top_block()
    tx = P.TxChain(mod, sps, 0.6, duplex=duplex); rx = P.RxChain(mod, sps, 1e6)
    rng = np.random.default_rng(1)
    # half random frames, half worst-case all-zero frames (tests the scrambler)
    pkts = [bytes([0xA5, 1]) + i.to_bytes(4, "big") + (rng.integers(0, 256, plen - 6, dtype=np.uint8).tobytes() if i % 2 else bytes(plen - 6)) for i in range(n)]
    for p in pkts:
        tx.framer._on_pdu(pmt.cons(pmt.PMT_NIL, pmt.init_u8vector(len(p), list(p))))
    bits_per_sym = 1 if mod == "bpsk" else 2
    nsamp = int((n * (plen + 30) * 8 / bits_per_sym + 20000) * sps)
    nv = 10 ** (-snr / 20) * 0.6
    ch = channels.channel_model(nv, cfo, 1.0 + 50e-6, [1.0], 3)
    head = blocks.head(gr.sizeof_gr_complex, nsamp)
    rot = blocks.multiply_const_cc(np.exp(1.1j))
    col = Collector()
    if tag_pollution:   # emulate PlutoSDR Source emitting 'packet_len' every 32768 samples
        tagger = blocks.stream_to_tagged_stream(gr.sizeof_gr_complex, 1, 32768, "packet_len")
        tb.connect(tx, head, rot, ch, tagger, rx)
    else:
        tb.connect(tx, head, rot, ch, rx)
    tb.msg_connect(rx, "out", col, "in")
    t = time.time(); tb.start()
    while head.nitems_written(0) < nsamp: time.sleep(0.05)
    time.sleep(0.5); tb.stop(); tb.wait(); dt = time.time() - t
    good = sum(1 for g in col.got if g in set(pkts))
    return good, n, len(col.got), dt

if __name__ == "__main__":
    ok = True
    for duplex in ("fdd-cont", "fdd"):
        for snr in (20, 14, 10, 8):
            g, n, tot, dt = run("bpsk", snr, duplex=duplex)
            ok &= g >= (0.97 if snr >= 10 else 0.9) * n
            print(f"{duplex} back-to-back frames, BPSK SNR={snr:>2} dB: {g}/{n} frames OK  "
                  f"(PER {100*(1-g/n):5.1f}%)  [{dt:.1f}s]", flush=True)
    g, n, tot, dt = run("bpsk", 20, tag_pollution=True)
    ok &= g >= 0.97 * n
    print(f"bpsk SNR=20 WITH Pluto 'packet_len' tag pollution: {g}/{n} OK", flush=True)
    print("RESULT:", "PASS" if ok else "FAIL")
