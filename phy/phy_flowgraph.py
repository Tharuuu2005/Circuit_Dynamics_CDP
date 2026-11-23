#!/usr/bin/env python3
"""
Optimized full-duplex PHY for ANTSDR E200 / PlutoSDR (GNU Radio 3.10, radioconda).

Run one of these per node (in the radioconda prompt):

    python phy/phy_flowgraph.py --node 1                 # PC/radio 1
    python phy/phy_flowgraph.py --node 2                 # PC/radio 2
    python phy/phy_flowgraph.py --node 1 --gui           # + constellation / spectrum windows
    python phy/phy_flowgraph.py --sim                    # both nodes in software, no radios
    python phy/phy_flowgraph.py --sim --sim-deaf 2       # ... with node 2 deafened by its own TX

Duplex modes (--duplex, see config.py): fdd (default: both directions at once, radio silent
when idle), fdd-cont (idle symbols sent continuously), tdd (take turns).
Frequencies: node 1 TX F1 / RX F2, node 2 the opposite (--f1 / --f2 in Hz).
Live control: JSON {"tx_atten": dB, "rx_gain": dB, "f1": Hz, "f2": Hz} to UDP ctrl_port.
Health line every 2 s: "[health] {...}" (receiver speed vs real time, frames sent, queue).

UDP interface to the transport layer (per node, see config.py):
    transport -> 127.0.0.1:tx_port  (Socket PDU, UDP server)  -> radio
    radio     -> 127.0.0.1:rx_port  (Socket PDU, UDP client)  -> transport

TX:  Socket PDU -> PDU Framer (symbols) -> Chunks to Symbols -> RRC Arb Resampler -> Multiply Const -> PlutoSDR Sink
RX:  PlutoSDR Source -> AGC -> RRC -> Symbol Sync -> Costas -> Constellation Decoder
     -> Differential Decoder [-> Map -> Unpack 2 bits for DQPSK]
     -> Correlate Access Code TS -> Repack Bits (tagged) -> Additive Scrambler (descramble) -> CRC32 check -> Tagged Stream to PDU -> Socket PDU
"""
import argparse
import json
import os
import socket
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402
from gnuradio import gr, blocks, digital, analog, filter as grfilter, network, pdu  # noqa: E402

import config  # noqa: E402
import framer_block  # noqa: E402
import agc_block  # noqa: E402

LEN_TAG = "packet_len"


def make_constellation(mod):
    if mod == "bpsk":
        return digital.constellation_bpsk().base()
    if mod == "qpsk":
        # DQPSK object: points in rotational order + Gray pre-diff code. Required so that
        # differential coding removes the Costas loop's 90-degree phase ambiguity.
        return digital.constellation_dqpsk().base()
    raise ValueError(mod)


