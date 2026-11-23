#!/usr/bin/env python3
"""
Fake radio link for testing app.py / cli.py on ONE PC without SDRs or GNU Radio.

    python tools/fake_radio.py --loss 0.02
    python app.py --node 1 --same-pc
    python app.py --node 2 --same-pc

Emulates the PHY: exact bit-rate serialization + latency + random frame loss.
"""
import argparse, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
import config
from arq_emulated_test import EmulatedLink

ap = argparse.ArgumentParser()
ap.add_argument("--profile", default=config.DEFAULT_PROFILE, choices=list(config.PROFILES))
ap.add_argument("--loss", type=float, default=0.01)
ap.add_argument("--latency", type=float, default=0.03)
a = ap.parse_args()
r = config.get_profile(a.profile)
l1 = EmulatedLink(52001, 52012, r.byte_rate, a.latency, a.loss, 0.0)   # node1 TX -> node2 RX
l2 = EmulatedLink(52011, 52002, r.byte_rate, a.latency, a.loss, 0.0)   # node2 TX -> node1 RX
l1.start(); l2.start()
print(f"fake radio: {a.profile} {r.bit_rate/1e6:.2f} Mbps, loss {a.loss*100:.1f}%. "
      "Start the apps with --same-pc. Ctrl+C to stop.")
try:
    while True:
        time.sleep(5); print(f"  1->2 frames {l1.n} lost {l1.lost} | 2->1 frames {l2.n} lost {l2.lost}")
except KeyboardInterrupt:
    pass
