#!/usr/bin/env python3
"""
Headless SDR Link (demos, benchmarks, debugging): the same encrypted link and file transfer as
the app, from a terminal. Uses the app's pairing key and settings in data/.

    python cli.py --node 2 listen                   # receive files and messages
    python cli.py --node 1 text "hello"             # a message (shows in the app's Messages)
    python cli.py --node 1 send photo.png notes/    # files / folders (resumable, SHA-256 checked)
    python cli.py --node 1 bench --mb 4             # random 4 MB, prints throughput
    add --start-phy to start this PC's radio too (otherwise the app's radio or GRC must run)

Both PCs: same profile, duplex and F1 / F2 (data/settings.json, or the app's hidden panel).
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import core  # noqa: E402
import files  # noqa: E402
import media  # noqa: E402
import secure  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", type=int, choices=(1, 2))
    ap.add_argument("--data", default=config.DATA_DIR)
    ap.add_argument("--start-phy", action="store_true", help="start this PC's radio too")
    ap.add_argument("--same-pc", action="store_true", help="both nodes on one PC (tools/fake_radio.py)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listen")
    t = sub.add_parser("text")
    t.add_argument("message")
    s = sub.add_parser("send")
    s.add_argument("paths", nargs="+")
    b = sub.add_parser("bench")
    b.add_argument("--mb", type=float, default=4.0)
    a = ap.parse_args()

    st = core.Settings.load(a.data)
    node_no = a.node or st.node
    pairing = secure.load_pairing(os.path.join(a.data, "pairing.key"))
    if node_no is None or pairing is None:
        raise SystemExit("Start the app once on this PC first (it sets the node number and the pairing key), "
                         "or copy data/pairing.key here and pass --node.")
    ports = config.SAME_PC_PORTS[node_no] if a.same_pc else None
    events = []
    node = core.Node(node_no, pairing, st, on_event=events.append, data_dir=a.data, ports=ports,
                     start_radio=a.start_phy)
    fm = files.FileManager(node, st.recv_dir or os.path.join(a.data, "received"))
    node.start()
    tx, rx = config.node_freqs(node_no, st.f1, st.f2)
    print(f"node {node_no}, pairing code {secure.fingerprint(pairing)}, {st.profile} "
          f"({node.rate.bit_rate / 1e6:.2f} Mbit/s), {st.duplex}, TX {tx / 1e6:g} MHz, RX {rx / 1e6:g} MHz")
    t0 = time.time()
    while node.tr.link_state != "up" and time.time() - t0 < 20:
        time.sleep(0.2)
    print("link up" if node.tr.link_state == "up" else "no link yet: " + " - ".join(node.diagnosis() or ()))

    def pump_events():
        while events:
            e = events.pop(0)
            if fm.on_event(e):
                continue
            if e["type"] == "msg" and e["kind"] == "chat":
                print(f"[message] {e['obj'].get('text')}")
            elif e["type"] == "file" and e["meta"].get("kind") == "bench":
                print(f"[bench] received {media.human(len(e['data']))}")
            elif e["type"] == "link":
                print(f"[link] {e['state']}")
            elif e["type"] == "restarted":
                print(f"[radio] restarted automatically: {e['why']}")

    def show(fids):
        line = "  ".join(f"{fm.items[f].name} {100 * fm.items[f].progress:.0f}% {fm.items[f].status}"
                         for f in fids if f in fm.items)
        last = node.samples[-1] if node.samples else {}
        print(f"\r{line}   [{last.get('goodput_out_KBps', 0)} KB/s out, {last.get('goodput_in_KBps', 0)} KB/s in]   ",
              end="", flush=True)

    try:
        if a.cmd == "text":
            node.send_msg("chat", {"text": a.message})
            time.sleep(2)
            pump_events()
        elif a.cmd == "send":
            fids = fm.send_paths(a.paths)
            while any(fm.items[f].active for f in fids):
                pump_events()
                show(fids)
                time.sleep(0.5)
            print()
            for f in fids:
                r = fm.items[f].record()
                print(f"{r['name']}: {r['status']}" + (f", {media.human(r['size'])} in {r['seconds']} s = "
                                                      f"{r['rate'] / 1e3:.1f} KB/s" if r.get("rate") else "")
                      + (f" ({r['error']})" if r.get("error") else ""))
        elif a.cmd == "bench":
            n = int(a.mb * 1e6)
            done = []
            node.on_event = lambda e: done.append(e) if e["type"] in ("sent", "failed") else events.append(e)
            t1 = time.time()
            node.send_data(os.urandom(n), {"kind": "bench", "name": f"bench_{a.mb:g}MB"})
            while not done:
                pump_events()
                last = node.samples[-1] if node.samples else {}
                print(f"\r  {last.get('goodput_out_KBps', 0)} KB/s", end="", flush=True)
                time.sleep(0.5)
            e = done[0]
            if e["type"] == "sent":
                dt = time.time() - t1
                print(f"\n{media.human(n)} in {dt:.1f} s = {n / dt / 1e3:.1f} KB/s, "
                      f"{e['frames']} frames, {e['retx']} resent")
            else:
                print(f"\nfailed: {e['reason']}")
        else:
            print("listening (Ctrl+C stops); received files go to", fm.recv_dir)
            seen = set()
            while True:
                pump_events()
                for f, it in list(fm.items.items()):
                    if it.direction == "in" and not it.active and f not in seen:
                        seen.add(f)
                        r = it.record()
                        print(f"\n[file] {r['name']} {media.human(r['size'])}: {r['status']}"
                              + (f", {r['seconds']} s, {r['rate'] / 1e3:.1f} KB/s -> {r['path']}" if r.get("rate") else ""))
                time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        last = node.samples[-1] if node.samples else {}
        print(f"\nframes getting through: to the other PC {last.get('frames_through_to_peer')}, "
              f"from it {last.get('frames_through_from_peer')}")
        node.stop()


if __name__ == "__main__":
    main()
