r"""
Continuous-stream PDU framer for GNU Radio (TX side).

Replaces this chain from the original flowgraph:
    PDU to Tagged Stream -> Stream CRC32 -> Protocol Formatter -> Tagged Stream Mux
    (+ preamble/postamble Vector Sources + Stream to Tagged Stream x2) -> Tag Gate
and the first half of the Constellation Modulator (unpack bits -> Gray map ->
differential encode). Output = differentially encoded SYMBOL INDICES, followed in the
flowgraph by "Chunks to Symbols" -> RRC "Polyphase Arbitrary Resampler" -> gain.

Why symbols and not bytes: GNU Radio buffers are sized in ITEMS. One byte at a framer
output is 32 samples of airtime (BPSK, sps=4) so a 4096-byte buffer alone delays every
frame by 131k samples; a symbol is only 4 samples. Measured one-way TX latency at
1 Msps went from ~700 ms (byte framer + Constellation Modulator) to a few ms.

Why:
  1. The PlutoSDR Sink only pushes a buffer to the radio when it is FULL. With bursty
     tagged-stream TX, the last packet of a burst sits in a half-full buffer until the
     next packet arrives -> ACKs/last chunks get "stuck" -> spurious ARQ timeouts.
     This block NEVER starves: when there is no packet to send it emits idle bytes,
     so the radio transmits continuously and every packet leaves immediately.
  2. Continuous TX keeps the receiver's Symbol Sync and Costas loop locked all the
     time, so a 1024-byte preamble is no longer needed (it was ~60% of airtime).
  3. Downstream back-pressure from the PlutoSDR Sink paces this block at exactly the
     air rate - no time.sleep() pacing tricks are needed for the radio.

Frame format (bit-exact with the stock RX blocks
"Correlate Access Code - Tag Stream" + "Stream CRC32 (check, packed)"):

    [preamble 0x55..] [access code 8B] [len 2B BE] [len 2B BE] [payload] [CRC32 4B LE] [postamble]
                                        ^^^^^^^^^^^^^^^^^^^^^^ len = len(payload)+4
                                                               \_____ whitened _____/

Whitening: payload+CRC are XORed with the keystream of GNU Radio's
"Additive Scrambler" (mask 0x8A, seed 0x7F, length 7, 8 bits/byte, reset on every
packet). Without it, long runs of identical bytes (WAV silence, flat image areas,
zero padding) give the receiver's Symbol Sync / Costas loop no transitions; those
frames then fail CRC on EVERY retry and ARQ cannot recover. The RX flowgraph undoes it
with an Additive Scrambler block (same params, Reset Tag Key = packet_len) placed
between Repack Bits and Stream CRC32.

Quiet FDD (idle_silence=True with burst_preamble_len): the radio keeps sending idle symbols
for `hangover` symbols after a DATA frame (none after an ACK / beacon) (so back-to-back frames need only the short preamble),
then goes SILENT. A frame that starts after silence gets the long burst preamble so the far
receiver's AGC / Symbol Sync / Costas re-lock. Why: an E200 whose own continuous
transmitter desensitises its receiver is deaf all the time in continuous FDD (the direction
INTO that radio crawls), but only during its own short ACK bursts in quiet FDD.

Priority: frames whose first two bytes are (MAGIC, type != DATA) - i.e. ARQ ACKs and
beacons from transport.py - jump ahead of queued DATA frames. This keeps ACK latency
low even when this node is simultaneously sending a large file (full duplex).
"""
import collections
import struct
import zlib

import numpy as np
import pmt
from gnuradio import gr

DEFAULT_ACCESS_CODE = "1010110011011101101001001110001011110010100011000010000011111100"

SCRAMBLER_MASK, SCRAMBLER_SEED, SCRAMBLER_LEN = 0x8A, 0x7F, 7


