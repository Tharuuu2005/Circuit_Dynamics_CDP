"""
Link security: unique node addresses, AES-256-GCM on every frame, replay protection.

Pairing file (data/pairing.key, created once on one PC and copied to the other):
    {"v": 2, "key": <32 random bytes, base64>, "n1": <32-bit address>, "n2": <32-bit address>}
Both PCs hold the same file; the radio node number (1 or 2) picks which address is "me".

Every frame the transport sends goes on the air as

    A5 | type | dst addr(4) | src addr(4) | epoch(4) | counter(4) | AES-GCM( frame[2:] ) | tag(16)
    (the first 18 bytes are the header: authenticated, not secret)

* The type byte stays in the clear so the radio's framer can still put ACKs and commands
  ahead of video and file data. Changing any header byte breaks the tag.
* Nonce = src addr | epoch | counter: never repeats (epoch = the start time in seconds, saved,
  always increasing; counter +1 per frame).
* Receiver: wrong destination or source address, failed tag, an epoch older than the peer's
  newest, or a counter already seen (4096-frame window) -> dropped and counted. A recorded
  frame (e.g. an UNLOCK) played back later is therefore useless, also after a restart.
"""
import base64
import collections
import hashlib
import json
import os
import struct
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = 0xA5
HDR = struct.Struct(">BBIIII")          # magic, type, dst, src, epoch, counter
TAG = 16
OVERHEAD = HDR.size + TAG - 2           # 32: the inner frame's own magic + type are not repeated
WINDOW = 4096


class PairingError(Exception):
    pass


def new_pairing():
    """fresh key and two distinct random 32-bit addresses."""
    key = os.urandom(32)
    while True:
        a1, a2 = struct.unpack(">II", os.urandom(8))
        if a1 != a2 and 0 < a1 < 0xFFFFFFFF and 0 < a2 < 0xFFFFFFFF:
            break
    return {"v": 2, "key": base64.b64encode(key).decode(), "n1": a1, "n2": a2,
            "created": time.strftime("%Y-%m-%d %H:%M")}


def save_pairing(p, path):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(p, f)
    os.replace(tmp, path)


def load_pairing(path):
    try:
        with open(path) as f:
            p = json.load(f)
        key = base64.b64decode(p["key"])
        if "n1" not in p and "gate" in p:          # a v6 key file works too
            p["n1"], p["n2"] = p.pop("gate"), p.pop("desk")
        if len(key) != 32 or int(p["n1"]) == int(p["n2"]):
            raise ValueError("bad key or addresses")
        p["n1"], p["n2"] = int(p["n1"]), int(p["n2"])
        return p
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as ex:
        raise PairingError(f"pairing file is damaged ({ex})")


def fingerprint(p):
    """short code both PCs show, to check they hold the same pairing (reveals nothing of the key)."""
    h = hashlib.sha256(base64.b64decode(p["key"]) + struct.pack(">II", p["n1"], p["n2"])).hexdigest()
    return f"{h[:4]}-{h[4:8]}".upper()


def addr_text(a):
    return f"{a >> 16:04X}:{a & 0xFFFF:04X}"


class LinkCrypto:
    def __init__(self, pairing, node, state_path=None):
        if node not in (1, 2):
            raise ValueError(node)
        self.aes = AESGCM(base64.b64decode(pairing["key"]))
        self.me = pairing[f"n{node}"]
        self.peer = pairing[f"n{3 - node}"]
        self.state_path = state_path
        st = self._load_state()
        self.epoch = max(int(time.time()), st.get("my_epoch", 0) + 1)
        self.ctr = 0
        self.peer_epoch = st.get("peer_epoch", 0)
        self._peer_max = 0                 # highest counter seen in peer_epoch
        self._seen = 0                     # bitmap of the WINDOW counters below _peer_max
        self.stats = collections.Counter()
        self._recent_rejects = collections.deque()
        self._save_state()

    # ------------------------------------------------------------------ state
    def _load_state(self):
        if not self.state_path:
            return {}
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_state(self):
        if not self.state_path:
            return
        try:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"my_epoch": self.epoch, "peer_epoch": self.peer_epoch}, f)
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    # ------------------------------------------------------------------ send
    def seal(self, frame):
        """plain transport frame (starts with A5, type) -> frame on the air."""
        self.ctr += 1
        if self.ctr > 0xFFFFFFFF:               # ~1 year of frames: move to a new epoch
            self.epoch = max(int(time.time()), self.epoch + 1)
            self.ctr = 1
            self._save_state()
        hdr = HDR.pack(MAGIC, frame[1], self.peer, self.me, self.epoch, self.ctr)
        nonce = struct.pack(">III", self.me, self.epoch, self.ctr)
        return hdr + self.aes.encrypt(nonce, bytes(frame[2:]), hdr)

    # ------------------------------------------------------------------ receive
    def _reject(self, why):
        self.stats[why] += 1
        self.stats["rejected"] += 1
        self._recent_rejects.append(time.monotonic())

    def recent_rejects(self, seconds=30):
        now = time.monotonic()
        while self._recent_rejects and now - self._recent_rejects[0] > seconds:
            self._recent_rejects.popleft()
        return len(self._recent_rejects)

    def open(self, wire):
        """frame on the air -> plain transport frame, or None (dropped and counted)."""
        if len(wire) < HDR.size + TAG or wire[0] != MAGIC:
            self._reject("malformed")
            return None
        _, typ, dst, src, epoch, ctr = HDR.unpack_from(wire)
        if src == self.me:
            self._reject("same_node")           # the other PC is set to the same node number
            return None
        if dst != self.me or src != self.peer:
            self._reject("foreign")             # another system nearby, or a wrong pairing
            return None
        if epoch < self.peer_epoch:
            self._reject("replay")
            return None
        if epoch == self.peer_epoch and ctr <= self._peer_max - WINDOW:
            self._reject("replay")
            return None
        nonce = struct.pack(">III", src, epoch, ctr)
        try:
            body = self.aes.decrypt(nonce, bytes(wire[HDR.size:]), bytes(wire[:HDR.size]))
        except Exception:
            self._reject("auth")                # wrong key or changed on the way
            return None
        # authentic: now check it is new
        if epoch > self.peer_epoch:             # the peer restarted (new epoch)
            self.peer_epoch, self._peer_max, self._seen = epoch, 0, 0
            self._save_state()
        if ctr > self._peer_max:
            shift = ctr - self._peer_max
            self._seen = ((self._seen << shift) | (1 << (shift - 1))) & ((1 << WINDOW) - 1) \
                if self._peer_max else 0
            self._peer_max = ctr
        elif ctr == self._peer_max:
            self._reject("replay")
            return None
        else:
            bit = 1 << (self._peer_max - ctr - 1)
            if self._seen & bit:
                self._reject("replay")
                return None
            self._seen |= bit
        self.stats["ok"] += 1
        return bytes((MAGIC, typ)) + body
