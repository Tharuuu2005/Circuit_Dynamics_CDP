#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: SDR Link PHY (optimized, full duplex)
# Author: EN2130 CDP
# GNU Radio version: 3.10.12.0

from PyQt5 import Qt
from gnuradio import qtgui
from gnuradio import blocks
from gnuradio import digital
from gnuradio import filter
from gnuradio.filter import firdes
from gnuradio import gr
from gnuradio.fft import window
import sys
import signal
from PyQt5 import Qt
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio import eng_notation
from gnuradio import gr, pdu
from gnuradio import iio
from gnuradio import network
import sdr_link_phy_bpsk_framer as framer  # embedded python block
import sip
import threading



class sdr_link_phy_bpsk(gr.top_block, Qt.QWidget):

    def __init__(self):
        gr.top_block.__init__(self, "SDR Link PHY (optimized, full duplex)", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("SDR Link PHY (optimized, full duplex)")
        qtgui.util.check_set_qss()
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme('gnuradio-grc'))
        except BaseException as exc:
            print(f"Qt GUI: Could not set Icon: {str(exc)}", file=sys.stderr)
        self.top_scroll_layout = Qt.QVBoxLayout()
        self.setLayout(self.top_scroll_layout)
        self.top_scroll = Qt.QScrollArea()
        self.top_scroll.setFrameStyle(Qt.QFrame.NoFrame)
        self.top_scroll_layout.addWidget(self.top_scroll)
        self.top_scroll.setWidgetResizable(True)
        self.top_widget = Qt.QWidget()
        self.top_scroll.setWidget(self.top_widget)
        self.top_layout = Qt.QVBoxLayout(self.top_widget)
        self.top_grid_layout = Qt.QGridLayout()
        self.top_layout.addLayout(self.top_grid_layout)

        self.settings = Qt.QSettings("gnuradio/flowgraphs", "sdr_link_phy_bpsk")

        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {str(exc)}", file=sys.stderr)
        self.flowgraph_started = threading.Event()

        ##################################################
        # Variables
        ##################################################
        self.sps = sps = 4
        self.samp_rate = samp_rate = 4000000
        self.node = node = 1
        self.f2 = f2 = 3375000000
        self.f1 = f1 = 3300000000
        self.constel = constel = digital.constellation_bpsk().base()
        self.uri = uri = 'ip:192.168.1.10'
        self.tx_port = tx_port = 52001
        self.tx_freq = tx_freq = f1 if node == 1 else f2
        self.tx_atten = tx_atten = 10
        self.rx_port = rx_port = 52002
        self.rx_freq = rx_freq = f2 if node == 1 else f1
        self.rrc_tx_taps = rrc_tx_taps = firdes.root_raised_cosine(sps, sps, 1.0, 0.35, 11 * sps + 1)
        self.rrc_rx_taps = rrc_rx_taps = firdes.root_raised_cosine(1.0, samp_rate, samp_rate / sps, 0.35, 11 * sps)
        self.rf_bw = rf_bw = int(min(max(1.5 * 1.35 * samp_rate / sps, 1e6), samp_rate))
        self.duplex = duplex = 'fdd'
        self.amp = amp = 0.6 / max([abs(c) for c in constel.points()])
        self.access_code = access_code = digital.packet_utils.default_access_code

        ##################################################
        # Blocks
        ##################################################

        self.tx_socket = network.socket_pdu('UDP_SERVER', '127.0.0.1', str(tx_port), 2048, False)
        self.tx_gain = blocks.multiply_const_cc(amp)
        self.tx_gain.set_max_output_buffer(4096)
        self.to_pdu = pdu.tagged_stream_to_pdu(gr.types.byte_t, 'packet_len')
        self.symbol_sync = digital.symbol_sync_cc(
            digital.TED_SIGNAL_TIMES_SLOPE_ML,
            sps,
            0.01,
            1.0,
            1.0,
            1.5,
            1,
            constel,
            digital.IR_MMSE_8TAP,
            128,
            [])
        self.rx_spectrum = qtgui.freq_sink_c(
            1024, #size
            window.WIN_BLACKMAN_hARRIS, #wintype
            rx_freq, #fc
            samp_rate, #bw
            'RX spectrum', #name
            1,
            None # parent
        )
        self.rx_spectrum.set_update_time(0.2)
        self.rx_spectrum.set_y_axis((-140), 10)
        self.rx_spectrum.set_y_label('Relative Gain', 'dB')
        self.rx_spectrum.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        self.rx_spectrum.enable_autoscale(False)
        self.rx_spectrum.enable_grid(False)
        self.rx_spectrum.set_fft_average(1.0)
        self.rx_spectrum.enable_axis_labels(True)
        self.rx_spectrum.enable_control_panel(False)
        self.rx_spectrum.set_fft_window_normalized(False)



        labels = ['', '', '', '', '',
            '', '', '', '', '']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ["blue", "red", "green", "black", "cyan",
            "magenta", "yellow", "dark red", "dark green", "dark blue"]
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]

        for i in range(1):
            if len(labels[i]) == 0:
                self.rx_spectrum.set_line_label(i, "Data {0}".format(i))
            else:
                self.rx_spectrum.set_line_label(i, labels[i])
            self.rx_spectrum.set_line_width(i, widths[i])
            self.rx_spectrum.set_line_color(i, colors[i])
            self.rx_spectrum.set_line_alpha(i, alphas[i])

        self._rx_spectrum_win = sip.wrapinstance(self.rx_spectrum.qwidget(), Qt.QWidget)
        self.top_layout.addWidget(self._rx_spectrum_win)
        self.rx_socket = network.socket_pdu('UDP_CLIENT', '127.0.0.1', str(rx_port), 2048, False)
        self.rx_constellation = qtgui.const_sink_c(
            1024, #size
            'Recovered symbols', #name
            1, #number of inputs
            None # parent
        )
        self.rx_constellation.set_update_time(0.2)
        self.rx_constellation.set_y_axis((-2), 2)
        self.rx_constellation.set_x_axis((-2), 2)
        self.rx_constellation.set_trigger_mode(qtgui.TRIG_MODE_FREE, qtgui.TRIG_SLOPE_POS, 0.0, 0, "")
        self.rx_constellation.enable_autoscale(False)
        self.rx_constellation.enable_grid(False)
        self.rx_constellation.enable_axis_labels(True)


        labels = ['', '', '', '', '',
            '', '', '', '', '']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ["blue", "red", "green", "black", "cyan",
            "magenta", "yellow", "dark red", "dark green", "dark blue"]
        styles = [0, 0, 0, 0, 0,
            0, 0, 0, 0, 0]
        markers = [0, 0, 0, 0, 0,
            0, 0, 0, 0, 0]
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]

        for i in range(1):
            if len(labels[i]) == 0:
                self.rx_constellation.set_line_label(i, "Data {0}".format(i))
            else:
                self.rx_constellation.set_line_label(i, labels[i])
            self.rx_constellation.set_line_width(i, widths[i])
            self.rx_constellation.set_line_color(i, colors[i])
            self.rx_constellation.set_line_style(i, styles[i])
            self.rx_constellation.set_line_marker(i, markers[i])
            self.rx_constellation.set_line_alpha(i, alphas[i])

        self._rx_constellation_win = sip.wrapinstance(self.rx_constellation.qwidget(), Qt.QWidget)
        self.top_layout.addWidget(self._rx_constellation_win)
        self.rrc_tx = filter.interp_fir_filter_ccf(sps, rrc_tx_taps)
        self.rrc_tx.declare_sample_delay(0)
        self.rrc_tx.set_max_output_buffer(4096)
        self.rrc_rx = filter.fft_filter_ccc(1, rrc_rx_taps, 1)
        self.rrc_rx.declare_sample_delay(0)
        self.repack = blocks.repack_bits_bb(1, 8, 'packet_len', False, gr.GR_MSB_FIRST)
        self.pluto_source = iio.fmcomms2_source_fc32(uri if uri else iio.get_pluto_uri(), [True, True], 32768)
        self.pluto_source.set_len_tag_key('')
        self.pluto_source.set_frequency(int(rx_freq))
        self.pluto_source.set_samplerate(int(samp_rate))
        self.pluto_source.set_gain_mode(0, 'manual')
        self.pluto_source.set_gain(0, 50)
        self.pluto_source.set_quadrature(True)
        self.pluto_source.set_rfdc(True)
        self.pluto_source.set_bbdc(True)
        self.pluto_source.set_filter_params('Auto', '', 0, 0)
        self.pluto_sink = iio.fmcomms2_sink_fc32(uri if uri else iio.get_pluto_uri(), [True, True], 32768, False)
        self.pluto_sink.set_len_tag_key('')
        self.pluto_sink.set_bandwidth(rf_bw)
        self.pluto_sink.set_frequency(int(tx_freq))
        self.pluto_sink.set_samplerate(int(samp_rate))
        self.pluto_sink.set_attenuation(0, tx_atten)
        self.pluto_sink.set_filter_params('Auto', '', 0, 0)
        self.framer = framer.blk(access_code=access_code, preamble_len=64 if duplex == 'tdd' else 4, postamble_len=2, max_queue=2048, idle_seed=1234, bits_per_symbol=1, pre_diff_code=[], idle_silence=duplex != 'fdd-cont', burst_preamble_len=128 if duplex == 'fdd' else 0, hangover=2048 if duplex == 'fdd' else 0)
        self.diff_dec = digital.diff_decoder_bb(2, digital.DIFF_DIFFERENTIAL)
        self.descrambler = digital.additive_scrambler_bb(0x8A, 0x7F, 7, count=0, bits_per_byte=8, reset_tag_key='packet_len')
        self.decoder = digital.constellation_decoder_cb(constel)
        self.crc_check = digital.crc32_bb(True, 'packet_len', True)
        self.costas = digital.costas_loop_cc(0.02, 2, False)
        self.corr = digital.correlate_access_code_bb_ts(access_code,
          3, 'packet_len')
        self.c2s = digital.chunks_to_symbols_bc(list(constel.points()) + [0], 1)
        self.c2s.set_max_output_buffer(4096)
        self.agc_sqrt = blocks.transcendental('sqrt', "float")
        self.agc_pow = blocks.complex_to_mag_squared(1)
        self.agc_f2c = blocks.float_to_complex(1)
        self.agc_eps = blocks.add_const_ff((1e-20))
        self.agc_div = blocks.divide_cc(1)
        self.agc_delay = blocks.delay(gr.sizeof_gr_complex*1, 256)
        self.agc_avg = blocks.moving_average_ff(512, (1.0 / 512), 4096, 1)
        self.agc = blocks.multiply_const_cc(0.56)


        ##################################################
        # Connections
        ##################################################
        self.msg_connect((self.to_pdu, 'pdus'), (self.rx_socket, 'pdus'))
        self.msg_connect((self.tx_socket, 'pdus'), (self.framer, 'in'))
        self.connect((self.agc, 0), (self.rrc_rx, 0))
        self.connect((self.agc_avg, 0), (self.agc_eps, 0))
        self.connect((self.agc_delay, 0), (self.agc_div, 0))
        self.connect((self.agc_div, 0), (self.agc, 0))
        self.connect((self.agc_eps, 0), (self.agc_sqrt, 0))
        self.connect((self.agc_f2c, 0), (self.agc_div, 1))
        self.connect((self.agc_pow, 0), (self.agc_avg, 0))
        self.connect((self.agc_sqrt, 0), (self.agc_f2c, 0))
        self.connect((self.c2s, 0), (self.rrc_tx, 0))
        self.connect((self.corr, 0), (self.repack, 0))
        self.connect((self.costas, 0), (self.decoder, 0))
        self.connect((self.costas, 0), (self.rx_constellation, 0))
        self.connect((self.crc_check, 0), (self.to_pdu, 0))
        self.connect((self.decoder, 0), (self.diff_dec, 0))
        self.connect((self.descrambler, 0), (self.crc_check, 0))
        self.connect((self.diff_dec, 0), (self.corr, 0))
        self.connect((self.framer, 0), (self.c2s, 0))
        self.connect((self.pluto_source, 0), (self.agc_delay, 0))
        self.connect((self.pluto_source, 0), (self.agc_pow, 0))
        self.connect((self.pluto_source, 0), (self.rx_spectrum, 0))
        self.connect((self.repack, 0), (self.descrambler, 0))
        self.connect((self.rrc_rx, 0), (self.symbol_sync, 0))
        self.connect((self.rrc_tx, 0), (self.tx_gain, 0))
        self.connect((self.symbol_sync, 0), (self.costas, 0))
        self.connect((self.tx_gain, 0), (self.pluto_sink, 0))


    def closeEvent(self, event):
        self.settings = Qt.QSettings("gnuradio/flowgraphs", "sdr_link_phy_bpsk")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()

        event.accept()

    def get_sps(self):
        return self.sps

    def set_sps(self, sps):
        self.sps = sps
        self.set_rf_bw(int(min(max(1.5 * 1.35 * self.samp_rate / self.sps, 1e6), self.samp_rate)))
        self.set_rrc_rx_taps(firdes.root_raised_cosine(1.0, self.samp_rate, self.samp_rate / self.sps, 0.35, 11 * self.sps))
        self.set_rrc_tx_taps(firdes.root_raised_cosine(self.sps, self.sps, 1.0, 0.35, 11 * self.sps + 1))
        self.symbol_sync.set_sps(self.sps)

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.set_rf_bw(int(min(max(1.5 * 1.35 * self.samp_rate / self.sps, 1e6), self.samp_rate)))
        self.set_rrc_rx_taps(firdes.root_raised_cosine(1.0, self.samp_rate, self.samp_rate / self.sps, 0.35, 11 * self.sps))
        self.pluto_sink.set_samplerate(int(self.samp_rate))
        self.pluto_source.set_samplerate(int(self.samp_rate))
        self.rx_spectrum.set_frequency_range(self.rx_freq, self.samp_rate)

    def get_node(self):
        return self.node

    def set_node(self, node):
        self.node = node
        self.set_rx_freq(self.f2 if self.node == 1 else self.f1)
        self.set_tx_freq(self.f1 if self.node == 1 else self.f2)

    def get_f2(self):
        return self.f2

    def set_f2(self, f2):
        self.f2 = f2
        self.set_rx_freq(self.f2 if self.node == 1 else self.f1)
        self.set_tx_freq(self.f1 if self.node == 1 else self.f2)

    def get_f1(self):
        return self.f1

    def set_f1(self, f1):
        self.f1 = f1
        self.set_rx_freq(self.f2 if self.node == 1 else self.f1)
        self.set_tx_freq(self.f1 if self.node == 1 else self.f2)

    def get_constel(self):
        return self.constel

    def set_constel(self, constel):
        self.constel = constel
        self.decoder.set_constellation(self.constel)

    def get_uri(self):
        return self.uri

    def set_uri(self, uri):
        self.uri = uri

    def get_tx_port(self):
        return self.tx_port

    def set_tx_port(self, tx_port):
        self.tx_port = tx_port

    def get_tx_freq(self):
        return self.tx_freq

    def set_tx_freq(self, tx_freq):
        self.tx_freq = tx_freq
        self.pluto_sink.set_frequency(int(self.tx_freq))

    def get_tx_atten(self):
        return self.tx_atten

    def set_tx_atten(self, tx_atten):
        self.tx_atten = tx_atten
        self.pluto_sink.set_attenuation(0,self.tx_atten)

    def get_rx_port(self):
        return self.rx_port

    def set_rx_port(self, rx_port):
        self.rx_port = rx_port

    def get_rx_freq(self):
        return self.rx_freq

    def set_rx_freq(self, rx_freq):
        self.rx_freq = rx_freq
        self.pluto_source.set_frequency(int(self.rx_freq))
        self.rx_spectrum.set_frequency_range(self.rx_freq, self.samp_rate)

    def get_rrc_tx_taps(self):
        return self.rrc_tx_taps

    def set_rrc_tx_taps(self, rrc_tx_taps):
        self.rrc_tx_taps = rrc_tx_taps
        self.rrc_tx.set_taps(self.rrc_tx_taps)

    def get_rrc_rx_taps(self):
        return self.rrc_rx_taps

    def set_rrc_rx_taps(self, rrc_rx_taps):
        self.rrc_rx_taps = rrc_rx_taps
        self.rrc_rx.set_taps(self.rrc_rx_taps)

    def get_rf_bw(self):
        return self.rf_bw

    def set_rf_bw(self, rf_bw):
        self.rf_bw = rf_bw
        self.pluto_sink.set_bandwidth(self.rf_bw)

    def get_duplex(self):
        return self.duplex

    def set_duplex(self, duplex):
        self.duplex = duplex

    def get_amp(self):
        return self.amp

    def set_amp(self, amp):
        self.amp = amp
        self.tx_gain.set_k(self.amp)

    def get_access_code(self):
        return self.access_code

    def set_access_code(self, access_code):
        self.access_code = access_code




def main(top_block_cls=sdr_link_phy_bpsk, options=None):

    qapp = Qt.QApplication(sys.argv)

    tb = top_block_cls()

    tb.start()
    tb.flowgraph_started.set()

    tb.show()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()

        Qt.QApplication.quit()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    qapp.exec_()

if __name__ == '__main__':
    main()
