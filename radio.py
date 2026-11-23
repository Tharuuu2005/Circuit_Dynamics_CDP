"""
Starts / stops the radio process (phy/phy_flowgraph.py) for app.py and cli.py, reads its
health line ("[health] {...}") and sends live changes (TX attenuation, RX gain, F1 / F2).
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time

import config

HERE = os.path.dirname(os.path.abspath(__file__))


class Radio:
    def __init__(self, node, profile, duplex, ports, on_line=None, on_health=None, extra_args=()):
        self.node, self.profile, self.duplex = node, profile, duplex
        self.tx_port, self.rx_port, self.ctrl_port = ports
        self.on_line = on_line or (lambda s: None)
        self.on_health = on_health or (lambda h: None)
        self.extra = list(extra_args)
        self.proc = None
        self.overflows = 0
        self.underflows = 0
        # watchdog state
        self.settings = None             # (f1, f2, tx_atten, rx_gain) of the running radio
        self.want = False                # the user wants the radio on
        self.t_start = self.t_health = 0.0
        self.link_seen = False
        self.down_since = None
        self.up_since = None
        self.backoff = 15.0
        self.pidfile = os.path.join(HERE, f".radio_node{node}.pid")

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def _kill_stale(self):
        """a radio process left by a crashed app keeps the E200 busy: end it first."""
        try:
            with open(self.pidfile) as f:
                pid = int(f.read().strip())
        except (OSError, ValueError):
            return
        try:
            if sys.platform.startswith("win"):
                out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                     capture_output=True, text=True, timeout=5).stdout.lower()
                if "python" in out:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=5)
                    self.on_line(f"ended an old radio process (PID {pid})")
                    time.sleep(1.5)
            else:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    if b"phy_flowgraph" in f.read():
                        os.kill(pid, 9)
                        time.sleep(1.0)
        except Exception:
            pass
        try:
            os.remove(self.pidfile)
        except OSError:
            pass

    def start(self, f1, f2, tx_atten=config.DEFAULT_TX_ATTEN, rx_gain=config.DEFAULT_RX_GAIN):
        if self.running:
            return
        self.settings = (f1, f2, tx_atten, rx_gain)
        self.want = True
        self.t_start = self.t_health = time.monotonic()
        self._kill_stale()
        cmd = [sys.executable, os.path.join(HERE, "phy", "phy_flowgraph.py"), "--node", str(self.node),
               "--profile", self.profile, "--duplex", self.duplex, "--f1", str(int(f1)), "--f2", str(int(f2)),
               "--tx-atten", str(tx_atten), "--rx-gain", str(rx_gain), "--tx-port", str(self.tx_port),
               "--rx-port", str(self.rx_port), "--ctrl-port", str(self.ctrl_port)] + self.extra
        self.on_line("starting radio: " + " ".join(cmd[2:]))
        flags = 0
        if sys.platform.startswith("win"):            # the radio must never wait for the GUI
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.HIGH_PRIORITY_CLASS
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     creationflags=flags)
        try:
            with open(self.pidfile, "w") as f:
                f.write(str(self.proc.pid))
        except OSError:
            pass
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def update_settings(self, f1=None, f2=None, tx_atten=None, rx_gain=None):
        """remember live changes, so a restart by the watchdog keeps them."""
        if self.settings:
            old = self.settings
            self.settings = tuple(o if n is None else n for o, n in zip(old, (f1, f2, tx_atten, rx_gain)))

    def stop(self, by_user=True):
        if by_user:
            self.want = False
        if self.running:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        try:
            os.remove(self.pidfile)
        except OSError:
            pass

    def set_rf(self, **rf):
        """live: tx_atten, rx_gain, f1, f2 (the radio prints '[rf] ...' when done)."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(json.dumps(rf).encode(), ("127.0.0.1", self.ctrl_port))
        s.close()

    def _reader(self, proc):
        for line in proc.stdout:
            line = line.rstrip()
            lead = len(line) - len(line.lstrip("OU"))     # gr-iio marks: O overflow, U underflow
            if lead:
                self.overflows += line[:lead].count("O")
                self.underflows += line[:lead].count("U")
                line = line[lead:]
            if line.startswith("[health] "):
                self.t_health = time.monotonic()
                try:
                    h = json.loads(line[9:])
                    h["overflows"], self.overflows = self.overflows, 0
                    h["underflows"], self.underflows = self.underflows, 0
                    self.on_health(h)
                except ValueError:
                    pass
                continue
            if line.strip() and "vmcircbuf" not in line and "max output buffer" not in line:
                self.on_line(line)
        self.on_line(f"radio process exited (code {proc.poll()})")


    def watchdog(self, link_up):
        """call about once a second. Restarts the radio (and returns why) when its process died,
        it stopped reporting, or the link has been down for a while after it had worked. Waits
        longer after each restart (15 s, 30 s, ... 2 min) so a PC whose peer is simply off is
        not restarted all the time."""
        if not self.want or not self.settings:
            return None
        now = time.monotonic()
        if link_up:                      # track the link first, also while the radio starts
            self.link_seen = True
            self.down_since = None
            if self.up_since is None:
                self.up_since = now
            if now - self.up_since > 60:
                self.backoff = 15.0
        else:
            self.up_since = None
            if self.link_seen and self.down_since is None:
                self.down_since = now
        if now - self.t_start < 5:      # give a (re)started radio time to come up
            return None
        why = None
        if not self.running:
            why = "the radio process stopped"
        elif now - self.t_health > 10 and now - self.t_start > 15:
            why = "the radio stopped reporting for 10 s"
        elif not link_up and self.down_since is not None and now - self.down_since > self.backoff \
                and now - self.t_start > self.backoff:
            why = f"no link for {now - self.down_since:.0f} s"
            self.backoff = min(120.0, self.backoff * 2)
        if why is None:
            return None
        self.stop(by_user=False)
        self.start(*self.settings)
        self.down_since = now if not link_up and self.link_seen else None
        return why


def health_text(h):
    """(text, level) for the radio line: level ok / bad. Both directions of this PC's radio:
    receiver and transmitter speed (must stay ~1.00x real time), TX queue (frames waiting to
    be sent: a growing queue = the transmitter can't keep up)."""
    speed, tx = h.get("speed", 0), h.get("tx_speed")
    parts = [f"receiver {speed:.2f}x"]
    if tx is not None:
        parts.append(f"transmitter {tx:.2f}x real time")
    if h.get("overflows") or h.get("underflows"):
        parts.append(f"{h.get('overflows', 0)} overflows / {h.get('underflows', 0)} underflows")
    if h.get("queue"):
        parts.append(f"TX queue {h['queue']}")
    # short dips (0.95x) are normal Windows timing jitter; only real shortfalls are flagged
    slow = speed < 0.9 or (tx is not None and tx < 0.9) or h.get("overflows") or h.get("underflows") \
        or h.get("queue", 0) > 60
    if slow:
        return " · ".join(parts) + "  -  this PC can't keep up with the radio: plug in the charger, " \
                                   "close other programs, Windows power mode Best performance", "bad"
    return " · ".join(parts), "ok"
