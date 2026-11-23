"""
End-to-end test: transport.py <-> UDP <-> real GNU Radio PHY (both nodes simulated,
AWGN + carrier offset + clock offset) <-> UDP <-> transport.py.

    python tests/e2e_sim_test.py --profile robust --sim-rate 2e6 --snr 15 --size 0.3

Sim rate is limited by your CPU (two complete modems run in one process).
"""
import argparse
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
import config  # noqa: E402
from transport import Transport  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="robust", choices=list(config.PROFILES))
    ap.add_argument("--sim-rate", type=float, default=2e6)
    ap.add_argument("--snr", type=float, default=15)
    ap.add_argument("--size", type=float, default=0.3, help="MB file node1 -> node2")
    ap.add_argument("--python", default=sys.executable, help="python with GNU Radio")
    ap.add_argument("--duplex", default=config.DEFAULT_DUPLEX, choices=config.DUPLEX_MODES)
    a = ap.parse_args()
    prof = config.PROFILES[a.profile]
    rate = config.PhyRate(a.sim_rate, prof["sps"], prof["mod"])
    cmd = a.python.split() + [os.path.join(ROOT, "phy", "phy_flowgraph.py"), "--sim", "--profile",
                              a.profile, "--sim-rate", str(a.sim_rate), "--snr", str(a.snr),
                              "--duplex", a.duplex]
    phy = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    threading.Thread(target=lambda: [print("  [phy]", l.rstrip()) for l in phy.stdout
                                     if "vmcirc" not in l], daemon=True).start()
    time.sleep(4)
    events = []
    lock = threading.Lock()

    def mk(n):
        def on(e):
            with lock:
                events.append((n, e))
        return on
    n1 = Transport(1, 2, 52001, 52002, rate, mk("n1"), duplex=a.duplex)
    n2 = Transport(2, 1, 52011, 52012, rate, mk("n2"), duplex=a.duplex)
    n1.start(); n2.start()
    t = time.time()
    while time.time() - t < 10 and not (n1.link_up and n2.link_up):
        time.sleep(0.2)
    print(f"link up: node1={n1.link_up} node2={n2.link_up} (beacons through the modem)")
    payloads = {
        n1.send_text("Hello over the air! සුභ දවසක්"): ("n2", "Hello over the air! සුභ දවසක්".encode()),
        n1.send_bytes(bytes(int(a.size * 1e6)), {"kind": "file", "name": "zeros.bin"}): ("n2", None),
        n2.send_bytes(os.urandom(int(a.size * 0.5e6)), {"kind": "file", "name": "rev.bin"}): ("n1", None),
    }
    sizes = {x: int(a.size * 1e6) for x in list(payloads)[1:2]}
    t0 = time.time()
    ok = True
    pending = set(payloads)
    while pending and time.time() - t0 < 240:
        time.sleep(0.3)
        with lock:
            for n, e in events:
                if e["type"] == "rx_done" and e["xfer"] in pending and payloads[e["xfer"]][0] == n:
                    exp = payloads[e["xfer"]][1]
                    good = e["ok"] and (exp is None or e["data"] == exp)
                    print(f"{n} got {e['meta'].get('name', e['meta'].get('kind'))}: {len(e['data'])} B "
                          f"in {e['seconds']:.2f}s ({e['rate_Bps']/1e3:.1f} KB/s) sha256_ok={good} dups={e['dups']}")
                    ok &= good
                    pending.discard(e["xfer"])
                if e["type"] == "tx_failed":
                    print("TX FAILED", e); ok = False; pending.clear()
            events.clear()
    if pending:
        print("TIMEOUT waiting for", pending); ok = False
    print(f"PHY raw rate in this sim: {rate.byte_rate/1e3:.1f} KB/s; node1 srtt="
          f"{(n1.srtt or 0)*1000:.0f} ms; node1 stats {dict(n1.stats)}")
    print(f"rate ctl: n1 rate={n1._rate:.0f}/{n1._rate_max:.0f} B/s min_rtt={n1.min_rtt*1000:.0f}ms srtt={(n1.srtt or 0)*1000:.0f}ms")
    n1.stop(); n2.stop(); phy.terminate(); phy.wait(5)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