class TxChain(gr.hier_block2):
    """Message 'in' (PDUs) -> continuous complex baseband.

    Framer (framing + whitening + Gray map + differential encoding, outputs symbol
    indices) -> Chunks to Symbols -> RRC Polyphase Arbitrary Resampler (x sps) -> gain.
    Equivalent to the Constellation Modulator with Differential Encoding = Yes, but with
    ~25x less buffering latency (see framer_block.py).
    """

    def __init__(self, mod, sps, amplitude=0.6, small_buffers=True, duplex="fdd"):
        gr.hier_block2.__init__(self, "tx_chain", gr.io_signature(0, 0, 0),
                                gr.io_signature(1, 1, gr.sizeof_gr_complex))
        self.message_port_register_hier_in("in")
        const = make_constellation(mod)
        k = config.BITS_PER_SYMBOL[mod]
        pre = const.pre_diff_code() if const.apply_pre_diff_code() else []
        # tdd:      silent between frames, long preamble on every frame (as before).
        # fdd:      idle symbols for a short hangover after a frame, then silent; long
        #           preamble only on a frame that starts after silence (quiet FDD).
        # fdd-cont: idle symbols all the time, short preamble.
        if duplex == "tdd":
            fr = dict(preamble_len=config.TDD_PREAMBLE_LEN, idle_silence=True)
        elif duplex == "fdd":
            fr = dict(preamble_len=config.PREAMBLE_LEN, idle_silence=True,
                      burst_preamble_len=config.BURST_PREAMBLE_LEN, hangover=config.HANGOVER_SYMBOLS)
        else:
            fr = dict(preamble_len=config.PREAMBLE_LEN, idle_silence=False)
        pre_len = fr.pop("preamble_len")
        self.framer = framer_block.blk(config.ACCESS_CODE, pre_len, config.POSTAMBLE_LEN,
                                       bits_per_symbol=k, pre_diff_code=pre, **fr)
        # extra table entry (index M) = 0+0j = silence
        self.c2s = digital.chunks_to_symbols_bc(list(const.points()) + [0j], 1)
        # RRC pulse shaping, x sps. An interpolating FIR (polyphase, C++) instead of the
        # Polyphase Arbitrary Resampler: same pulse and level, ~6x less CPU (measured 51.6 vs
        # 8.5 Msps). With the arbitrary resampler a slower PC's transmitter fell behind real
        # time while it received at the same time: frames broke up, one direction stalled.
        taps = grfilter.firdes.root_raised_cosine(sps, sps, 1.0, config.EXCESS_BW, 11 * sps + 1)
        self.rrc = grfilter.interp_fir_filter_ccf(sps, taps)
        peak = max(abs(p) for p in const.points())
        self.amp = blocks.multiply_const_cc(amplitude / peak)
        if small_buffers:
            for b in (self.c2s, self.rrc, self.amp):
                b.set_max_output_buffer(4096)
        self.msg_connect(self, "in", self.framer, "in")
        self.connect(self.framer, self.c2s, self.rrc, self.amp, self)


class RxChain(gr.hier_block2):
    """Complex baseband -> message 'out' (PDUs that passed CRC32)."""

    def __init__(self, mod, sps, samp_rate, ac_threshold=3, agc="fast", sync_bw=0.01,
                 costas_bw=None, agc_attack=1e-2, ff_len=512):
        gr.hier_block2.__init__(self, "rx_chain",
                                gr.io_signature(1, 1, gr.sizeof_gr_complex),
                                gr.io_signature(0, 0, 0))
        self.message_port_register_hier_out("out")
        const = make_constellation(mod)
        bps = config.BITS_PER_SYMBOL[mod]

        # Software AGC: normalises amplitude so loop gains of Symbol Sync / Costas don't
        # depend on distance and RX gain (the original graph had no AGC). Feed-forward
        # (look-ahead) AGC is used because a feedback AGC2 raises its gain during the
        # silence between TDD bursts and then kicks the loops off-lock at the start of
        # each burst: measured 87% -> 99.4% frames OK on bursty traffic.
        if agc == "fast":
            # C++ blocks only (agc_block.FastAGC): never holds the Python lock, so the Python
            # TX framer in the same process keeps up with the radio even on a slow PC.
            self.agc = agc_block.FastAGC(ff_len)
        elif agc in ("ff", "py"):
            # Feed-forward AGC: normalises by the peak over the NEXT ff_len samples, so
            # gain is correct from the first symbol of a burst (no attack transient).
            # Our numpy block: GNU Radio's feedforward_agc_cc is O(ff_len) per sample
            # and only reaches ~1 Msps with ff_len=512; this one is O(1) per sample.
            self.agc = agc_block.blk(ff_len, 1.0)
        elif agc == "ffgr":   # GNU Radio's block (slow; for comparison tests only)
            self.agc = analog.feedforward_agc_cc(ff_len, 1.0)
        else:
            self.agc = analog.agc2_cc(agc_attack, 1e-3, 1.0, 1.0)
            self.agc.set_max_gain(65536)
        ntaps = 11 * sps
        self.rrc = grfilter.fft_filter_ccc(
            1, grfilter.firdes.root_raised_cosine(1.0, samp_rate, samp_rate / sps,
                                                  config.EXCESS_BW, ntaps), 1)
        self.sync = digital.symbol_sync_cc(
            digital.TED_SIGNAL_TIMES_SLOPE_ML, sps,
            sync_bw,   # loop bandwidth (0.01 for continuous TX; was 0.045)
            1.0,       # damping
            1.0,       # TED gain (signal is AGC-normalised)
            1.5,       # max deviation
            1,         # output sps
            const,
            digital.IR_MMSE_8TAP, 128, [])
        if costas_bw is None:
            costas_bw = 0.02 if bps == 1 else 0.015
        self.costas = digital.costas_loop_cc(costas_bw, 2 if bps == 1 else 4, False)
        self.dec = digital.constellation_decoder_cb(const)
        self.diff = digital.diff_decoder_bb(2 ** bps, digital.DIFF_DIFFERENTIAL)
        chain = [self, self.agc, self.rrc, self.sync, self.costas, self.dec, self.diff]
        if bps > 1:
            pre = const.pre_diff_code()
            inv = [0] * len(pre)
            for i, v in enumerate(pre):
                inv[v] = i
            self.unmap = digital.map_bb(inv)
            self.unpack = blocks.unpack_k_bits_bb(bps)
            chain += [self.unmap, self.unpack]
        self.corr = digital.correlate_access_code_bb_ts(config.ACCESS_CODE, ac_threshold, LEN_TAG)
        self.repack = blocks.repack_bits_bb(1, 8, LEN_TAG, False, gr.GR_MSB_FIRST)
        self.descr = digital.additive_scrambler_bb(framer_block.SCRAMBLER_MASK,
                                                   framer_block.SCRAMBLER_SEED,
                                                   framer_block.SCRAMBLER_LEN, 0, 8, LEN_TAG)
        self.crc = digital.crc32_bb(True, LEN_TAG, True)
        self.to_pdu = pdu.tagged_stream_to_pdu(gr.types.byte_t, LEN_TAG)
        chain += [self.corr, self.repack, self.descr, self.crc, self.to_pdu]
        self.connect(*chain)
        self.msg_connect(self.to_pdu, "pdus", self, "out")

    def tap(self):
        """Costas output (symbols) for constellation display."""
        return self.costas


