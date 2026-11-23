"""
Radio watchdog (radio.Radio.watchdog) with a fake radio process (no GNU Radio needed):
restarts a radio that died, one that stopped reporting, and one whose link has stayed down
after it had worked; leaves alone a radio the user stopped. Takes about a minute.

    python tests/watchdog_test.py
"""
import os
import sys
import tempfile
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
import radio  # noqa: E402

FAKE = r'''
import json, os, sys, time
mode = os.environ.get("FAKE_RADIO", "ok")
print("PHY running.", flush=True)
t0 = time.time()
while True:
    time.sleep(0.5)
    if mode == "die" and time.time() - t0 > 2:
        sys.exit(1)
    if mode == "hang" and time.time() - t0 > 2:
        time.sleep(1000)
    print("[health] " + json.dumps({"speed": 1.0, "tx_speed": 1.0}), flush=True)
'''

ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}", flush=True)


tmp = tempfile.mkdtemp(prefix="wd_")
os.makedirs(os.path.join(tmp, "phy"))
with open(os.path.join(tmp, "phy", "phy_flowgraph.py"), "w") as f:
    f.write(FAKE)
radio.HERE = tmp                                   # the fake radio replaces phy/phy_flowgraph.py


def mk():
    r = radio.Radio(1, "robust", "fdd", (59001, 59002, 59003))
    r.pidfile = os.path.join(tmp, "pid")
    return r


def run_for(r, seconds, link):
    why = []
    t = time.time()
    while time.time() - t < seconds:
        w = r.watchdog(link(time.time() - t))
        if w:
            why.append(w)
        time.sleep(0.5)
    return why


os.environ["FAKE_RADIO"] = "ok"
r = mk(); r.start(3300e6, 3375e6)
check("healthy radio with a working link is left alone", run_for(r, 8, lambda t: True) == [])
r.stop()
check("radio stopped by the user is not restarted", run_for(r, 6, lambda t: False) == [] and not r.running)

os.environ["FAKE_RADIO"] = "die"
r = mk(); r.start(3300e6, 3375e6)
w = run_for(r, 9, lambda t: True)
check(f"radio process that dies is restarted ({w[:1]})", any("stopped" in x for x in w))
r.stop()

os.environ["FAKE_RADIO"] = "hang"
r = mk(); r.start(3300e6, 3375e6)
w = run_for(r, 18, lambda t: True)
check(f"radio that stops reporting is restarted ({w[:1]})", any("reporting" in x for x in w))
r.stop()

os.environ["FAKE_RADIO"] = "ok"
r = mk(); r.start(3300e6, 3375e6)
w = run_for(r, 26, lambda t: t < 5)          # link up for 5 s, then gone
check(f"link down 15 s after it had worked -> restart ({w[:1]})", any("no link" in x for x in w))
check("... and it waits longer before the next try", r.backoff > 15)
r.stop()
r = mk(); r.start(3300e6, 3375e6)
check("link never came up (peer off): no restarts", run_for(r, 20, lambda t: False) == [])
r.stop()
print("RESULT:", "PASS" if ok else "FAIL")
