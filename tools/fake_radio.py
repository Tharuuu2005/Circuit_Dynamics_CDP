#!/usr/bin/env python3
"""
Fake radio link for trying the app on ONE PC without SDRs or GNU Radio.

    python tools/fake_radio.py --loss 0.02
    python app.py --same-pc --no-radio --role gate --data data_gate
    python app.py --same-pc --no-radio --role desk --data data_desk

(Pair the two copies once: create the key in one, load data_.../pairing.key in the other.)
Emulates the radio: exact bit rate, the framer's priorities (control > live stream > files),
latency and random frame loss. SDR_FAKE_CAMERA=1 gives a moving test picture instead of a webcam.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
import config  # noqa: E402
import emu  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--profile", default=config.DEFAULT_PROFILE, choices=list(config.PROFILES))
ap.add_argument("--loss", type=float, default=0.01)
ap.add_argument("--latency", type=float, default=0.004)
a = ap.parse_args()
r = config.get_profile(a.profile)
g, d = config.SAME_PC_PORTS[1], config.SAME_PC_PORTS[2]
l1, l2 = emu.link(g[:2], d[:2], r.byte_rate, a.loss, a.latency)
print(f"fake radio: {a.profile} {r.bit_rate / 1e6:.2f} Mbit/s, loss {a.loss * 100:.1f} %. "
      "Start the apps with --same-pc --no-radio. Ctrl+C stops.")
try:
    while True:
        time.sleep(5)
        print(f"  gate->desk frames {l1.n} lost {l1.lost} | desk->gate frames {l2.n} lost {l2.lost}")
except KeyboardInterrupt:
    pass