def scrambler_keystream(n, mask=SCRAMBLER_MASK, seed=SCRAMBLER_SEED, reg_len=SCRAMBLER_LEN):
    """Byte keystream identical to digital.additive_scrambler_bb(mask, seed, len, 0, 8)."""
    sr, out = seed, bytearray()
    for _ in range(n):
        b = 0
        for i in range(8):
            b |= (sr & 1) << i
            newbit = bin(sr & mask).count("1") & 1
            sr = (sr >> 1) | (newbit << reg_len)
        out.append(b)
    return np.frombuffer(bytes(out), dtype=np.uint8)


# Must match transport.py
MAGIC = 0xA5
TYPE_DATA = 0x01


class blk(gr.sync_block):
    """PDU Framer (continuous). Message input 'in' (PDU of uint8), byte stream output."""

    def __init__(self, access_code=DEFAULT_ACCESS_CODE, preamble_len=4, postamble_len=2,
                 max_queue=2048, idle_seed=1234, bits_per_symbol=1, pre_diff_code=(),
                 idle_silence=False, burst_preamble_len=0, hangover=0):
        gr.sync_block.__init__(self, name="PDU Framer (continuous)",
                               in_sig=None, out_sig=[np.uint8])
        self.message_port_register_in(pmt.intern("in"))
        self.set_msg_handler(pmt.intern("in"), self._on_pdu)

        ac_int = int(access_code, 2)
        self._ac = ac_int.to_bytes(len(access_code) // 8, "big")
        self._pre = b"\x55" * int(preamble_len)
        self._post = b"\x55" * int(postamble_len)
        self._max_queue = int(max_queue)
        self._ks = scrambler_keystream(0x1000)
        self._q_hi = collections.deque()   # ACK / control
        self._q_lo = collections.deque()   # DATA
        self._cur = np.zeros(0, dtype=np.uint8)
        self._pos = 0
        # Pseudo-random idle fill: keeps plenty of symbol transitions for timing
        # recovery and a flat spectrum. A false access-code match inside idle data is
        # rejected by the duplicated length field + CRC32.
        self._k = int(bits_per_symbol)
        self._M = 1 << self._k
        # TDD mode: between frames output symbol index M, which the flowgraph's
        # Chunks to Symbols table maps to 0+0j -> the radio is SILENT when it has
        # nothing to send (no self-interference into its own receiver), while the
        # sample stream to the PlutoSDR Sink stays continuous (no stuck buffers).
        self._silence = bool(idle_silence)
        self.SILENCE = self._M
        # quiet FDD: idle symbols for `hangover` symbols after a frame, then silence; a frame
        # starting after silence is preceded by extra preamble (burst_preamble_len in total)
        extra = max(0, int(burst_preamble_len) - int(preamble_len)) if idle_silence else 0
        self._hang = int(hangover) if extra else 0
        self._idle_n = 0
        self._quiet = True              # nothing transmitted yet
        self._after_data = False        # hangover only after DATA (an ACK goes silent at once)
        self.bursts = 0
        code = list(pre_diff_code) if len(pre_diff_code) else list(range(self._M))
        self._map = np.array(code, dtype=np.uint8)
        self._weights = (1 << np.arange(self._k - 1, -1, -1)).astype(np.uint8)
        # Burst preamble: 0x55 then FIXED pseudo-random bytes. After differential encoding a long
        # 0x55 run is a periodic ++--++-- pattern on which the symbol-timing loop can settle half a
        # symbol off; random symbols have no such false lock point (simulation: frames sent alone
        # after silence decode ~80% instead of ~50%).
        if extra:
            rnd = np.random.default_rng(20260).integers(0, 256, max(0, extra - 4), dtype=np.uint8).tobytes()
            self._extra_pre = self._to_chunks((b"\x55" * 4 + rnd)[:extra])
        else:
            self._extra_pre = None
        self._diff_state = 0
        rng = np.random.default_rng(int(idle_seed))
        self._idle = rng.integers(0, self._M, 1 << 16, dtype=np.uint8)
        self._idle_pos = 0
        self.dropped = 0
        self.sent = 0
        # Keep the byte buffer after this block small so a newly queued frame does not
        # wait behind a large backlog of idle bytes (lower RTT for the ARQ).
        try:
            self.set_max_output_buffer(0, 4096)
        except Exception:
            pass

    def _to_chunks(self, b):
        """bytes -> k-bit symbol values, MSB first (same as Packed to Unpacked, MSB)."""
        bits = np.unpackbits(np.frombuffer(b, dtype=np.uint8) if isinstance(b, bytes) else b)
        if self._k == 1:
            return bits
        return (bits.reshape(-1, self._k) * self._weights).sum(axis=1).astype(np.uint8)

    # ------------------------------------------------------------------ messages
    def _on_pdu(self, msg):
        if not pmt.is_pair(msg):
            return
        vec = pmt.cdr(msg)
        if not pmt.is_u8vector(vec):
            return
        payload = bytes(pmt.u8vector_elements(vec))
        n = len(payload)
        if n == 0 or n + 4 > 0x0FFF:
            return
        ln = struct.pack(">H", n + 4)
        body = np.frombuffer(payload + struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF),
                             dtype=np.uint8) ^ self._ks[:n + 4]
        arr = self._to_chunks(np.concatenate((
            np.frombuffer(self._pre + self._ac + ln + ln, dtype=np.uint8),
            body, np.frombuffer(self._post, dtype=np.uint8))))
        is_ctrl = n >= 2 and payload[0] == MAGIC and payload[1] != TYPE_DATA
        q = self._q_hi if is_ctrl else self._q_lo
        if len(q) >= self._max_queue:
            self.dropped += 1          # ARQ will recover it
            return
        q.append(arr)

    def _next_frame(self):
        if self._q_hi:
            self._after_data = False
            return self._q_hi.popleft()
        if self._q_lo:
            self._after_data = True
            return self._q_lo.popleft()
        return None

    # ------------------------------------------------------------------ stream
    def work(self, input_items, output_items):
        out = output_items[0]
        n = len(out)
        i = 0
        while i < n:
            if self._pos >= len(self._cur):
                f = self._next_frame()
                if f is None:
                    break
                if self._extra_pre is not None and self._quiet:   # first frame after silence
                    f = np.concatenate((self._extra_pre, f))
                    self.bursts += 1
                self._quiet, self._idle_n = False, 0
                self._cur, self._pos = f, 0
                self.sent += 1
            take = min(n - i, len(self._cur) - self._pos)
            out[i:i + take] = self._cur[self._pos:self._pos + take]
            i += take
            self._pos += take
        n_data = i
        if i < n:
            take = min(n - i, 512)
            hang = min(take, max(0, self._hang - self._idle_n)) if self._silence and self._after_data else 0
            if hang:            # quiet FDD hangover: idle symbols, then silence
                idx = (self._idle_pos + np.arange(hang)) & 0xFFFF
                out[i:i + hang] = self._idle[idx]
                self._idle_pos = (self._idle_pos + hang) & 0xFFFF
                self._idle_n += hang
                n_data = i + hang
                i += hang
                take -= hang
            if take <= 0:
                pass
            elif self._silence:
                out[i:i + take] = self.SILENCE
                self._quiet = True
            else:
                # Idle fill (random symbols): keeps the radio transmitting continuously
                # so every frame leaves immediately and the RX loops stay locked.
                idx = (self._idle_pos + np.arange(take)) & 0xFFFF
                out[i:i + take] = self._idle[idx]
                n_data = i + take
            i += take
        if n_data:
            # Gray pre-diff map + differential encoding: y[n] = (x[n] + y[n-1]) mod M
            seg = self._map[out[:n_data]].astype(np.int64)
            seg[0] += self._diff_state
            enc = np.cumsum(seg) % self._M
            out[:n_data] = enc
            self._diff_state = int(enc[-1])
        return i
