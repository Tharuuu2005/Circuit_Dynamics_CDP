"""
Link security: AES-GCM frames, addresses, replay protection; then two transports with
security over an emulated link: text + 1 MB both ways, an attacker replaying recorded
frames (a captured command must not be delivered again) and a radio with a different
pairing (all its frames rejected).

    python tests/secure_test.py
"""
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import secure  # noqa: E402
import transport  # noqa: E402
import emu  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}", flush=True)


tmp = tempfile.mkdtemp(prefix="sec_")
check("overhead constant matches transport", secure.OVERHEAD == transport.SEC_OVERHEAD)
P = secure.new_pairing()
secure.save_pairing(P, os.path.join(tmp, "pairing.key"))
P2 = secure.load_pairing(os.path.join(tmp, "pairing.key"))
check("pairing file round trip, fingerprint stable", secure.fingerprint(P) == secure.fingerprint(P2))

g = secure.LinkCrypto(P, 1, os.path.join(tmp, "g.state"))
d = secure.LinkCrypto(P, 2, os.path.join(tmp, "d.state"))
frame = bytes([0xA5, 5]) + b"command" + bytes(range(50))
w = g.seal(frame)
check("ciphertext hides the content", b"command" not in w)
check("decrypts to the same frame", d.open(w) == frame)
check("the same frame again is rejected (replay)", d.open(w) is None and d.stats["replay"] == 1)
bad = bytearray(g.seal(frame)); bad[-20] ^= 1
check("one flipped bit -> rejected (auth)", d.open(bytes(bad)) is None and d.stats["auth"] == 1)
bad = bytearray(g.seal(frame)); bad[1] = 1
check("changed type byte in the header -> rejected", d.open(bytes(bad)) is None)
check("a frame from the same node number (misconfigured PC) is rejected", g.open(g.seal(frame)) is None and g.stats["same_node"] == 1)
ws = [g.seal(frame) for _ in range(10)]
check("frames arriving out of order are accepted once",
      all(d.open(x) == frame for x in ws[::-1]) and all(d.open(x) is None for x in ws))
Q = secure.new_pairing()
x = secure.LinkCrypto(Q, 1)
check("radio with another pairing -> rejected", d.open(x.seal(frame)) is None and d.stats["foreign"] >= 1)
Q["n1"], Q["n2"] = P["n1"], P["n2"]          # same addresses, wrong key
x = secure.LinkCrypto(Q, 1)
check("same addresses but wrong key -> rejected", d.open(x.seal(frame)) is None)
old = g.seal(frame)
time.sleep(1.1)
g2 = secure.LinkCrypto(P, 1, os.path.join(tmp, "g.state"))   # node 1 restarted
check("restarted sender: new epoch accepted", d.open(g2.seal(frame)) == frame)
check("frame recorded before the restart -> rejected", d.open(old) is None)
d2 = secure.LinkCrypto(P, 2, os.path.join(tmp, "d.state"))   # node 2 restarted too
check("... also after the receiver restarted (epoch saved)", d2.open(old) is None)

# ------------------------------------------------------------------ two transports
rate = config.get_profile("robust")
p1, p2 = (53001, 53002), (53011, 53012)
a, b = emu.link(p1, p2, rate.byte_rate, loss=0.02)
cap = []
a.tap = lambda pkt: cap.append(pkt)                  # attacker records node 1's transmissions
events = {1: [], 2: []}
c1 = secure.LinkCrypto(P, 1)
c2 = secure.LinkCrypto(P, 2)
t1 = transport.Transport(1, 2, p1[0], p1[1], rate, on_event=events[1].append, crypto=c1)
t2 = transport.Transport(2, 1, p2[0], p2[1], rate, on_event=events[2].append, crypto=c2)
t1.start(); t2.start()
t0 = time.time()
while not (t1.link_up and t2.link_up) and time.time() - t0 < 5:
    time.sleep(0.05)
check("link up with encryption", t1.link_up and t2.link_up)


def wait_rx(ev, n, timeout):
    t = time.time()
    while time.time() - t < timeout:
        got = [e for e in ev if e["type"] == "rx_done"]
        if len(got) >= n:
            return got
        time.sleep(0.02)
    return [e for e in ev if e["type"] == "rx_done"]


cap.clear()
t1.send_text("command door 1")
got = wait_rx(events[2], 1, 3)
check("command delivered", got and got[0]["data"] == b"command door 1")
data1, data2 = os.urandom(1_000_000), os.urandom(500_000)
ts = time.time()
t1.send_bytes(data1, {"kind": "file", "name": "a"})
t2.send_bytes(data2, {"kind": "file", "name": "b"})
r2 = wait_rx(events[2], 2, 40)
r1 = wait_rx(events[1], 1, 40)
dt = time.time() - ts
good = len(r2) == 2 and r2[1]["data"] == data1 and len(r1) == 1 and r1[0]["data"] == data2
check(f"1 MB + 0.5 MB both ways, intact ({1e6 / max(1e-3, r2[-1]['seconds']) / 1e3:.0f} KB/s "
      f"of {rate.byte_rate / 1e3:.0f} raw)", good)
n_before = len([e for e in events[2] if e["type"] == "rx_done"])
rej_before = c2.stats["rejected"]
for pkt in cap[:20]:                                # replay the recorded command frames
    a.inject(pkt)
time.sleep(1.0)
n_after = len([e for e in events[2] if e["type"] == "rx_done"])
check(f"replayed command frames rejected ({c2.stats['rejected'] - rej_before} dropped), "
      f"nothing delivered again", n_after == n_before and c2.stats["rejected"] - rej_before >= 1)
evil = secure.LinkCrypto(secure.new_pairing(), 1)
f = transport.DATA_HDR.pack(0xA5, 5, 1, 2, 999, 0, 1, 1) + b"x"
for _ in range(5):
    a.inject(evil.seal(f))
time.sleep(0.3)
check("frames from a wrong-key radio counted as rejected", c2.stats["foreign"] >= 5)
t1.stop(); t2.stop()
a.running = b.running = False
print("RESULT:", "PASS" if ok else "FAIL")
