#!/usr/bin/env python3
"""
Headless CLI for the SDR link (handy for demos, benchmarking and debugging).

    python cli.py --node 2 listen                       # receive & save everything
    python cli.py --node 1 text "hello"                 # send a text message
    python cli.py --node 1 send photo.png               # send a file (lossless)
    python cli.py --node 1 send photo.png --compress 60 # JPEG-compress first
    python cli.py --node 1 bench --mb 4                 # random 4 MB, prints throughput
    python cli.py --node 1 --start-phy bench --mb 4     # ... and start the radio too

Without --start-phy the radio (phy/phy_flowgraph.py or the GRC flowgraph) must already run.
Both PCs: same --profile, --duplex and F1 / F2 (--f1 / --f2 in MHz, remembered).
"""
import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import media  # noqa: E402
from radio import Radio, health_text  # noqa: E402
from transport import Transport  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", type=int, choices=(1, 2), required=True)
    ap.add_argument("--profile", choices=list(config.PROFILES), default=config.DEFAULT_PROFILE)
    ap.add_argument("--duplex", choices=config.DUPLEX_MODES, default=config.DEFAULT_DUPLEX)
    ap.add_argument("--f1", type=float, default=None, metavar="MHZ", help="node 1 TX / node 2 RX")
    ap.add_argument("--f2", type=float, default=None, metavar="MHZ", help="node 2 TX / node 1 RX")
    ap.add_argument("--start-phy", action="store_true", help="start the radio too")
    ap.add_argument("--same-pc", action="store_true", help="both nodes on one PC (tools/fake_radio.py)")
    ap.add_argument("--tx-port", type=int)
    ap.add_argument("--rx-port", type=int)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listen")
    t = sub.add_parser("text"); t.add_argument("message")
    s = sub.add_parser("send"); s.add_argument("path"); s.add_argument("--compress", type=int, default=0)
    b = sub.add_parser("bench"); b.add_argument("--mb", type=float, default=4.0)
    a = ap.parse_args()
    f1, f2 = config.freqs_from_args(a.f1, a.f2)

    node = config.NODES[a.node]
    peer = 2 if a.node == 1 else 1
    rate = config.get_profile(a.profile)
    done = threading.Event()
    result = {}
    t_last = [0.0]

    def on(e):
        typ = e["type"]
        if typ == "link":
            print(f"[link] {'UP' if e['up'] else 'DOWN'}")
        elif typ == "tx_progress" and time.time() - t_last[0] > 0.5:
            t_last[0] = time.time()
            print(f"  tx {100*e['acked']/e['total']:5.1f}%  {e['rate_Bps']/1e3:6.0f} KB/s  retx={e['retx']}", end="\r")
        elif typ == "tx_done":
            print(f"\n[tx] delivered {media.human(e['bytes'])} in {e['seconds']:.2f}s = "
                  f"{e['rate_Bps']/1e3:.0f} KB/s ({e['rate_Bps']*8/1e6:.2f} Mbps), frames={e['frames']}, "
                  f"retx={e['retx']}")
            result.update(e); done.set()
        elif typ == "tx_failed":
            print(f"\n[tx] FAILED: {e['reason']}"); result.update(e); done.set()
        elif typ == "rx_start":
            print(f"[rx] incoming transfer {e['xfer']} ({e['total']} frames)")
        elif typ == "rx_progress" and time.time() - t_last[0] > 0.5:
            t_last[0] = time.time()
            print(f"  rx {100*e['got']/e['total']:5.1f}%  {e['rate_Bps']/1e3:6.0f} KB/s", end="\r")
        elif typ == "rx_done":
            m = e["meta"]
            if not e["ok"]:
                print("\n[rx] integrity check FAILED"); return
            if m.get("kind") == "text":
                print(f"[rx] text from node {e['src']}: {e['data'].decode('utf-8', 'replace')}")
            else:
                path = media.save_received(e["data"], m)
                print(f"\n[rx] {m.get('name')} {media.human(len(e['data']))} in {e['seconds']:.2f}s "
                      f"({e['rate_Bps']/1e3:.0f} KB/s) -> {path}")
                if m.get("kind") == "audio":
                    media.open_file(path)

    ports = config.SAME_PC_PORTS[a.node] if a.same_pc else (
        a.tx_port or node["tx_port"], a.rx_port or node["rx_port"], node["ctrl_port"])
    tr = Transport(a.node, peer, ports[0], ports[1], rate, on, duplex=a.duplex)
    tr.start()
    radio = None
    if a.start_phy:
        radio = Radio(a.node, a.profile, a.duplex, ports, on_line=lambda s: print("[radio] " + s, flush=True),
                      on_health=lambda h: print("[radio] " + health_text(h)[0], flush=True))
        radio.start(f1, f2)

        def watch():
            while True:
                time.sleep(1.0)
                why = radio.watchdog(tr.link_up)
                if why:
                    print(f"[radio] restarted automatically: {why}", flush=True)
        threading.Thread(target=watch, daemon=True).start()
        time.sleep(3)
    tx, rx = config.node_freqs(a.node, f1, f2)
    print(f"node {a.node}, {a.profile} ({rate.bit_rate/1e6:.2f} Mbps raw), {a.duplex}, TX {tx/1e6:g} MHz, "
          f"RX {rx/1e6:g} MHz. Waiting for link...")
    t0 = time.time()
    while not tr.link_up and time.time() - t0 < 15:
        time.sleep(0.1)
    if not tr.link_up:
        print("WARNING: no beacons from the peer yet - is the PHY running on both nodes?")
    try:
        if a.cmd == "listen":
            while True:
                time.sleep(1)
        if a.cmd == "text":
            tr.send_text(a.message)
        elif a.cmd == "send":
            data, meta = media.load_for_send(a.path, compress=a.compress > 0, quality=a.compress or 70)
            print(f"sending {meta['name']} {media.human(len(data))} "
                  f"(est. {len(data)/(rate.byte_rate*0.9):.1f}s)")
            tr.send_bytes(data, meta)
        elif a.cmd == "bench":
            data = os.urandom(int(a.mb * 1e6))
            tr.send_bytes(data, {"kind": "file", "name": f"bench_{a.mb:g}MB.bin"})
        if a.cmd == "text":
            time.sleep(2)
        else:
            done.wait()
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        q = (tr.current_quality_out, tr.current_quality_in)
        if any(x is not None for x in q):
            print("frames getting through: to the peer " + ("-" if q[0] is None else f"{100*q[0]:.0f}%")
                  + ", from the peer " + ("-" if q[1] is None else f"{100*q[1]:.0f}%"))
        tr.stop()
        if radio:
            radio.stop()


if __name__ == "__main__":
    main()