class HardwareNode(gr.top_block):
    def __init__(self, args):
        gr.top_block.__init__(self, f"sdr_link_node{args.node}", catch_exceptions=True)
        prof = config.PROFILES[args.profile]
        node = dict(config.NODES[args.node])
        node["tx_freq"], node["rx_freq"] = config.node_freqs(args.node, args.f1, args.f2)
        for k in ("uri", "tx_freq", "rx_freq", "tx_port", "rx_port"):
            v = getattr(args, k)
            if v is not None:
                node[k] = v
        sr, sps, mod = prof["samp_rate"], prof["sps"], prof["mod"]
        rf_bw = config.rf_bandwidth(sr, sps)
        self.info = dict(node=args.node, profile=args.profile, duplex=args.duplex, **node,
                         samp_rate=sr, sps=sps,
                         mod=mod, rf_bw=rf_bw, bit_rate=sr / sps * config.BITS_PER_SYMBOL[mod])
        from gnuradio import iio

        self.sock_tx = network.socket_pdu("UDP_SERVER", "127.0.0.1", str(node["tx_port"]), 2048, False)
        self.sock_rx = network.socket_pdu("UDP_CLIENT", "127.0.0.1", str(node["rx_port"]), 2048, False)
        self.tx = TxChain(mod, sps, args.amplitude, duplex=args.duplex)
        self.rx = RxChain(mod, sps, sr, args.ac_threshold, agc=args.agc)
        self.node, self.sr = args.node, sr

        self.sink = iio.fmcomms2_sink_fc32(node["uri"], [True, True], args.buffer, False)
        self.sink.set_len_tag_key("")
        self.sink.set_bandwidth(rf_bw)
        self.sink.set_frequency(int(node["tx_freq"]))
        self.sink.set_samplerate(int(sr))
        self.sink.set_attenuation(0, float(args.tx_atten))
        self.sink.set_filter_params("Auto", "", 0, 0)

        self.src = iio.fmcomms2_source_fc32(node["uri"], [True, True], args.buffer)
        # The PlutoSDR Source defaults to emitting a 'packet_len' tag on every buffer -
        # the SAME tag name the RX framing blocks use. GR 3.10.9's correlator drops
        # upstream tags so it's harmless there, but it floods Tag Debug and is fragile
        # across versions. Disable it.
        self.src.set_len_tag_key("")
        self.src.set_frequency(int(node["rx_freq"]))
        self.src.set_samplerate(int(sr))
        # Manual gain by default (the value that worked in the original flowgraph).
        # The AD9361 slow_attack AGC has peak detectors BEFORE the channel filter, so the
        # board's own continuous transmitter (50 MHz away) can drive its gain down and
        # bury the far node's signal -> one-way link. Use --rx-agc to try it anyway.
        if args.rx_agc:
            self.src.set_gain_mode(0, "slow_attack")
        else:
            self.src.set_gain_mode(0, "manual")
            self.src.set_gain(0, float(args.rx_gain))
        self.src.set_quadrature(True)
        self.src.set_rfdc(True)
        self.src.set_bbdc(True)
        self.src.set_filter_params("Auto", "", 0, 0)
        # (In GR 3.10 the source has no set_bandwidth(); its analog filter follows the
        #  sample rate via the 'Auto' filter configuration.)

        self.msg_connect(self.sock_tx, "pdus", self.tx, "in")
        self.connect(self.tx, self.sink)
        self.connect(self.src, self.rx)
        self.msg_connect(self.rx, "out", self.sock_rx, "pdus")
        self._gui_src = (self.src, self.rx.tap(), sr, node["rx_freq"])
        # receiver speed: samples/s actually processed (must stay ~ the sample rate)
        self.probe = blocks.probe_rate(gr.sizeof_gr_complex, 1000.0, 0.3)
        self.connect(self.src, self.probe)
        # transmitter speed: samples/s the TX chain hands to the radio (below ~1.0 = this PC
        # can't keep up, the radio runs dry mid-frame and the far end can't decode)
        self.tx_probe = blocks.probe_rate(gr.sizeof_gr_complex, 1000.0, 0.3)
        self.connect(self.tx, self.tx_probe)
        self.rf = dict(tx_atten=float(args.tx_atten), rx_gain=float(args.rx_gain),
                       f1=int(args.f1), f2=int(args.f2), rx_agc=bool(args.rx_agc))

    def apply_rf(self, req):
        """live changes from the app: TX attenuation, RX gain, frequencies."""
        if "tx_atten" in req:
            self.rf["tx_atten"] = max(0.0, min(89.0, float(req["tx_atten"])))
            self.sink.set_attenuation(0, self.rf["tx_atten"])
        if "rx_gain" in req and not self.rf["rx_agc"]:
            self.rf["rx_gain"] = max(0.0, min(71.0, float(req["rx_gain"])))
            self.src.set_gain(0, self.rf["rx_gain"])
        if "f1" in req or "f2" in req:
            f1, f2 = int(req.get("f1", self.rf["f1"])), int(req.get("f2", self.rf["f2"]))
            err, _ = config.check_freqs(f1, f2)
            if err:
                raise ValueError(err)
            tx, rx = config.node_freqs(self.node, f1, f2)
            self.sink.set_frequency(int(tx))
            self.src.set_frequency(int(rx))
            self.rf.update(f1=f1, f2=f2)
        return dict(self.rf)

    def health(self):
        f = self.tx.framer
        return {"speed": round(self.probe.rate() / self.sr, 3), "tx_speed": round(self.tx_probe.rate() / self.sr, 3),
                "tx_frames": f.sent,
                "bursts": f.bursts, "queue": len(f._q_hi) + len(f._q_lo), "dropped": f.dropped}


