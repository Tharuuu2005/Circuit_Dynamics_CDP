r"""
Fast feed-forward (look-ahead) AGC for bursty TDD reception.

Same algorithm as GNU Radio's "Feed Forward AGC" block (that block uses an
approximate magnitude; this one uses the exact |x|):
    out[i] = in[i] * reference / max(|in[i .. i+N-1]|)
but the sliding maximum is computed with the van Herk / Gil-Werman algorithm (prefix
and suffix running maxima over blocks of N samples), vectorised in numpy, so the cost
is O(1) per sample instead of O(N). GNU Radio's block with N=512 manages only ~1 Msps;
this one runs far above the 10 Msps the link needs.

Why look-ahead AGC: in TDD mode the radio is silent between bursts. A feedback AGC
(AGC2) raises its gain during the silence, so the first symbols of each burst arrive
~10x too large and kick the Symbol Sync and Costas loops off lock. Looking ahead over
N samples gives the correct gain from the first symbol of a burst.
"""
import numpy as np
from gnuradio import gr


def sliding_max(a, w):
    """m[i] = max(a[i:i+w]) for i in 0..len(a)-w, in O(len(a))."""
    n = len(a)
    nb = -(-n // w)
    pad = np.full(nb * w, -np.inf, dtype=a.dtype)
    pad[:n] = a
    blocks = pad.reshape(nb, w)
    prefix = np.maximum.accumulate(blocks, axis=1).ravel()
    suffix = np.maximum.accumulate(blocks[:, ::-1], axis=1)[:, ::-1].ravel()
    m = n - w + 1
    return np.maximum(suffix[:m], prefix[w - 1:w - 1 + m])


class blk(gr.sync_block):
    """Fast Feed-Forward AGC: gain = reference / peak envelope of the next num_samples."""

    def __init__(self, num_samples=512, reference=1.0):
        gr.sync_block.__init__(self, name="Fast Feed-Forward AGC",
                               in_sig=[np.complex64], out_sig=[np.complex64])
        self.n = int(num_samples)
        self.ref = float(reference)
        self.set_history(self.n)

    def work(self, input_items, output_items):
        x = input_items[0]
        out = output_items[0]
        k = len(out)
        env = np.abs(x)                       # magnitude envelope
        peak = sliding_max(env, self.n)[:k]
        np.maximum(peak, 1e-12, out=peak)
        out[:] = x[:k] * (self.ref / peak).astype(np.float32)
        return k


# FastAGC output level equals blk's average level on RRC-shaped BPSK (RMS ~0.56 of peak)
FAST_REFERENCE = 0.56


class FastAGC(gr.hier_block2):
    """Same look-ahead idea built only from GNU Radio C++ blocks, so it never holds the Python
    lock (the Python AGC and the Python TX framer run in the same process and compete for it;
    on a slow PC the transmitter then falls behind real time and its frames break up):
        out[n] = x[n - N/2] * reference / sqrt(mean |x|^2 over the N samples around n)"""

    def __init__(self, num_samples=512, reference=FAST_REFERENCE):
        from gnuradio import blocks
        gr.hier_block2.__init__(self, "fast_ff_agc", gr.io_signature(1, 1, gr.sizeof_gr_complex),
                                gr.io_signature(1, 1, gr.sizeof_gr_complex))
        n = int(num_samples)
        self.mag2 = blocks.complex_to_mag_squared()
        self.avg = blocks.moving_average_ff(n, 1.0 / n, 4096)
        self.eps = blocks.add_const_ff(1e-20)
        self.sqrt = blocks.transcendental("sqrt", "float")
        self.f2c = blocks.float_to_complex()
        self.delay = blocks.delay(gr.sizeof_gr_complex, n // 2)
        self.div = blocks.divide_cc()
        self.ref = blocks.multiply_const_cc(float(reference))
        self.connect(self, self.mag2, self.avg, self.eps, self.sqrt, self.f2c, (self.div, 1))
        self.connect(self, self.delay, (self.div, 0))
        self.connect(self.div, self.ref, self)
