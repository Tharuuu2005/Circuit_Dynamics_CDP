#!/usr/bin/env python3
"""
Can this PC run the radio? Runs the real receive chain and transmit chain together, as fast
as the PC allows (no radio needed), for about 10 s, and compares that with what the radio
needs (4 Msps each way at --profile robust, 10 Msps at balanced).

    python tools/speed_check.py                 # robust
    python tools/speed_check.py --profile balanced

You want RESULT: OK (1.5x real time or better). Below 1.0x the transmitter falls behind:
its frames break up and the direction OUT of this PC stalls, while the other direction can
still look fine.
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "phy"))
import numpy as np  # noqa: E402
import pmt  # noqa: E402
from gnuradio import gr, blocks  # noqa: E402
import config  # noqa: E402
import phy_flowgraph as P  # noqa: E402


def run(sr, sps, agc, seconds):
    n = int(sr * seconds)
    tb = gr.top_block()
    noise = (np.random.randn(65536) + 1j * np.random.randn(65536)).astype(np.complex64) * 0.3
    src = blocks.vector_source_c(noise.tolist(), True)
    head = blocks.head(gr.sizeof_gr_complex, n)
    rx = P.RxChain("bpsk", sps, sr, agc=agc)
    tx = P.TxChain("bpsk", sps, 0.6, duplex="fdd")
    txhead = blocks.head(gr.sizeof_gr_complex, n)
    tb.connect(src, head, rx)
    tb.connect(tx, txhead, blocks.null_sink(gr.sizeof_gr_complex))
    frame = bytes([0xA5, 1]) + os.urandom(1000)
    for _ in range(int(n / sps / 8 / 1046) + 10):            # keep the transmitter busy
        tx.framer._on_pdu(pmt.cons(pmt.PMT_NIL, pmt.init_u8vector(len(frame), list(frame))))
    t = time.time()
    tb.run()
    return n / (time.time() - t) / sr


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=list(config.PROFILES), default=config.DEFAULT_PROFILE)
    ap.add_argument("--agc", choices=("fast", "py"), default="fast")
    ap.add_argument("--seconds", type=float, default=10.0, help="of radio time to process")
    a = ap.parse_args()
    p = config.PROFILES[a.profile]
    print(f"processing {a.seconds:g} s of radio samples ({p['samp_rate'] / 1e6:g} Msps receive + transmit) ...",
          flush=True)
    x = run(p["samp_rate"], p["sps"], a.agc, a.seconds)
    print(f"this PC runs the receiver and transmitter together at {x:.2f}x real time")
    if x >= 1.5:
        print("RESULT: OK")
    elif x >= 1.0:
        print("RESULT: MARGINAL - plug in the charger, Windows power mode Best performance, close other programs")
    else:
        print("RESULT: TOO SLOW - this PC can't keep up with the radio at this profile")


if __name__ == "__main__":
    main()