class SimNodes(gr.top_block):
    """Two complete nodes connected through AWGN + CFO + timing-offset channels."""

    def __init__(self, args):
        gr.top_block.__init__(self, "sdr_link_sim", catch_exceptions=True)
        from gnuradio import channels
        prof = config.PROFILES[args.profile]
        sr = args.sim_rate or prof["samp_rate"]
        sps, mod = prof["sps"], prof["mod"]
        self.info = dict(sim=True, profile=args.profile, duplex=args.duplex, samp_rate=sr, sps=sps, mod=mod,
                         snr_db=args.snr, bit_rate=sr / sps * config.BITS_PER_SYMBOL[mod],
                         deaf_node=args.sim_deaf, deaf_db=args.sim_deaf_db)
        nv = 10 ** (-args.snr / 20.0) * args.amplitude
        self.nodes = []
        for n in (1, 2):
            c = config.NODES[n]
            tx_port = args.sim_ports[n - 1][0]
            rx_port = args.sim_ports[n - 1][1]
            s_tx = network.socket_pdu("UDP_SERVER", "127.0.0.1", str(tx_port), 2048, False)
            s_rx = network.socket_pdu("UDP_CLIENT", "127.0.0.1", str(rx_port), 2048, False)
            self.nodes.append(dict(s_tx=s_tx, s_rx=s_rx,
                                   tx=TxChain(mod, sps, args.amplitude, duplex=args.duplex),
                                   rx=RxChain(mod, sps, sr, args.ac_threshold)))
        for a, b, cfo in ((0, 1, 1.3e-4), (1, 0, -0.9e-4)):
            A, B = self.nodes[a], self.nodes[b]
            ch = channels.channel_model(noise_voltage=nv, frequency_offset=cfo,
                                        epsilon=1.0 + 20e-6, taps=[1.0 + 0j], noise_seed=a + 7)
            thr = blocks.throttle(gr.sizeof_gr_complex, sr, True)
            for blk_ in (thr, ch):
                blk_.set_max_output_buffer(4096)
            rot = blocks.multiply_const_cc(np.exp(1j * (0.7 + a)))  # arbitrary static phase
            rot.set_max_output_buffer(4096)
            self.msg_connect(A["s_tx"], "pdus", A["tx"], "in")
            self.connect(A["tx"], thr, rot, ch)
            if args.sim_deaf == b + 1:
                # Self-desensitisation of node b+1: while ITS transmitter is on, strong noise
                # (sim_deaf_db above the wanted signal) is added to its receiver.
                tx_b = self.nodes[b]["tx"]
                env = blocks.complex_to_mag(1)
                pw = blocks.multiply_const_ff(10 ** (args.sim_deaf_db / 20.0) / args.amplitude)
                f2c = blocks.float_to_complex(1)
                nz = analog.noise_source_c(analog.GR_GAUSSIAN, 1.0, 77)
                mul = blocks.multiply_cc(1)
                add = blocks.add_cc(1)
                thr_b = blocks.throttle(gr.sizeof_gr_complex, sr, True)
                for blk_ in (env, pw, f2c, mul, add, thr_b):
                    blk_.set_max_output_buffer(4096)
                self.connect(tx_b, thr_b, env, pw, f2c, (mul, 0))
                self.connect(nz, (mul, 1))
                self.connect(ch, (add, 0))
                self.connect(mul, (add, 1))
                self.connect(add, B["rx"])
                self._deaf_keep = (env, pw, f2c, nz, mul, add, thr_b)
            else:
                self.connect(ch, B["rx"])
            self.msg_connect(B["rx"], "out", B["s_rx"], "pdus")
            A["thr"] = thr
        self._gui_src = None


