# SDR LINK v5.3 – messenger + 4 in a Row over two ANTSDR E200s

EN2130 Communication Design Project. GNU Radio 3.10 (radioconda, Windows) BPSK modem,
Selective Repeat ARQ transport, Tkinter app with chat, photos, audio, files and a
**4 in a Row** game played over the radio link.

Built on Claude_3: the same modem blocks, framer, look-ahead AGC and transport. The main
change fixes the slow direction seen in FDD at 3.3 / 3.375 GHz (~100 KB/s one way, ~8 KB/s
the other).

```
 app.py / cli.py ─► transport.py (SR-ARQ, SACK, pacing, link quality) ─► UDP 52001 ─┐
                        ▲  UDP 52002                                                │
 phy_flowgraph.py / grc: Framer ─► Chunks→Symbols ─► RRC ─► Pluto Sink    (TX F1 on node 1)
                         Pluto Source ─► AGC ─► RRC ─► Symbol Sync ─► Costas ─► Decoder
                         ─► Diff Dec ─► Correlate AC ─► Repack ─► Descramble ─► CRC32  (RX F2)
```
Node 2 uses the opposite frequencies (TX F2, RX F1).

## 0. v5.3: warning, link drops, game delay

**Seen on air with v5.1:** both directions work (80+ KB/s), but:
* the radio line sometimes turns red: *transmitter 0.96× real time – this PC can't keep up*;
* sometimes the link drops and doesn't come back until the app is restarted;
* game moves sometimes arrive much later than the link speed suggests.

**The red warning** fired at anything under 0.97×. Short dips to 0.95× are normal Windows
timing jitter and cost nothing. It now turns red only below 0.90×, on overflows or
underflows, or when the TX queue passes 60 frames. A *steady* transmitter below 1.00×
still means the PC ↔ E200 link is too slow: use `--profile lean` on both PCs (section 1).

**Link drops that don't recover.** The radio process can stall or die (a USB / Ethernet
hiccup to the E200, Windows suspending the adapter, a gr-iio buffer error). v5.1 then sat
there with the link down. v5.3 has a **radio watchdog** in the app and in `cli.py`:
* it restarts the radio when its process stops, when it stops reporting for 10 s, or when
  the link has been down for 15 s after it had worked;
* each further try waits longer (15 s, 30 s, … 2 min; back to 15 s after a minute of link
  up), so a PC whose partner is simply switched off isn't restarted all the time;
* F1 / F2 / TX atten / RX gain changed with **Apply** are kept across restarts;
* the chat shows *radio restarted automatically: <reason>*.

**Game delay.** A move is a tiny transfer. In quiet FDD a frame sent alone after silence
is decoded less reliably (about 3 in 4) than one right behind another, and a lost move
waited for the retransmission timeout, which grows while a file is moving (up to 1–2 s).
Moves also queued behind file data. v5.3:
* chat lines and moves (≤ 480 bytes) use a **priority frame type** that the framer sends
  ahead of file data;
* each is sent **twice back to back** and resent after 1.5 round trips without an ACK;
* they never wait for a file transfer slot.

Emulated link that loses 25 % of isolated frames, with a big file going the other way:
move delay 90th percentile **1015 ms → 38 ms**, worst **2337 ms → 41 ms**.

**Transfer stability** (found while testing the above):
* the delay-based speed valve could leave the sender stuck at 30 % speed: quiet-FDD ACKs
  come every 32 KB, so at low speed they were seconds apart and the valve, which only
  recovered when an ACK arrived, never got back up. It now measures queueing from the
  *smallest* recent round trip (held-back ACKs don't count) and recovers by time;
* in quiet FDD the retransmission timeout is never shorter than the gap between ACKs, so
  frames are no longer resent while their ACK is simply not due yet.

Result through the GNU Radio modem, both directions at once (quiet FDD): forward transfer
**18–20 → 27 KB/s** of 31.2 raw at the sim rate; emulated ARQ test **88 % → 92 %** of the raw
rate (115 KB/s).

## 1. v5.1: a PC whose transmitter can't keep up

**Seen on air with v5.0** (both PCs running `cli.py bench --mb 4` at the same time):
* Node 2 → node 1: 3.8 MB at 113 KB/s.
* Node 1 → node 2: failed ("no ACK progress").
* Both receivers ran at 1.00× real time.
* **Node 1's transmit queue grew to about 420 frames** (node 2's never passed 33). That
  means node 1 got data out at only about 0.55× real time. The E200 then runs out of
  samples mid-frame, the frames break up, and node 2 can't decode them.

