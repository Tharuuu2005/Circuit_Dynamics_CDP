"""
The one-slow-direction problem, through the REAL GNU Radio modem (both nodes simulated):
node 2's receiver is swamped by noise whenever its OWN transmitter is on (self-
desensitisation, what one E200 does on the hardware). Each direction is measured alone.

  fdd-cont: node 2 transmits idle symbols all the time -> it is always deaf -> data INTO
            node 2 crawls or fails, data OUT of node 2 is fine (the reported symptom).
  fdd:      quiet FDD, node 2 is silent when it has nothing to send -> it is deaf only
            during its own short ACKs -> both directions fast.

    python tests/deaf_sim_test.py                  # both modes
    python tests/deaf_sim_test.py --modes fdd --deaf-db 3
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


def run(mode, a):
    prof = config.PROFILES[a.profile]
    rate = config.PhyRate(a.sim_rate, prof["sps"], prof["mod"])
    cmd = a.python.split() + [os.path.join(ROOT, "phy", "phy_flowgraph.py"), "--sim", "--profile", a.profile,
                              "--sim-rate", str(a.sim_rate), "--snr", str(a.snr), "--duplex", mode,
                              "--sim-deaf", str(a.deaf_node), "--sim-deaf-db", str(a.deaf_db)]
    phy = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log = []
    threading.Thread(target=lambda: [log.append(ln) for ln in phy.stdout], daemon=True).start()
    time.sleep(4)
    if phy.poll() is not None:
        raise SystemExit("simulated radio did not start (ports busy? another phy_flowgraph.py running?):\n"
                         + "".join(log[-5:]))
    ev = []
    n1 = Transport(1, 2, 52001, 52002, rate, lambda e: ev.append(("n1", e)), duplex=mode)
    n2 = Transport(2, 1, 52011, 52012, rate, lambda e: ev.append(("n2", e)), duplex=mode)
    n1.start(); n2.start()
    t = time.time()
    while time.time() - t < 10 and not (n1.link_up and n2.link_up):
        time.sleep(0.2)
    res = {}
    size = int(a.kb * 1000)
    for name, snd, rcv in (("node 1 -> node 2 (INTO the deaf radio)", n1, "n2"),
                           ("node 2 -> node 1 (out of the deaf radio)", n2, "n1")):
        ev.clear()
        data = os.urandom(size)
        x = snd.send_bytes(data, {"kind": "file", "name": "f.bin"})
        t0 = time.time()
        got = txd = None
        while time.time() - t0 < a.timeout and (got is None or txd is None):
            time.sleep(0.2)
            for n, e in list(ev):
                if n == rcv and e["type"] == "rx_done" and e["xfer"] == x:
                    got = e
                if e["type"] == "tx_done" and e["xfer"] == x and n != rcv:
                    txd = e
        if got and got["ok"] and got["data"] == data:
            frac = got["rate_Bps"] / rate.byte_rate
            res[name] = frac
            st = snd.stats
            print(f"  {mode:8s} {name}: {size / 1000:.0f} KB in {got['seconds']:.1f} s = {100 * frac:.0f}% of the "
                  f"raw rate (~{frac * 125:.0f} KB/s on the real 1 Mbit/s link)"
                  + (f"\n           frames {txd and txd['frames']}, retransmitted {txd and txd['retx']}, receiver dups "
                     f"{got['dups']}, ACKs received {st['rx_ack']}, listen-mode {st['listen_mode']}, RTO {st['rto']}"
                     if a.verbose else ""), flush=True)
        else:
            res[name] = 0.0
            print(f"  {mode:8s} {name}: not delivered within {a.timeout:.0f} s", flush=True)
    n1.stop(); n2.stop(); phy.terminate(); phy.wait(5)
    time.sleep(1)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="robust", choices=list(config.PROFILES))
    ap.add_argument("--sim-rate", type=float, default=1e6, help="limited by this PC's CPU")
    ap.add_argument("--snr", type=float, default=15)
    ap.add_argument("--deaf-db", type=float, default=0.0, help="self-interference vs wanted signal")
    ap.add_argument("--kb", type=float, default=150)
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--modes", default="fdd-cont,fdd")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--deaf-node", type=int, default=2, choices=(0, 2), help="0 = no deaf radio (baseline)")
    ap.add_argument("--python", default=sys.executable, help="python with GNU Radio")
    ap.add_argument("--pass-frac", type=float, default=0.4, help="(the sim itself is CPU-limited)")
    a = ap.parse_args()
    out = {m: run(m, a) for m in a.modes.split(",")}
    ok = True
    if "fdd" in out:
        ok = all(v >= a.pass_frac for v in out["fdd"].values())
        print(f"quiet FDD, both directions at least {a.pass_frac:.0%} of the raw rate: {ok}")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