def run_gui(tb):
    from PyQt5 import Qt
    from gnuradio import qtgui
    import sip
    app = Qt.QApplication(sys.argv)
    src, sym, sr, fc = tb._gui_src
    const = qtgui.const_sink_c(1024, "Recovered symbols (after Costas)", 1, None)
    const.set_update_time(0.2)
    freq = qtgui.freq_sink_c(1024, 5, fc, sr, "RX spectrum", 1, None)
    freq.set_update_time(0.2)
    tb.connect(sym, const)
    tb.connect(src, freq)
    w = Qt.QWidget()
    lay = Qt.QHBoxLayout(w)
    lay.addWidget(sip.wrapinstance(const.qwidget(), Qt.QWidget))
    lay.addWidget(sip.wrapinstance(freq.qwidget(), Qt.QWidget))
    w.setWindowTitle(f"SDR link - node {tb.info.get('node')}")
    w.resize(1100, 450)
    w.show()
    tb.start()
    app.exec_()
    tb.stop()
    tb.wait()


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--node", type=int, choices=(1, 2), default=1)
    p.add_argument("--profile", choices=list(config.PROFILES), default=config.DEFAULT_PROFILE)
    p.add_argument("--duplex", choices=config.DUPLEX_MODES, default=config.DEFAULT_DUPLEX,
                   help="fdd (default): both ways at once, silent when idle; fdd-cont: idle symbols "
                        "all the time; tdd: take turns")
    p.add_argument("--f1", type=float, default=None, help="node 1 TX / node 2 RX frequency, Hz")
    p.add_argument("--f2", type=float, default=None, help="node 2 TX / node 1 RX frequency, Hz")
    p.add_argument("--uri", default=None, help="override IIO URI, e.g. ip:192.168.2.10")
    p.add_argument("--tx-freq", dest="tx_freq", type=float, default=None)
    p.add_argument("--rx-freq", dest="rx_freq", type=float, default=None)
    p.add_argument("--tx-port", dest="tx_port", type=int, default=None)
    p.add_argument("--rx-port", dest="rx_port", type=int, default=None)
    p.add_argument("--ctrl-port", dest="ctrl_port", type=int, default=None)
    p.add_argument("--tx-atten", type=float, default=10.0, help="TX attenuation dB (0..89)")
    p.add_argument("--rx-gain", type=float, default=50.0,
                   help="manual RX gain in dB (0..71), default 50")
    p.add_argument("--rx-agc", action="store_true",
                   help="use the AD9361 slow_attack AGC instead of manual gain")
    p.add_argument("--buffer", type=int, default=32768, help="IIO buffer size (samples)")
    p.add_argument("--amplitude", type=float, default=0.6, help="TX digital amplitude (peak)")
    p.add_argument("--ac-threshold", type=int, default=3, help="access-code bit errors allowed")
    p.add_argument("--agc", choices=("fast", "py"), default="fast",
                   help="fast: C++ look-ahead AGC (default); py: the Python one of Claude_3")
    p.add_argument("--gui", action="store_true", help="show constellation + spectrum (costs CPU)")
    p.add_argument("--sim", action="store_true", help="simulate both nodes, no hardware")
    p.add_argument("--snr", type=float, default=20.0, help="sim: SNR in dB")
    p.add_argument("--sim-rate", type=float, default=None, help="sim: override sample rate")
    p.add_argument("--sim-deaf", type=int, default=0, choices=(0, 1, 2),
                   help="sim: this node's receiver is swamped while its own transmitter is on")
    p.add_argument("--sim-deaf-db", type=float, default=10.0,
                   help="sim: self-interference level above the wanted signal, dB")
    a = p.parse_args(argv)
    saved = config.load_freqs()
    a.f1 = int(a.f1 or saved[0])
    a.f2 = int(a.f2 or saved[1])
    a.sim_ports = [(52001, 52002), (52011, 52012)]
    return a