**Cause:** CPU. The pulse shaper on the transmit side was GNU Radio's *Polyphase
Arbitrary Resampler*, used for a plain ×4. It is the most expensive block in the radio:
8.5 Msps on the test machine. The receive AGC was a Python block, which shares the
Python lock with the Python transmit framer.

**Fix:**
* **Transmit pulse shaping:** an *Interpolating FIR Filter* with the same RRC pulse
  (51.6 Msps; in-band shape identical within 0.06 dB, same output level).
* **Receive AGC:** the same look-ahead AGC built from C++ blocks (`agc_block.FastAGC`).
  `--agc py` brings back the Python one.
* **Result:** receive + transmit together run at **3.20× real time** on the test
  machine, against **1.31×** with the Claude_3 / v5.0 code. On a laptop half as fast,
  the old code falls below real time and the new one keeps a 1.6× margin.

**New diagnostics:**
* **`python tools/speed_check.py`:** run it on each PC; you want `RESULT: OK` (1.5× or
  better).
* **The radio line** now also shows *transmitter N× real time*, underflows and the TX
  queue, and turns red if either side falls behind or the queue keeps growing.

The GRC flowgraphs use the same blocks.

### v5.2: the transmitter can't get its samples to the E200 (`--profile lean`)

**Seen on air with v5.1 on node 1:**
* receiver 1.00× real time;
* transmitter 0.3–0.45×, **even when idle** with an empty queue;
* node 2 received only 68 % of node 1's frames, and node 2 → node 1 ran at 74 KB/s.

**What it means:** the CPU keeps up (the receive chain is the heavier one). What can't keep
up is moving the transmit samples to the E200. At `robust`, each direction carries
4 Msps × 4 bytes = 16 MB/s (128 Mbit/s). Receive got all of it, transmit got about a third,
about 170–190 Mbit/s in total. That is typical of a **USB 2.0 port or a USB 2.0 Ethernet
adapter**. A real Gigabit port carries both directions easily.

**Software fix: `--profile lean`.** The same 1 Mbit/s BPSK signal on the air, made with
2 instead of 4 samples per symbol. That is 2 Msps, half the traffic between the PC and the
E200.

**Simulation results:**
* back-to-back frames 200/200 at 20, 10 and 8 dB;
* bursts 80/80 at 20 dB and 71/80 at 8 dB;
* both directions at once 97 % of the raw rate;
* speed_check 4.6× real time.

Use the same profile on both PCs.

