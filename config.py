"""
Shared configuration for the radio (phy/phy_flowgraph.py), the transport and the app.
Both PCs must use the SAME profile, duplex mode and frequencies.

Throughput (raw PHY bit rate) = samp_rate / sps   (BPSK, 1 bit per symbol)

  profile   samp_rate  sps  raw rate   file goodput
  robust      4 Msps    4   1.0 Mbps   ~110 KB/s
  balanced   10 Msps    4   2.5 Mbps   ~280 KB/s   (needs a fast PC: watch the radio line)
  lean        2 Msps    2   1.0 Mbps   ~110 KB/s   (same signal as robust, half the PC <-> E200 traffic)

Frequencies: node 1 transmits on F1 and listens on F2; node 2 does the opposite.
Defaults F1 = 3300 MHz, F2 = 3375 MHz. Change them in the app (F1 / F2 + Apply) or with
--f1 / --f2 (MHz); the choice is saved in radio_settings.json next to this file.
"""
import json
import os
from dataclasses import dataclass

VERSION = "5.3"

PROFILES = {
    "robust":   dict(samp_rate=4_000_000,  sps=4, mod="bpsk"),
    "balanced": dict(samp_rate=10_000_000, sps=4, mod="bpsk"),
    # same 1 Mbit/s signal on the air as robust, but half the samples between the PC and the
    # E200 (2 instead of 4 samples per symbol): for a PC whose link to the E200 can't carry
    # 4 Msps both ways (USB 2.0 port or USB-Ethernet adapter: "transmitter 0.3x real time")
    "lean":     dict(samp_rate=2_000_000,  sps=2, mod="bpsk"),
}
DEFAULT_PROFILE = "robust"

# ------------------------------------------------------------------ duplex modes
# fdd       (default) both directions at the same time; each radio is SILENT when it has
#           nothing to send. A radio whose own transmitter deafens its receiver then only
#           misses what arrives during its own short ACKs, instead of everything.
# fdd-cont  both directions at the same time, radios transmit idle symbols continuously
#           (the previous FDD: fine only if both radios have good TX/RX isolation).
# tdd       take turns: send a burst, then listen for the ACK (slowest, most tolerant).
DUPLEX_MODES = ("fdd", "fdd-cont", "tdd")
DEFAULT_DUPLEX = "fdd"

# ------------------------------------------------------------------ frequencies
F1_DEFAULT = 3_300_000_000       # node 1 TX  /  node 2 RX
F2_DEFAULT = 3_375_000_000       # node 2 TX  /  node 1 RX
FREQ_MIN, FREQ_MAX = 70_000_000, 6_000_000_000
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "radio_settings.json")

NODES = {
    1: dict(uri="ip:192.168.1.10", tx_port=52001, rx_port=52002, ctrl_port=52003),
    2: dict(uri="ip:192.168.1.10", tx_port=52001, rx_port=52002, ctrl_port=52003),
}
# Both nodes on ONE PC (tests, tools/fake_radio.py): node 2 uses these ports instead.
SAME_PC_PORTS = {1: (52001, 52002, 52003), 2: (52011, 52012, 52013)}


def check_freqs(f1, f2):
    """(error, warning) for F1 / F2 in Hz; error None = usable."""
    if not (FREQ_MIN <= f1 <= FREQ_MAX and FREQ_MIN <= f2 <= FREQ_MAX):
        return "frequencies must be between 70 and 6000 MHz", None
    gap = abs(f1 - f2)
    if gap < 10_000_000:
        return "F1 and F2 must be at least 10 MHz apart (each radio transmits and receives at once)", None
    if gap < 40_000_000:
        return None, (f"F1 and F2 only {gap / 1e6:.0f} MHz apart: a radio's own transmitter may "
                      "deafen its receiver. If one direction is slow, move them further apart.")
    return None, None


def load_freqs():
    try:
        with open(SETTINGS_FILE) as f:
            d = json.load(f)
        f1, f2 = int(round(float(d["f1_mhz"]) * 1e6)), int(round(float(d["f2_mhz"]) * 1e6))
        if check_freqs(f1, f2)[0] is None:
            return f1, f2
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return F1_DEFAULT, F2_DEFAULT


def save_freqs(f1, f2):
    with open(SETTINGS_FILE, "w") as f:
        json.dump({"f1_mhz": f1 / 1e6, "f2_mhz": f2 / 1e6}, f)


def freqs_from_args(f1_mhz, f2_mhz):
    """--f1 / --f2 in MHz (None = saved / default) -> (f1, f2) Hz; checked and remembered."""
    s1, s2 = load_freqs()
    f1 = int(round(f1_mhz * 1e6)) if f1_mhz else s1
    f2 = int(round(f2_mhz * 1e6)) if f2_mhz else s2
    err, warn = check_freqs(f1, f2)
    if err:
        raise SystemExit(f"--f1 {f1 / 1e6:g} --f2 {f2 / 1e6:g}: {err}")
    if warn:
        print("WARNING: " + warn, flush=True)
    if f1_mhz or f2_mhz:
        save_freqs(f1, f2)
    return f1, f2


def node_freqs(node, f1, f2):
    """(tx, rx) Hz of this node."""
    return (f1, f2) if node == 1 else (f2, f1)


# ------------------------------------------------------------------ framing
EXCESS_BW = 0.35
ACCESS_CODE = "1010110011011101101001001110001011110010100011000010000011111100"
PREAMBLE_LEN = 4        # bytes of 0x55 before the access code (receiver already locked)
POSTAMBLE_LEN = 2
BURST_PREAMBLE_LEN = 128 # quiet FDD: a frame after silence gets this much preamble (8 x 0x55 + random)
TDD_PREAMBLE_LEN = 64
HANGOVER_SYMBOLS = 2048  # quiet FDD: keep sending idle symbols this long after a frame

PHY_MTU = 1024           # max UDP datagram handed to GNU Radio (Socket PDU MTU 2048)
PHY_OVERHEAD = PREAMBLE_LEN + 8 + 4 + 4 + POSTAMBLE_LEN

BITS_PER_SYMBOL = {"bpsk": 1}
DEFAULT_TX_ATTEN = 10.0
DEFAULT_RX_GAIN = 50.0


@dataclass
class PhyRate:
    samp_rate: float
    sps: int
    mod: str

    @property
    def bit_rate(self) -> float:
        return self.samp_rate / self.sps * BITS_PER_SYMBOL[self.mod]

    @property
    def byte_rate(self) -> float:
        return self.bit_rate / 8.0

    def frame_airtime(self, udp_len: int) -> float:
        """Seconds of airtime for one UDP datagram of udp_len bytes."""
        return (udp_len + PHY_OVERHEAD) / self.byte_rate


def get_profile(name: str) -> PhyRate:
    p = PROFILES[name]
    return PhyRate(p["samp_rate"], p["sps"], p["mod"])


def rf_bandwidth(samp_rate: float, sps: int) -> int:
    """Analog RF filter bandwidth: ~1.5x the occupied bandwidth, within AD9361 limits."""
    occupied = (1 + EXCESS_BW) * samp_rate / sps
    return int(min(max(1.5 * occupied, 1_000_000), samp_rate))