def main(argv=None):
    args = parse_args(argv)
    tb = SimNodes(args) if args.sim else HardwareNode(args)
    print("PHY config:", tb.info, flush=True)

    def stop(*_):
        tb.stop()
        tb.wait()
        sys.exit(0)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    if args.gui and not args.sim:
        run_gui(tb)
        return
    tb.start()
    print("PHY running. Ctrl+C to stop.", flush=True)
    if args.sim:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop()
    ctrl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        ctrl.bind(("127.0.0.1", args.ctrl_port or config.NODES[args.node]["ctrl_port"]))
        ctrl.settimeout(0.5)
    except OSError as ex:
        print(f"live RF control unavailable: {ex}", flush=True)
        ctrl = None
    t_next = time.monotonic() + 2.0
    try:
        while True:
            if ctrl is None:
                time.sleep(0.5)
            else:
                try:
                    msg = ctrl.recv(4096)
                    print("[rf] " + json.dumps(tb.apply_rf(json.loads(msg.decode()))), flush=True)
                except socket.timeout:
                    pass
                except Exception as ex:
                    print(f"[rf] rejected: {ex}", flush=True)
            if time.monotonic() >= t_next:
                t_next += 2.0
                try:
                    print("[health] " + json.dumps(tb.health()), flush=True)
                except Exception as ex:          # diagnostics never stop the radio
                    print(f"[health] unavailable: {ex}", flush=True)
    except KeyboardInterrupt:
        stop()


if __name__ == "__main__":
    main()