**Hardware fix:** connect that laptop to its E200 through a Gigabit port. Windows shows
the speed under Settings → Network → Ethernet → adapter properties ("Link speed
1000/1000 Mbps"). A USB adapter should be USB 3 in a USB 3 port.

## 2. The one-slow-direction problem and quiet FDD

**A second possible cause.** In the old FDD mode each radio transmits *all the time*: when it has nothing to
send, it sends idle symbols. If one E200's own transmitter leaks into its receiver
(self-desensitisation; the README of the 2-node link already saw this on one board),
that radio is partly deaf **all the time**. Data *into* it crawls; data *out of* it is fine,
because the few ACKs that get through are enough. This reproduces the symptom: with
radio 2 losing 75 % of the frames that arrive while it transmits, the old mode gives
22 KB/s into radio 2 and 109 KB/s out of it.

**Fix: quiet FDD (`--duplex fdd`, now the default).** Both directions still work at the
same time, but a radio is **silent when it has nothing to send**:

* **Framer** (`phy/framer_block.py`):
  * After a data frame it keeps sending idle symbols for 2048 symbols (2 ms), so
    back-to-back frames still need only the 4-byte preamble.
  * Then it goes silent. After an ACK or beacon it goes silent at once.
  * A frame that starts after silence gets a 128-byte burst preamble, so the far
    receiver re-locks. The preamble is 0x55 and then fixed pseudo-random bytes: in
    simulation a long 0x55-only preamble let the timing loop settle half a symbol off,
    and only about 50 % of frames sent alone after a silence decoded.
* **Receiver ACKs less often.** It ACKs per 32 KB of data instead of every frame, plus a
  double ACK when the sender pauses. A deaf receiver then loses few incoming frames to
  its own ACKs.
* **A sender that stops hearing ACKs** while it transmits (it is the deaf one) switches
  to "burst, then listen" for 15 s, so the ACKs reach it. The app tells you when this
  happens.
* **Both directions measured.** Every beacon carries frame counters, so both apps show
  the share of frames getting through **to** and **from** the other node. A lossy
  direction automatically gets shorter frames.

**Results** (no radios; *emulated* = frame-level emulator, *modem sim* = the real GNU
Radio modem with simulated air, CPU-limited on the test machine):

| test | old continuous FDD | quiet FDD |
|---|---|---|
| emulated, radio 2 loses 75 % of frames while it transmits: into radio 2 / out of radio 2 | 22 / 109 KB/s | **103 / 117 KB/s** |
| emulated, radio 2 completely deaf while it transmits | 0 / 0 (fails) | **99 / 102 KB/s** |
| modem sim, radio 2 completely deaf (−4 dB SINR while it transmits) | fails both ways | 59 % / 44 % of raw rate |
| modem sim, no deaf radio | 97 % / 76 % | 94 % / 97 % |
| modem sim, both directions at once (raw 31 KB/s in the sim) | 29 / 29 KB/s | 29 / 28 KB/s |

On your radios the problem is partial deafness (your slow direction still did 8 KB/s),
which is the 75 % row. **This is not yet measured on the E200s.**

If one direction is still slow on air:
1. Look at "frames getting through" in the app (top bar) on both PCs.
2. Raise **TX atten** on the radio that is slow to *receive*: its own transmitter
   leaks less.
3. Lower TX atten on the other radio.
4. Separate the TX and RX antennas.
5. Move F1 / F2 further apart.
6. `--duplex tdd` is the fallback (slower: one direction at a time; 57–88 KB/s in your
   earlier TDD test).

## 3. Quick start (two PCs, one E200 each)

1. radioconda prompt, once per PC: `conda install pillow` (photo previews and
   compression; Windows radioconda already has Tk). Then run
   `python tools/speed_check.py` and look for `RESULT: OK`.
2. E200 on Ethernet, PC adapter `192.168.1.100 / 255.255.255.0`, check with
   `iio_info -u ip:192.168.1.10`.
3. Copy this folder to both PCs, then:

   | PC 1 | PC 2 |
   |---|---|
   | `python app.py --node 1 --start-phy` | `python app.py --node 2 --start-phy` |

4. Wait for the green **Link up**, then chat, send photos/audio/files and play.

**Frequencies.** Node 1 transmits on **F1** and listens on **F2**; node 2 does the opposite.
Defaults: F1 3300 MHz, F2 3375 MHz.
* To change them: edit **F1 MHz / F2 MHz** at the bottom of the app and press **Apply**
  (the radio retunes at once), or start with `--f1 3300 --f2 3375`.
* The choice is saved in `radio_settings.json`.
* **Set the same F1 / F2 on both PCs.** Keep them at least 40 MHz apart (the app warns
  below that).

**Other options** (must match on both PCs):
* `--profile robust` (default, 4 Msps, 1 Mbit/s), `lean` (2 Msps, the same 1 Mbit/s,
  half the PC ↔ E200 traffic) or `balanced` (10 Msps, 2.5 Mbit/s, needs a fast PC and a
  Gigabit link).
* `--duplex fdd` (default), `fdd-cont` (the old continuous FDD) or `tdd`.

**TX atten / RX gain** can be changed live (Apply).

**The GRC flowgraph instead of the Python radio** (for the viva):
1. Open `grc/sdr_link_phy_bpsk.grc`.
2. Set `node`, and set `duplex`, `f1` and `f2` to the same values as the app.
3. Run it (F6), then start `python app.py --node N` **without** `--start-phy`.

## 4. Using the app

* **Chat:**
  * Type and press **Enter**. A ✓ appears when the other node has the message.
  * Photos appear in the chat; click a photo or file name to open it.
  * Received files are saved in `received/`.
* **Files:** **Ctrl+I** photo, **Ctrl+U** audio, **Ctrl+O** any file. Files go lossless
  unless you tick *Compress photos*. Every file is checked with SHA-256.
* **4 in a Row** (right panel):
  * **Ctrl+N** starts a game. The player who didn't start the last game moves first.
  * Click a column, or press **1–7**. **Esc** leaves the message box first, so the
    digits drop discs instead of typing.
  * Node 1 is red, node 2 yellow. The score is kept for the session.
  * Each move is one small verified transfer, sent as a priority frame (twice, ahead of
    any file data), so it arrives in a few tens of ms. Moves carry a number, so a late or
    repeated move can't confuse the boards. A move that can't be delivered is sent again
    when the link is back.
* **Top bar:**
  * Link state and round-trip time.
  * *frames getting through* to and from the other node (shown while there is traffic).
  * *burst + listen mode* if this radio is deaf while it transmits.
* **Bottom:**
  * Transfers table.
  * The radio line: *receiver N× real time* must stay ≈1.00, otherwise the PC is too
    slow.
  * F1, F2, TX atten and RX gain, with **Apply**.
* **F5** starts or stops the radio. While it is on, the watchdog restarts it by itself if
  it stalls or the link stays down (section 0); F5 off stops that too.

**CLI** (benchmarks, demos):
```
python cli.py --node 2 --start-phy listen
python cli.py --node 1 --start-phy text "hello"
python cli.py --node 1 --start-phy send photo.png
python cli.py --node 1 --start-phy bench --mb 4      # prints KB/s and frames getting through
```
Use `bench` once in each direction (node 1 → 2, then node 2 → 1) to compare them.

**One PC, no radios:** `python tools/fake_radio.py`, then `python app.py --node 1 --same-pc`
and `python app.py --node 2 --same-pc`.

## 5. Tests (no radios needed)

| command | what it shows |
|---|---|
| `python tests/deaf_emulated_test.py` | the slow-direction problem and the fix, all three modes, both directions |
| `python tests/deaf_sim_test.py` | the same through the real GNU Radio modem (`--deaf-node 0` = no deaf radio) |
| `python tests/e2e_sim_test.py --duplex fdd` | text + files both ways at once through the modem, SHA-256 checked |
| `python tests/phy_per_test.py` | frame error rate vs SNR (BPSK, back-to-back frames) |
| `python tests/phy_burst_test.py` | frames after silence (quiet FDD and TDD) |
| `python tests/arq_emulated_test.py` | ARQ with 2 % loss + bursts, 4 MB + 1 MB at the same time |
| `python tools/speed_check.py` | this PC runs receive + transmit fast enough (want 1.5× real time or better) |
| `python tests/game_test.py` | 4 in a Row rules and protocol (reordering, duplicates, simultaneous new game) |
| `python tests/watchdog_test.py` | radio watchdog: restarts a dead / silent radio and a lost link, leaves a stopped one alone (~1 min) |

Last run on the test machine (GNU Radio 3.10.9, v5.3):
* All PASS.
* Back-to-back frames: 299/300 down to 8 dB. The one lost is the very first frame after
  the radio starts, while the AGC's averaging window fills.
* Bursts: quiet FDD 96–100 % from 20 down to 8 dB; TDD 95–98 %.
* ARQ: 92 % of the raw rate (115 KB/s of 4 MB, 112 KB/s the other way at the same time).
* Both directions at once through the modem: 94–97 % of the raw rate each.
* `e2e_sim_test.py --duplex tdd` passes most runs but now and then fails on the 2-core
  test machine (the TDD turn-taking in the simulated modem is CPU-sensitive). v5.2 does
  the same (1 of 4 runs), so it is the test box, not v5.3.
* speed_check: 3.20× real time.

## 6. Review of the original flowgraph: what changed and why

| block / setting | problem | change |
|---|---|---|
| `preamble_size` = 1024 bytes | ~60 % of airtime was preamble | 4 bytes between back-to-back frames, 128 after silence (quiet FDD), 64 in TDD |
| PDU→Tagged Stream, Stream CRC32, Protocol Formatter, Tagged Stream Mux, Vector Sources, Tag Gate | The PlutoSDR Sink pushes only **full** buffers, so the last frames and ACKs of a burst sat in a half-full buffer → spurious ARQ timeouts | One **Embedded Python framer**: framing + CRC32 + whitening + differential encoding. The sample stream never stops (silence or idle symbols between frames) |
| Constellation Modulator | Byte-domain buffers held **~700 ms** of samples at 1 Msps | Framer outputs symbol indices → Chunks to Symbols → RRC Polyphase Arb Resampler → gain, small buffers |
| *(missing)* scrambler | Long runs of identical bits (silence in WAV, flat image areas) gave the loops no transitions: all-zero frames failed 25 of 30 | Whitening in the framer; RX **Additive Scrambler** (mask 0x8A, seed 0x7F, len 7, 8 bits/byte, reset tag `packet_len`) before CRC32 |
| *(missing)* AGC | Loop gains depended on distance | **Look-ahead (feed-forward) AGC**, 512 samples, O(1) per sample (`phy/agc_block.py`) |
| PlutoSDR Source gain | – | Manual 50 dB (the AD9361's own AGC can be pulled down by the board's own transmitter) |
| Symbol Sync BW 0.045, TED gain 0.1 / Costas BW 0.0628 | Jittery / noisy | 0.01, TED gain 1.0 / 0.02 |
| Correlate Access Code threshold 0 | Differential decoding turns 1 bit error into 2 | 3 |
| Repack Bits | GRC defaults: no length tag, LSB first → CRC always fails | Length tag `packet_len`, MSB first |
| Continuous FDD idle fill | A radio deafened by its own transmitter was deaf all the time → one slow direction | **Quiet FDD** (section 2) |
| Polyphase Arbitrary Resampler (TX RRC, ×4) | Most expensive block (8.5 Msps); a slower PC's transmitter fell behind real time | **Interpolating FIR Filter**, same RRC pulse (51.6 Msps) |
| Python look-ahead AGC | Shares the Python lock with the Python framer | **Same AGC from C++ blocks** |

## 7. Transport protocol (Selective Repeat ARQ)

```
DATA : A5 | 01 | src | dst | xfer(2) | seq(4) | total(4) | txn(1) | up to 1009 bytes
ACK  : A5 | 02 | src | dst | xfer(2) | cum(4) | nrecv(4) | flags | echo_seq(4) | echo_txn | SACK bitmap
HELLO: A5 | 03 | src | FF  | 0(2)    | t_send | t_echo | echo_age | frames sent(4) | share of peer's frames received(2)
```
A transfer = `meta_len(2) | JSON {kind, name, size, sha256} | bytes`; kinds: text, image,
audio, file, game.

* **Selective Repeat with SACK.** Every ACK is a full snapshot (cumulative point plus a
  512-frame bitmap), so a lost ACK costs nothing.
* **Loss detection without timeouts.** The radio delivers frames in order: if a later
  frame is ACKed and an earlier one isn't, the earlier one is resent at once.
* **Exact RTT.** Every ACK echoes the exact transmission that triggered it.
* **Timeouts.** Adaptive RTO only for tail losses.
* **Pacing** at 98 % of the PHY rate, with a delay-based safety valve.
* **Priority and fairness.** ACKs and beacons jump ahead of queued data. Up to 4
  transfers run round-robin, so a chat line or a game move doesn't wait behind a
  photo.
* **Quiet-FDD additions:** see section 2.

## 8. Troubleshooting

| symptom | check |
|---|---|
| One direction much slower than the other | Section 2: compare *frames getting through*, raise TX atten on the radio that receives badly, separate its antennas, or try `--duplex tdd` |
| *Link up*, then *Link down* after a few seconds | The same self-deafening, stronger: as above |
| One node *Link down*, the other *One-way* | The path into the *Link down* node is broken: antennas, TX atten 0 on the other node, RX gain 40–60 |
| Link never comes up | Same profile, duplex, F1 / F2 and node numbers 1 and 2? `iio_info` works? Run `python phy/phy_flowgraph.py --node N --gui` and look for a clean 2-point constellation |
| One direction fails while *that sender's* TX queue keeps growing, or *transmitter below 1.00×* / underflows | If `tools/speed_check.py` says OK on that PC, its link to the E200 is too slow: use `--profile lean` on both PCs, or a Gigabit / USB 3 connection (section 1). Otherwise: charger, Best performance, close other programs, no `--gui` |
| *receiver below 1.00× real time* or overflows | PC too slow: plug in the charger, close other programs, don't use `--gui`, use `robust` |
| Chat shows *radio restarted automatically* now and then | The radio stalled (process stopped / no reports) or the link stayed down; the watchdog brought it back. Often: check the Ethernet cable / adapter to the E200, and set Windows *Power options → USB selective suspend* and the adapter's *Allow the computer to turn off this device* off |
| Radio line red with *transmitter* between 0.90 and 1.00× all the time | PC ↔ E200 link a bit too slow: `--profile lean` on both PCs |
| "Unable to create buffer" | Another program holds the E200 (GRC, an old window): close it |

## 9. Files

```
app.py               messenger + 4 in a Row (Tk)
connect4.py          game rules and its radio protocol
cli.py               headless send / listen / bench
transport.py         SR-ARQ transport, quiet-FDD ACK policy, listen mode, link quality
radio.py             starts / stops the radio process, health line, live RF control
config.py            profiles, duplex modes, frequencies (radio_settings.json), framing
media.py             file types, save, open, photo compression
phy/phy_flowgraph.py the radio (hardware, or --sim both nodes, --sim-deaf to test self-deafening)
phy/framer_block.py  TX framer (embedded in the .grc too)
phy/agc_block.py     look-ahead AGC (embedded in the .grc too)
grc/                 GRC flowgraphs (tools/make_grc.py regenerates them)
tools/fake_radio.py  emulated link for one PC
tools/speed_check.py can this PC run the radio (receive + transmit at once)?
tests/               see section 5
```
