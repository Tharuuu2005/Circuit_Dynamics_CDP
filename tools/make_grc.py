#!/usr/bin/env python3
"""
Generate the optimized GNU Radio Companion flowgraph (.grc) with GRC's own API, so the
file is guaranteed to load in GRC 3.10. The TX framer is embedded as an Embedded
Python Block (source copied from phy/framer_block.py).

    python tools/make_grc.py                 # -> grc/sdr_link_phy.grc  (hardware)
    python tools/make_grc.py --bpsk-only     # -> grc/sdr_link_phy_bpsk.grc (clean BPSK-only graph)
    python tools/make_grc.py --loopback-sim  # -> grc/sdr_link_phy_loopback_sim.grc
                                             #    (Pluto blocks replaced by a noisy
                                             #     channel, TX looped to RX; no GUI)
"""
import argparse
import os
import sys

from gnuradio import gr
from gnuradio.grc.core.platform import Platform
from gnuradio.grc.core.io import yaml

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def build(loopback_sim=False, bpsk_only=False):
    p = Platform(version=gr.version(),
                 version_parts=(gr.major_version(), gr.api_version(), gr.minor_version()),
                 prefs=gr.prefs())
    p.build_library()
    fg = p.make_flow_graph()
    blocks = {}

    def add(key, bid, xy, **params):
        b = fg.new_block(key)
        b.params["id"].set_value(bid)
        for k, v in params.items():
            b.params[k].set_value(str(v))
        b.states["coordinate"] = xy
        blocks[bid] = b
        b.rewrite()
        return b

    def con(a, b, ap=0, bp=0):
        A, B = blocks[a], blocks[b]
        src = [s for s in A.sources if s.key == str(ap)] or [A.sources[ap]] if isinstance(ap, int) else \
            [s for s in A.sources if s.key == ap]
        dst = [s for s in B.sinks if s.key == str(bp)] or [B.sinks[bp]] if isinstance(bp, int) else \
            [s for s in B.sinks if s.key == bp]
        fg.connect(src[0], dst[0])

    o = fg.options_block
    o.params["id"].set_value(("sdr_link_phy_bpsk" if bpsk_only else "sdr_link_phy") +
                             ("_loopback_sim" if loopback_sim else ""))
    o.params["title"].set_value("SDR Link PHY (optimized, full duplex)" +
                                (" - loopback simulation" if loopback_sim else ""))
    o.params["author"].set_value("EN2130 CDP")
    o.params["generate_options"].set_value("no_gui" if loopback_sim else "qt_gui")
    o.params["run_options"].set_value("run" if loopback_sim else "prompt")

    # ------------------------------------------------------------------ variables
    y = 10
    V = [
        ("node", "1"),
        ("duplex", "'fdd'"),
        ("f1", "3300000000"),
        ("f2", "3375000000"),
        ("use_qpsk", "False"),
        ("samp_rate", "10000000"),
        ("sps", "4"),
        ("bps", "2 if use_qpsk else 1"),
        ("constel", "digital.constellation_dqpsk().base() if use_qpsk else digital.constellation_bpsk().base()"),
        ("pre_diff", "list(constel.pre_diff_code()) if use_qpsk else []"),
        ("unmap", "[0, 1, 3, 2] if use_qpsk else [0, 1]"),
        ("access_code", "digital.packet_utils.default_access_code"),
        ("tx_freq", "f1 if node == 1 else f2"),
        ("rx_freq", "f2 if node == 1 else f1"),
        ("uri", "'ip:192.168.1.10'"),
        ("tx_port", "52001"),
        ("rx_port", "52002"),
        ("tx_atten", "10"),
        ("rf_bw", "int(min(max(1.5 * 1.35 * samp_rate / sps, 1e6), samp_rate))"),
        ("amp", "0.6 / max([abs(c) for c in constel.points()])"),
        ("rrc_tx_taps", "firdes.root_raised_cosine(sps, sps, 1.0, 0.35, 11 * sps + 1)"),
        ("rrc_rx_taps", "firdes.root_raised_cosine(1.0, samp_rate, samp_rate / sps, 0.35, 11 * sps)"),
    ]
    if bpsk_only:
        drop = {"use_qpsk", "bps", "pre_diff", "unmap"}
        V = [(k, v) for k, v in V if k not in drop]
        V = [(k, "digital.constellation_bpsk().base()") if k == "constel" else
             (k, "4000000") if k == "samp_rate" else (k, v) for k, v in V]
    for i, (k, v) in enumerate(V):
        add("variable", k, (200 + 190 * (i % 7), y + 70 * (i // 7)), value=v)
    if bpsk_only:
        add("note", "note_vars", (10, 100), note="node = 1 or 2 (node 1 TX f1 / RX f2, node 2 the opposite). duplex = 'fdd' (default: silent when idle), 'fdd-cont' (idle symbols all the time) or 'tdd'; must match app.py --duplex. f1 / f2 must match the app's F1 / F2. samp_rate 4000000 + sps 4 = --profile robust, 2000000 + sps 2 = lean, 10000000 + sps 4 = balanced")
    else:
        add("note", "note_vars", (10, 100), note="node = 1 or 2 (swaps TX/RX freq). use_qpsk: False = BPSK (robust/balanced), True = DQPSK (fast). Match --profile in app.py")
    add("note", "note_tx", (10, 260), note="TX: Socket PDU -> Framer (framing+CRC+whitening+Gray+diff-enc, emits symbols; index M = silence when idle in fdd / tdd) -> Chunks to Symbols (+0j entry) -> RRC x sps -> gain -> Pluto")
    add("note", "note_rx", (10, 520), note="RX: Pluto -> look-ahead AGC (C++ blocks) -> RRC -> Symbol Sync -> Costas -> Decoder -> Diff Dec -> Map -> Unpack -> Correlate AC -> Repack (MSB) -> Descramble -> CRC32 check -> PDU")

    # ------------------------------------------------------------------ TX
    with open(os.path.join(ROOT, "phy", "framer_block.py")) as f:
        framer_src = f.read()
    add("network_socket_pdu", "tx_socket", (10, 300), type="UDP_SERVER", host="127.0.0.1",
        port="str(tx_port)", mtu="2048")
    fr = add("epy_block", "framer", (230, 300), _source_code=framer_src)
    fr.rewrite()
    for k, v in dict(access_code="access_code", preamble_len="64 if duplex == 'tdd' else 4", postamble_len="2",
                     idle_silence="duplex != 'fdd-cont'",
                     burst_preamble_len="128 if duplex == 'fdd' else 0",
                     hangover="2048 if duplex == 'fdd' else 0",
                     max_queue="2048", idle_seed="1234",
                     bits_per_symbol="1" if bpsk_only else "bps",
                     pre_diff_code="[]" if bpsk_only else "pre_diff").items():
        fr.params[k].set_value(v)
    add("digital_chunks_to_symbols_xx", "c2s", (520, 300), in_type="byte", out_type="complex",
        symbol_table="list(constel.points()) + [0]", dimension="1", maxoutbuf="4096")
    add("interp_fir_filter_xxx", "rrc_tx", (740, 300), type="ccf", interp="sps", taps="rrc_tx_taps",
        samp_delay="0", maxoutbuf="4096")
    add("blocks_multiply_const_vxx", "tx_gain", (960, 300), type="complex", const="amp",
        maxoutbuf="4096")
    con("tx_socket", "framer", "pdus", "in")
    con("framer", "c2s"); con("c2s", "rrc_tx"); con("rrc_tx", "tx_gain")

    # ------------------------------------------------------------------ radio / channel
    if loopback_sim:
        add("blocks_throttle", "throttle", (1150, 300), type="complex", samples_per_second="samp_rate",
            maxoutbuf="4096")
        add("channels_channel_model", "channel", (1150, 420), noise_voltage="0.06",
            freq_offset="1e-4", epsilon="1.00002", taps="[1.0]", seed="3")
        con("tx_gain", "throttle"); con("throttle", "channel")
        rx_in = "channel"
    else:
        add("iio_pluto_sink", "pluto_sink", (1150, 280), uri="uri", frequency="int(tx_freq)",
            samplerate="int(samp_rate)", buffer_size="32768", cyclic="False",
            attenuation1="tx_atten", len_tag_key="''", filter_source="'Auto'", bandwidth="rf_bw")
        add("iio_pluto_source", "pluto_source", (10, 560), uri="uri", frequency="int(rx_freq)",
            samplerate="int(samp_rate)", buffer_size="32768", quadrature="True", rfdc="True",
            bbdc="True", gain1="'manual'", manual_gain1="50", len_tag_key="''",
            filter_source="'Auto'", bandwidth="rf_bw")
        con("tx_gain", "pluto_sink")
        rx_in = "pluto_source"

    # ------------------------------------------------------------------ RX
    # look-ahead AGC from C++ blocks (phy/agc_block.FastAGC):
    #   out = x delayed 256 * 0.56 / sqrt(mean |x|^2 over 512 samples)
    add("blocks_complex_to_mag_squared", "agc_pow", (200, 640), vlen="1")
    add("blocks_moving_average_xx", "agc_avg", (380, 640), type="float", length="512", scale="1.0 / 512",
        max_iter="4096", vlen="1")
    add("blocks_add_const_vxx", "agc_eps", (580, 640), type="float", const="1e-20")
    add("blocks_transcendental", "agc_sqrt", (740, 640), type="float", name="'sqrt'")
    add("blocks_float_to_complex", "agc_f2c", (900, 640), vlen="1")
    add("blocks_delay", "agc_delay", (200, 700), type="complex", delay="256", num_ports="1", vlen="1")
    add("blocks_divide_xx", "agc_div", (1060, 660), type="complex", num_inputs="2", vlen="1")
    add("blocks_multiply_const_vxx", "agc", (1060, 740), type="complex", const="0.56")
    add("fft_filter_xxx", "rrc_rx", (460, 580), type="ccc", decim="1", taps="rrc_rx_taps")
    add("digital_symbol_sync_xx", "symbol_sync", (660, 560), type="cc",
        ted_type="digital.TED_SIGNAL_TIMES_SLOPE_ML", constellation="constel", sps="sps",
        ted_gain="1.0", loop_bw="0.01", damping="1.0", max_dev="1.5", osps="1",
        resamp_type="digital.IR_MMSE_8TAP", nfilters="128", pfb_mf_taps="[]")
    add("digital_costas_loop_cc", "costas", (930, 580),
        w="0.02" if bpsk_only else "0.015 if use_qpsk else 0.02",
        order="2" if bpsk_only else "4 if use_qpsk else 2", use_snr="False")
    add("digital_constellation_decoder_cb", "decoder", (10, 760), constellation="constel")
    add("digital_diff_decoder_bb", "diff_dec", (220, 760), coding="digital.DIFF_DIFFERENTIAL",
        modulus="2" if bpsk_only else "2 ** bps")
    if not bpsk_only:
        add("digital_map_bb", "unmap_blk", (420, 760), map="unmap")
        add("blocks_unpack_k_bits_bb", "unpack", (580, 760), k="bps")
    add("digital_correlate_access_code_xx_ts", "corr", (760, 750), type="byte",
        access_code="access_code", threshold="3", tagname="'packet_len'")
    add("blocks_repack_bits_bb", "repack", (10, 900), k="1", l="8", len_tag_key="'packet_len'",
        align_output="False", endianness="gr.GR_MSB_FIRST")
    add("digital_additive_scrambler_bb", "descrambler", (240, 900), mask="0x8A", seed="0x7F",
        len="7", count="0", bits_per_byte="8", reset_tag_key="'packet_len'")
    add("digital_crc32_bb", "crc_check", (480, 900), check="True", lengthtagname="'packet_len'",
        packed="True")
    add("pdu_tagged_stream_to_pdu", "to_pdu", (680, 900), type="byte", tag="packet_len")
    add("network_socket_pdu", "rx_socket", (900, 900), type="UDP_CLIENT", host="127.0.0.1",
        port="str(rx_port)", mtu="2048")
    chain = [("agc", "rrc_rx"), ("rrc_rx", "symbol_sync"), ("symbol_sync", "costas"),
             ("costas", "decoder"), ("decoder", "diff_dec")]
    chain += [("diff_dec", "corr")] if bpsk_only else \
        [("diff_dec", "unmap_blk"), ("unmap_blk", "unpack"), ("unpack", "corr")]
    chain += [("corr", "repack"), ("repack", "descrambler"), ("descrambler", "crc_check"),
              ("crc_check", "to_pdu")]
    for a, b in chain:
        con(a, b)
    con(rx_in, "agc_pow"); con("agc_pow", "agc_avg"); con("agc_avg", "agc_eps"); con("agc_eps", "agc_sqrt")
    con("agc_sqrt", "agc_f2c"); con(rx_in, "agc_delay"); con("agc_delay", "agc_div", 0, 0)
    con("agc_f2c", "agc_div", 0, 1); con("agc_div", "agc")
    con("to_pdu", "rx_socket", "pdus", "pdus")

    if not loopback_sim:
        add("qtgui_const_sink_x", "rx_constellation", (1150, 600), type="complex",
            name="'Recovered symbols'", size="1024", update_time="0.2", autoscale="False")
        add("qtgui_freq_sink_x", "rx_spectrum", (260, 460), type="complex", name="'RX spectrum'",
            fftsize="1024", fc="rx_freq", bw="samp_rate", update_time="0.2")
        con("costas", "rx_constellation")
        con("pluto_source", "rx_spectrum")
        add("note", "note_gui", (1150, 760),
            note="QT sinks cost CPU: disable them (select + D) if GNU Radio prints U/O at 10 Msps")

    fg.rewrite()
    fg.validate()
    errs = list(fg.iter_error_messages())
    return fg, errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loopback-sim", action="store_true")
    ap.add_argument("--bpsk-only", action="store_true")
    ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()
    fg, errs = build(a.loopback_sim, a.bpsk_only)
    name = ("sdr_link_phy_bpsk" if a.bpsk_only else "sdr_link_phy") + \
        ("_loopback_sim" if a.loopback_sim else "") + ".grc"
    out = a.out or os.path.join(ROOT, "grc", name)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        yaml.dump(fg.export_data(), f)
    print("wrote", out)
    if errs:
        print("VALIDATION ERRORS:")
        for e in errs:
            print("  ", e)
        sys.exit(1)
    print("flowgraph valid")


if __name__ == "__main__":
    main()
