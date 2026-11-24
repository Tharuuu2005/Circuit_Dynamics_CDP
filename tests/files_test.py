"""
File transfer system end to end: two nodes (core.Node + files.FileManager, encrypted
transport) over an emulated radio link at the robust rate with frame loss. No GNU Radio.

  * files of different kinds and sizes (also empty), a folder (zipped), SHA-256 checked
  * speed of a large file
  * the link drops in the middle of a file for longer than the ARQ gives up after: the file
    continues where it stopped once the link is back (only the lost blocks are sent again)
  * cancelling at the sender removes the partial file at the receiver

    python tests/files_test.py
"""
import hashlib
import os
import sys
import tempfile
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import core  # noqa: E402
import files  # noqa: E402
import secure  # noqa: E402
import emu  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}", flush=True)


def wait(cond, timeout):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return time.time() - t
        time.sleep(0.05)
    return None


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


tmp = tempfile.mkdtemp(prefix="files_")
P = secure.new_pairing()
rate = config.get_profile("robust")
p1, p2 = (56001, 56002, 56003), (56011, 56012, 56013)
d12, d21 = emu.link(p1, p2, rate.byte_rate, loss=0.02)
fm = {}
nodes = {}
for n, ports in ((1, p1), (2, p2)):
    dd = os.path.join(tmp, f"node{n}")
    s = core.Settings(os.path.join(dd, "settings.json"), {"node": n})
    node = core.Node(n, P, s, data_dir=dd, ports=ports, start_radio=False)
    m = files.FileManager(node, os.path.join(dd, "received"))
    node.on_event = m.on_event
    nodes[n], fm[n] = node, m
    node.start()
check("link up", wait(lambda: all(x.tr.link_state == "up" for x in nodes.values()), 8) is not None)

src = os.path.join(tmp, "src")
os.makedirs(os.path.join(src, "folder", "sub"))
paths = {
    "notes.txt": b"hello from node 1\n" * 20,
    "empty.bin": b"",
    "photo.jpg": os.urandom(180_000),
    "big.bin": os.urandom(3_000_000),
}
for name, data in paths.items():
    with open(os.path.join(src, name), "wb") as f:
        f.write(data)
for k in range(3):
    with open(os.path.join(src, "folder", "sub", f"f{k}.txt"), "w") as f:
        f.write("line\n" * 1000 * (k + 1))

fids = fm[1].send_paths([os.path.join(src, n) for n in paths] + [os.path.join(src, "folder")])


def all_done(m, ids):
    return all(m.items.get(i) and m.items[i].status == "done" for i in ids)


t = wait(lambda: all_done(fm[1], fids), 90)
check(f"5 items delivered and verified at the sender ({t:.1f} s)" if t else "5 items delivered", t is not None)
rx = {fm[2].items[f].name: fm[2].items[f].path for f in fids if f in fm[2].items}
check("all files intact at the receiver (SHA-256)",
      all(n in rx and sha(rx[n]) == hashlib.sha256(d).hexdigest() for n, d in paths.items()))
check("folder arrived as folder.zip with its 3 files",
      "folder.zip" in rx and len(__import__("zipfile").ZipFile(rx["folder.zip"]).namelist()) == 3)
big = fm[1].items[fids[3]].record()
check(f"3 MB at {big['rate'] / 1e3:.0f} KB/s of {rate.byte_rate / 1e3:.0f} KB/s raw (2 % frame loss, encrypted)",
      big["rate"] and big["rate"] > 0.75 * rate.byte_rate)

# link drop in the middle of a file
data = os.urandom(4_000_000)
pbig = os.path.join(src, "resume.bin")
open(pbig, "wb").write(data)
f0 = nodes[1].tr.stats["tx_data"]
fid = fm[1].send_paths([pbig])[0]
wait(lambda: fm[1].items[fid].progress > 0.35, 60)
at = fm[1].items[fid].progress
d12.loss = d21.loss = 1.0
time.sleep(25)                      # longer than the ARQ's 20 s give-up time
check("link lost in the middle of the file", nodes[1].tr.link_state == "down")
d12.loss = d21.loss = 0.02
t = wait(lambda: fm[1].items[fid].status == "done", 120)
it = fm[1].items[fid]
frames = nodes[1].tr.stats["tx_data"] - f0
need = len(data) / 977
check(f"file continued after the drop (dropped at {100 * at:.0f} %, {it.resends} blocks sent again, "
      f"{frames / need:.2f}x the frames of one copy)", t is not None and frames < 1.5 * need)
check("... and arrived intact", fm[2].items[fid].path and sha(fm[2].items[fid].path) == hashlib.sha256(data).hexdigest())

# cancel
fid = fm[1].send_paths([pbig])[0]
wait(lambda: fm[1].items[fid].progress > 0.2, 60)
fm[1].cancel(fid)
gone = wait(lambda: fid in fm[2].items and fm[2].items[fid].status == "cancelled", 5)
check("cancel at the sender ends the transfer at the receiver too", gone is not None)
time.sleep(0.5)
check("... and the partial file is removed", not any(fid in n for n in os.listdir(fm[2].inc_dir)))
check("history written", len(fm[1].history) >= 7 and os.path.exists(fm[1].hist_path))
check("telemetry recorded (CSV)", any(f.endswith(".csv") for f in os.listdir(os.path.join(nodes[1].data_dir, "telemetry"))))
for n in nodes.values():
    n.stop()
d12.running = d21.running = False
print("RESULT:", "PASS" if ok else "FAIL")
